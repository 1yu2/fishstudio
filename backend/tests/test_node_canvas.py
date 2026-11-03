"""节点式画布：图校验、Copilot 拆镜头，以及真实 PG 上的画布、自动保存、节点生成、上传与结果汇总。"""

import asyncio
import os
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from app.services import node_canvas_service as service

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
requires_pg = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL 未设置，跳过真实 PG 画布测试"
)


def _node(node_id, kind="text", **data):
    return {"id": node_id, "type": kind, "position": {"x": 0, "y": 0}, "data": data}


# ── Copilot ──────────────────────────────────────────────────────────────


def test_copilot_split_repairs_invalid_output(monkeypatch):
    replies = iter(
        [
            "不是 JSON",
            '{"characters": [{"name": "林夏", "description": "短发"}], "shots": [{"title": "推门", "prompt": "雨夜推门", "characters": ["林夏"], "duration_s": 4}]}',
        ]
    )
    calls = []

    async def fake_llm(messages):
        calls.append(messages)
        return next(replies)

    monkeypatch.setattr(service, "_call_llm", fake_llm)
    result = asyncio.run(
        service.copilot_split(SimpleNamespace(aspect_ratio="9:16"), idea="雨夜便利店", shots=3)
    )

    assert result.characters[0].name == "林夏"
    assert result.shots[0].duration_s == 4
    assert len(calls) == 2 and "不符合要求" in calls[1][-1]["content"]


def test_copilot_split_gives_up_after_one_repair(monkeypatch):
    async def fake_llm(messages):
        return "{}"

    monkeypatch.setattr(service, "_call_llm", fake_llm)
    with pytest.raises(service.CanvasError, match="无法识别"):
        asyncio.run(service.copilot_split(SimpleNamespace(aspect_ratio="9:16"), idea="x", shots=2))


# ── 真实 PostgreSQL ──────────────────────────────────────────────────────


def _admin_dsn() -> dict:
    parsed = urlparse(TEST_DATABASE_URL.replace("postgresql+asyncpg", "postgresql"))
    return {
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
    }


async def _recreate_database(drop_only: bool = False) -> None:
    import asyncpg

    db = urlparse(TEST_DATABASE_URL.replace("postgresql+asyncpg", "postgresql")).path.lstrip("/")
    conn = await asyncpg.connect(database="postgres", **_admin_dsn())
    try:
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid <> pg_backend_pid()",
            db,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db}"')
        if not drop_only:
            await conn.execute(f'CREATE DATABASE "{db}"')
    finally:
        await conn.close()


@pytest.fixture
def pg(tmp_path, monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL 未设置")
    monkeypatch.setattr(service, "STORAGE_DIR", tmp_path)
    asyncio.run(_recreate_database())
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    import app.db.migrations as migrations

    mp = pytest.MonkeyPatch()
    mig_engine = create_async_engine(TEST_DATABASE_URL)
    mp.setattr(migrations, "engine", mig_engine)
    mp.setattr(migrations.get_settings(), "database_url", TEST_DATABASE_URL)
    asyncio.run(migrations.bootstrap_and_upgrade())
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())
    asyncio.run(mig_engine.dispose())
    mp.undo()
    asyncio.run(_recreate_database(drop_only=True))


async def _user(session, name="canvas-user", admin=False):
    from app.db.models import User

    user = User(username=name, password_hash="x", is_admin=admin)
    session.add(user)
    await session.flush()
    return user


