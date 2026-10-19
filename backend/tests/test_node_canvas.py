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
    assert runs["nodes"]["vid"]["jobs"][0]["status"] == "pending"
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