@requires_pg
def test_canvas_lifecycle_and_autosave(pg):
    from app.db.models import SeriesProject

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            other = await _user(s, "other")
            canvas = await service.create_canvas(
                s, owner, title="", aspect_ratio="9:16", idea="雨夜便利店的故事"
            )
            project = await s.get(SeriesProject, canvas.project_id)
            first_node = canvas.graph["nodes"][0]
            graph = {
                "nodes": [first_node, _node("img-1", "image", prompt="角色")],
                "edges": [
                    {"id": "e1", "source": first_node["id"], "target": "img-1", "targetHandle": "prompt"},
                    {"id": "dangling", "source": "missing", "target": "img-1"},
                ],
            }
            await service.save_graph(s, canvas, graph, revision=1)
            with pytest.raises(service.CanvasConflict):
                await service.save_graph(s, canvas, graph, revision=1)
            with pytest.raises(service.CanvasError, match="节点 id 重复"):
                await service.save_graph(
                    s, canvas, {"nodes": [_node("a"), _node("a")], "edges": []}, revision=2
                )
            with pytest.raises(LookupError):
                await service.get_canvas(s, canvas.id, other)
            with pytest.raises(service.CanvasError, match="画幅"):
                await service.create_canvas(s, owner, title="x", aspect_ratio="2:1")
            listed = await service.list_canvases(s, owner)
            return canvas, project, listed, await service.list_canvases(s, other)

    canvas, project, listed, others = asyncio.run(_run())

    assert canvas.title == "雨夜便利店的故事"
    assert project.project_type == "node_canvas"
    assert canvas.revision == 2
    assert [edge["id"] for edge in canvas.graph["edges"]] == ["e1"]  # 悬空连线被丢弃
    assert [item.id for item in listed] == [canvas.id] and others == []


@requires_pg
def test_run_nodes_uploads_and_results(pg, monkeypatch):
    from app.db.models import Artifact, GenerationJob

    async def fake_plan(session, **kwargs):
        fake_plan.calls.append(kwargs)
        return {
            "provider": "ark",
            "model": "seedance",
            "resolution": "480p",
            "protocol": "ark_v3",
            "billable_duration": kwargs["duration"],
            "estimated_total_cost": Decimal("0.72"),
            "price_snapshot": {},
            "deployment_name": "ark-seedance",
            "route": {"tier": kwargs["tier"]},
        }

    fake_plan.calls = []
    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", fake_plan)

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await service.create_canvas(s, owner, title="t")
            other = await service.create_canvas(s, owner, title="other")
            await service.save_graph(
                s,
                canvas,
                {"nodes": [_node("img"), _node("vid", "video"), _node("up", "image")], "edges": []},
                revision=1,
            )
            uploaded = await service.upload(
                s, canvas, node_id="up", filename="face.png", content_type="image/png", data=b"png"
            )
            foreign = await service.upload(
                s, other, node_id="x", filename="x.png", content_type="image/png", data=b"png"
            )
            with pytest.raises(service.CanvasError, match="只支持"):
                await service.upload(
                    s, canvas, node_id="up", filename="a.gif", content_type="image/gif", data=b"gif"
                )

            image_job = await service.run_node(
                s,
                canvas,
                "img",
                service.NodeRunRequest(
                    kind="image",
                    prompt="林夏定妆",
                    model="ark-seedream",
                    count=2,
                    reference_artifact_ids=[uploaded.id],
                ),
            )
            video_job = await service.run_node(
                s,
                canvas,
                "vid",
                service.NodeRunRequest(
                    kind="video",
                    prompt="推门",
                    tier="preview",
                    duration=5,
                    first_frame_artifact_id=uploaded.id,
                    last_frame_artifact_id=uploaded.id,
                ),
            )
            with pytest.raises(service.CanvasError, match="不属于"):
                await service.run_node(
                    s,
                    canvas,
                    "img",
                    service.NodeRunRequest(kind="image", prompt="x", reference_artifact_ids=[foreign.id]),
                )
            with pytest.raises(service.CanvasError, match="节点不存在"):
                await service.run_node(s, canvas, "nope", service.NodeRunRequest(kind="image", prompt="x"))
            with pytest.raises(service.CanvasError, match="需要提示词"):
                await service.run_node(s, canvas, "img", service.NodeRunRequest(kind="image", prompt=" "))

            # 模拟 Worker 完成图片任务并产出两张图
            image_job.status = "succeeded"
            for index in range(2):
                s.add(
                    Artifact(
                        project_id=canvas.project_id,
                        job_id=image_job.id,
                        type="image",
                        storage_key=f"images/r{index}.png",
                    )
                )
            await s.flush()
            runs = await service.node_runs(s, canvas)
            quote = await service.quote_node(
                s, canvas, service.NodeRunRequest(kind="video", prompt="推门", tier="final")
            )
            stored_video = await s.get(GenerationJob, video_job.id)
            return image_job, stored_video, uploaded, runs, quote

    image_job, video_job, uploaded, runs, quote = asyncio.run(_run())

    assert image_job.operation == "generate_image" and image_job.shot_id is None
    assert image_job.params["deployment"] == "ark-seedream"
    assert image_job.params["source_image"] == uploaded.storage_key
    assert image_job.params["candidate_count"] == 2 and image_job.params["canvas_node_id"] == "img"
    assert video_job.params["mode"] == "first_last_frame"
    assert video_job.params["video_tier"] == "preview"
    assert video_job.params["routing_snapshot"]["deployment_name"] == "ark-seedance"
    assert fake_plan.calls[0]["mode"] == "first_last_frame" and fake_plan.calls[0]["tier"] == "preview"
    assert [job["status"] for job in runs["nodes"]["img"]["jobs"]] == ["succeeded"]
    assert len(runs["nodes"]["img"]["results"]) == 2
    assert runs["nodes"]["vid"]["jobs"][0]["status"] == "queued"  # 还没被 Worker 领取
    assert runs["nodes"]["up"]["results"][0]["uploaded"] is True
    assert runs["active_jobs"] == 1
    assert quote == {
        "kind": "video",
        "mode": "text",
        "model": "ark-seedance",
        "resolution": "480p",
        "estimated_cost": 0.72,
    }


def test_display_status_follows_remote_state_between_polls():
    from types import SimpleNamespace as Job

    assert service._display_status(Job(status="pending", external_task_id="", result={})) == ("queued", 0)
    remote_queued = Job(
        status="pending", external_task_id="t1", result={"remote_status": "queued", "queue_position": 2}
    )
    assert service._display_status(remote_queued) == ("queued", 2)
    assert service._display_status(
        Job(status="pending", external_task_id="t1", result={"remote_status": "running"})
    ) == ("running", 0)
    assert service._display_status(Job(status="running", external_task_id="t1", result={})) == ("running", 0)
    running_but_queued_remotely = Job(
        status="running", external_task_id="t1", result={"remote_status": "queued", "queue_position": 1}
    )
    assert service._display_status(running_but_queued_remotely) == ("queued", 1)
    assert service._display_status(Job(status="leased", external_task_id="", result={})) == ("leased", 0)
    assert service._display_status(
        Job(status="succeeded", external_task_id="t1", result={"queue_position": 3})
    ) == ("succeeded", 0)


def test_worker_records_remote_progress_and_queue_position():
    from types import SimpleNamespace as Job

    from app.worker.generation_worker import _record_remote_progress

    job = Job(progress=10, result={"keep": 1})
    _record_remote_progress(job, {"status": "running", "progress": 37})
    assert job.progress == 37 and job.result == {"keep": 1, "remote_status": "running", "queue_position": 0}
    _record_remote_progress(job, {"status": "running", "progress": 20})
    assert job.progress == 37  # 进度只增不减
    _record_remote_progress(job, {"status": "queued", "queue_position": 1})
    assert job.result["queue_position"] == 1 and job.progress == 37
    _record_remote_progress(job, {"status": "running", "progress": 100})
    assert job.progress == 99  # 100 留给真正完成


def test_daily_cap_configuration(monkeypatch):
    from datetime import datetime, timezone

    from app.services import budget_service

    monkeypatch.delenv("DAILY_BUDGET_CNY", raising=False)
    assert budget_service.daily_cap() == Decimal("20")
    monkeypatch.setenv("DAILY_BUDGET_CNY", "0")
    assert budget_service.daily_cap() is None
    monkeypatch.setenv("DAILY_BUDGET_CNY", "35.5")
    assert budget_service.daily_cap() == Decimal("35.5")
    monkeypatch.setenv("BUDGET_TIMEZONE", "Asia/Shanghai")
    start = budget_service.day_start(datetime(2026, 10, 19, 17, 30, tzinfo=timezone.utc))
    assert start.isoformat() == "2026-10-20T00:00:00+08:00"  # 北京时间已是第二天
    assert budget_service.is_cloud("ark") and not budget_service.is_cloud("local_h3")


@requires_pg
def test_daily_cap_blocks_cloud_runs_but_not_local(pg, monkeypatch):
    from app.db.models import UsageEvent

    monkeypatch.setenv("DAILY_BUDGET_CNY", "1")
    plans = {"provider": "ark"}

    async def fake_plan(session, **kwargs):
        return {
            "provider": plans["provider"],
            "model": "m",
            "resolution": "480p",
            "billable_duration": 4,
            "estimated_total_cost": Decimal("0.40"),
            "price_snapshot": {},
            "deployment_name": "d",
            "route": {},
        }

    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", fake_plan)
    req = service.NodeRunRequest(kind="video", prompt="推门", duration=4)

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await service.create_canvas(s, owner, title="t")
            await service.save_graph(s, canvas, {"nodes": [_node("vid", "video")], "edges": []}, revision=1)
            s.add(
                UsageEvent(
                    user_id=owner.id,
                    operation="generate_video",
                    provider="ark",
                    status="success",
                    actual_cost=Decimal("0.30"),
                )
            )
            s.add(
                UsageEvent(
                    user_id=owner.id,
                    operation="generate_video",
                    provider="local_h3",
                    status="success",
                    actual_cost=Decimal("5"),
                )
            )
            await s.flush()
            first = await service.run_node(s, canvas, "vid", req)  # 0.30 + 0.40 ≤ 1
            with pytest.raises(service.CanvasError, match="超出今日云端花费上限"):
                await service.run_node(s, canvas, "vid", req)  # 0.30 + 0.40 进行中 + 0.40 > 1
            plans["provider"] = "local_h3"
            local = await service.run_node(s, canvas, "vid", req)  # 本地不计费、不受上限限制
            runs = await service.node_runs(s, canvas)
            return first, local, runs["budget"]

    first, local, budget = asyncio.run(_run())

    assert first.params["budget_estimate"] == "0.40"
    assert local.params["budget_estimate"] == "0"
    assert budget == {"cap": 1.0, "spent": 0.3, "in_flight": 0.4, "remaining": 0.3}


@requires_pg
def test_polled_jobs_stay_running_and_bypass_concurrency_limits(pg):
    from datetime import datetime, timedelta, timezone

    from app.db.models import GenerationJob
    from app.services import job_service

    past = datetime.now(timezone.utc) - timedelta(seconds=5)

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await service.create_canvas(s, owner, title="t")

            def job(key, **values):
                row = GenerationJob(
                    operation="generate_video",
                    project_id=canvas.project_id,
                    idempotency_key=key,
                    provider="local_h3",
                    model="MiniMax-H3",
                    worker_class="video",
                    **values,
                )
                s.add(row)
                return row

            polling = [
                job(f"poll-{i}", status="running", external_task_id=f"t{i}", leased_by="", lease_until=past)
                for i in range(5)
            ]
            fresh = job("fresh", status="pending")
            crashed = job(
                "crashed", status="running", external_task_id="tc", leased_by="dead", lease_until=past
            )
            unsubmitted = job("unsubmitted", status="leased", leased_by="dead", lease_until=past)
            await s.flush()

            recovered = await job_service.recover_expired_leases(s)
            leased = await job_service.lease_jobs(s, limit=20, worker_id="w1", max_project_active=5)
            return polling, fresh, crashed, unsubmitted, recovered, {j.id for j in leased}

    polling, fresh, crashed, unsubmitted, recovered, leased_ids = asyncio.run(_run())

    assert recovered == 2
    assert crashed.status == "running" and crashed.id in leased_ids  # 已提交的崩溃任务直接继续轮询
    assert unsubmitted.status == "pending"  # 未提交的回到队列
    for row in polling:
        assert row.id in leased_ids and row.status == "running" and row.leased_by == "w1"
    # 项目里已有 6 个在跑（5 个轮询 + 1 个崩溃恢复），新任务受并发上限限制，不被领取
    assert fresh.id not in leased_ids and fresh.status == "pending"
    assert unsubmitted.id not in leased_ids


# ── 画布行为反馈（Eval L3）────────────────────────────────────────────────


class _FakeSession:
    """不连库的最小会话：记录 add 的对象，get 从 artifacts 里取。"""

    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def get(self, model, key):
        return self.artifacts.get(key)

    def begin_nested(self):
        class _Ctx:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()


def _fake_canvas(selected=None):
    graph = {"nodes": [_node("img", "image", **({"selected": selected} if selected else {}))], "edges": []}
    return SimpleNamespace(id="c1", project_id="p1", owner_id=7, graph=graph, revision=1)


def _save(session, canvas, selected):
    graph = {"nodes": [_node("img", "image", selected=selected)], "edges": []}
    return asyncio.run(service.save_graph(session, canvas, graph, canvas.revision, user_id=7))


def test_save_graph_records_selected_only_on_change():
    session = _FakeSession(
        {"A": SimpleNamespace(id="A", project_id="p1"), "B": SimpleNamespace(id="B", project_id="p1")}
    )
    canvas = _fake_canvas()
    _save(session, canvas, "A")
    _save(session, canvas, "A")  # 没变化：不记录
    _save(session, canvas, "B")
    assert [(f.artifact_id, f.action, f.step_id, f.user_id) for f in session.added] == [
        ("A", "selected", None, 7),
        ("B", "selected", None, 7),
    ]


def test_save_graph_ignores_foreign_project_artifact():
    session = _FakeSession({"X": SimpleNamespace(id="X", project_id="other")})
    canvas = _fake_canvas()
    _save(session, canvas, "X")
    _save(session, canvas, "missing")
    assert session.added == []
    assert canvas.revision == 3  # 保存本身照常成功


def test_save_graph_survives_feedback_failure(monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.services.feedback_service.record_canvas_feedback", boom)
    session = _FakeSession({})
    canvas = _fake_canvas()
    result = _save(session, canvas, "A")
    assert result.revision == 2 and result.graph["nodes"][0]["data"]["selected"] == "A"


@requires_pg
def test_canvas_feedback_regenerate_replace_and_stats(pg, monkeypatch):
    from sqlalchemy import select

    from app.db.models import Artifact, ArtifactFeedback, GenerationJob
    from app.services import feedback_service

    async def fake_plan(session, **kwargs):
        return {}

    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", fake_plan)

    async def _feedback(s, canvas):
        rows = (
            (
                await s.execute(
                    select(ArtifactFeedback)
                    .where(ArtifactFeedback.project_id == canvas.project_id)
                    .order_by(ArtifactFeedback.created_at, ArtifactFeedback.id)
                )
            )
            .scalars()
            .all()
        )
        return [(f.action, f.artifact_id) for f in rows]

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await service.create_canvas(s, owner, title="t")
            await service.save_graph(
                s, canvas, {"nodes": [_node("img", "image"), _node("up", "image")], "edges": []}, revision=1
            )
            req = service.NodeRunRequest(kind="image", prompt="定妆", model="ark-seedream", count=2)
            first = await service.run_node(s, canvas, "img", req)
            after_first = await _feedback(s, canvas)  # 首次运行不记录

            first.status = "succeeded"
            arts = []
            for index in range(2):
                art = Artifact(
                    project_id=canvas.project_id, job_id=first.id, type="image", storage_key=f"i/{index}.png"
                )
                s.add(art)
                arts.append(art)
            await s.flush()
            await service.save_graph(
                s,
                canvas,
                {"nodes": [_node("img", "image", selected=arts[0].id), _node("up", "image")], "edges": []},
                revision=canvas.revision,
            )
            second = await service.run_node(s, canvas, "img", req)  # 已有成功结果 -> regenerated
            await service.upload(
                s, canvas, node_id="img", filename="a.png", content_type="image/png", data=b"png"
            )  # 已有生成结果 -> replaced
            await service.upload(
                s, canvas, node_id="up", filename="b.png", content_type="image/png", data=b"png"
            )  # 无生成结果 -> 不记录
            second.status = "succeeded"
            s.add(
                Artifact(project_id=canvas.project_id, job_id=second.id, type="image", storage_key="i/2.png")
            )
            await s.flush()
            stats = await feedback_service.canvas_adoption_stats(s, canvas.project_id)
            return after_first, await _feedback(s, canvas), arts, stats

    after_first, rows, arts, stats = asyncio.run(_run())

    assert after_first == []
    assert sorted(rows) == sorted(
        [("selected", arts[0].id), ("regenerated", arts[0].id), ("replaced", arts[0].id)]
    )
    # 3 张成功产物、1 张被选中，所在节点共生成 2 次
    assert stats["total_results"] == 3 and stats["selected_count"] == 1
    assert stats["adoption_rate"] == pytest.approx(1 / 3)
    assert stats["avg_generations_per_selected"] == 2


@requires_pg
def test_canvas_feedback_db_failure_does_not_block_main_flow(pg, monkeypatch):
    """反馈写入在真实库上 flush 失败时：保存 / 运行仍成功提交，没有残留反馈行。"""
    from sqlalchemy import func, select, text

    from app.db.models import Artifact, ArtifactFeedback, GenerationJob, NodeCanvas
    from app.services import feedback_service

    real = feedback_service.record_canvas_feedback

    async def failing(session, **kwargs):
        await real(session, **kwargs)  # 先写入一行并 flush
        await session.execute(text("SELECT 1/0"))  # 再让数据库报错，整个 savepoint 应回滚

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await service.create_canvas(s, owner, title="t")
            await service.save_graph(s, canvas, {"nodes": [_node("img", "image")], "edges": []}, revision=1)
            req = service.NodeRunRequest(kind="image", prompt="定妆", model="ark-seedream", count=1)
            first = await service.run_node(s, canvas, "img", req, user_id=owner.id)
            first.status = "succeeded"
            art = Artifact(project_id=canvas.project_id, job_id=first.id, type="image", storage_key="i/0.png")
            s.add(art)
            await s.commit()
            canvas_id, art_id = canvas.id, art.id

            monkeypatch.setattr("app.services.feedback_service.record_canvas_feedback", failing)
            canvas = await s.get(NodeCanvas, canvas_id)
            await service.save_graph(
                s,
                canvas,
                {"nodes": [_node("img", "image", selected=art_id)], "edges": []},
                revision=canvas.revision,
                user_id=owner.id,
            )
            await service.run_node(s, canvas, "img", req, user_id=owner.id)  # 触发 regenerated 失败
            await service.upload(
                s, canvas, node_id="img", filename="a.png", content_type="image/png", data=b"png"
            )  # 触发 replaced 失败
            await s.commit()

        async with pg() as s:
            canvas = await s.get(NodeCanvas, canvas_id)
            jobs = (await s.execute(select(func.count()).select_from(GenerationJob))).scalar_one()
            feedback = (await s.execute(select(func.count()).select_from(ArtifactFeedback))).scalar_one()
            return canvas, jobs, feedback

    canvas, jobs, feedback = asyncio.run(_run())

    assert canvas.graph["nodes"][0]["data"]["selected"] and canvas.revision == 3
    assert jobs == 2
    assert feedback == 0
