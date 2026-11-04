"""工作台总览：真实 PG 上的数据隔离、running / failed / needs_review 定义、预算与空用户。"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlparse

import pytest

from app.services import node_canvas_service as canvas_service
from app.services import workspace_overview_service as service

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
requires_pg = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL 未设置，跳过真实 PG 工作台测试"
)


async def _recreate_database(drop_only: bool = False) -> None:
    import asyncpg

    parsed = urlparse(TEST_DATABASE_URL.replace("postgresql+asyncpg", "postgresql"))
    db = parsed.path.lstrip("/")
    conn = await asyncpg.connect(
        database="postgres",
        user=parsed.username or "postgres",
        password=parsed.password or "",
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
    )
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
def pg(monkeypatch):
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL 未设置")
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


NOW = datetime.now(timezone.utc)


def _node(node_id, kind="text", **data):
    return {"id": node_id, "type": kind, "position": {"x": 0, "y": 0}, "data": data}


async def _user(session, name, admin=False):
    from app.db.models import User

    user = User(username=name, password_hash="x", is_admin=admin)
    session.add(user)
    await session.flush()
    return user


async def _canvas(session, user, title, nodes):
    canvas = await canvas_service.create_canvas(session, user, title=title)
    canvas.graph = {"nodes": nodes, "edges": []}
    await session.flush()
    return canvas


async def _job(session, canvas, node_id, status, *, age=0, operation="generate_image", **fields):
    """age 越大越早创建，用来固定“最新任务”的顺序。"""
    from app.db.models import GenerationJob

    job = GenerationJob(
        project_id=canvas.project_id,
        operation=operation,
        status=status,
        params={"canvas_id": canvas.id, "canvas_node_id": node_id},
        idempotency_key=f"t:{canvas.id}:{node_id}:{age}:{status}:{operation}",
        created_at=NOW - timedelta(minutes=age),
        **fields,
    )
    session.add(job)
    await session.flush()
    return job


async def _artifact(session, canvas, job, *, age=0, kind="image"):
    from app.db.models import Artifact

    artifact = Artifact(
        project_id=canvas.project_id,
        job_id=job.id if job else None,
        type=kind,
        storage_key=f"x/{age}-{kind}.bin",
        created_at=NOW - timedelta(minutes=age),
    )
    session.add(artifact)
    await session.flush()
    return artifact


@requires_pg
def test_empty_user_gets_empty_overview(pg, monkeypatch):
    monkeypatch.setenv("DAILY_BUDGET_CNY", "50")

    async def _run():
        async with pg() as s:
            return await service.overview(s, await _user(s, "empty"))

    result = asyncio.run(_run())
    assert result["counts"] == {"running": 0, "needs_review": 0, "finished_this_week": 0, "failed": 0}
    assert result["projects"] == result["running"] == result["needs_review"] == result["failed"] == []
    assert result["budget"] == {
        "cap": "50.00",
        "spent_today": "0.00",
        "in_flight": "0.00",
        "remaining": "50.00",
    }


@requires_pg
def test_budget_without_cap(pg, monkeypatch):
    monkeypatch.setenv("DAILY_BUDGET_CNY", "0")

    async def _run():
        async with pg() as s:
            return (await service.overview(s, await _user(s, "nocap")))["budget"]

    budget = asyncio.run(_run())
    assert budget["cap"] is None and budget["remaining"] is None


@requires_pg
def test_users_are_isolated_and_admin_sees_only_own(pg):
    async def _run():
        async with pg() as s:
            alice = await _user(s, "alice")
            admin = await _user(s, "root", admin=True)
            mine = await _canvas(s, alice, "爱丽丝的", [_node("v1", "video")])
            await _canvas(s, admin, "管理员的", [_node("v1", "video")])
            await _job(s, mine, "v1", "running", operation="generate_video", progress=30)
            return await service.overview(s, alice), await service.overview(s, admin)

    alice_view, admin_view = asyncio.run(_run())
    assert [p["title"] for p in alice_view["projects"]] == ["爱丽丝的"]
    assert alice_view["counts"]["running"] == 1
    assert [p["title"] for p in admin_view["projects"]] == ["管理员的"]
    assert admin_view["counts"]["running"] == 0 and admin_view["running"] == []


@requires_pg
def test_running_failed_and_latest_job_semantics(pg):
    async def _run():
        async with pg() as s:
            user = await _user(s, "u")
            canvas = await _canvas(
                s,
                user,
                "雨夜",
                [
                    _node("a", "image", label="镜头 A"),
                    _node("b", "video", label="镜头 B"),
                    _node("c", "image"),
                ],
            )
            # a：先失败后成功 => 不算失败
            await _job(s, canvas, "a", "failed", age=10, error="旧失败")
            ok = await _job(s, canvas, "a", "succeeded", age=5)
            await _artifact(s, canvas, ok, age=5)
            # b：最新失败 => 失败；另有一个排队中的任务
            await _job(s, canvas, "b", "failed", age=3, error="供应商超时\n堆栈", operation="generate_video")
            # c：先失败，最新正在跑 => 不算失败，计入 running
            await _job(s, canvas, "c", "failed", age=4, error="x")
            await _job(s, canvas, "c", "running", age=1, progress=60)
            await _job(s, canvas, "b", "pending", age=0, operation="generate_video")
            return await service.overview(s, user)

    result = asyncio.run(_run())
    assert result["counts"]["running"] == 2 and result["counts"]["failed"] == 0
    assert sorted(item["status"] for item in result["running"]) == ["queued", "running"]
    assert {item["progress"] for item in result["running"]} == {0, 60}


@requires_pg
def test_failed_when_latest_fails(pg):
    async def _run():
        async with pg() as s:
            user = await _user(s, "u")
            canvas = await _canvas(s, user, "p", [_node("b", "video", label="镜头 B")])
            await _job(s, canvas, "b", "succeeded", age=5, operation="generate_video")
            await _job(s, canvas, "b", "failed", age=1, operation="generate_video", error="供应商超时\n堆栈")
            return await service.overview(s, user)

    result = asyncio.run(_run())
    assert result["counts"]["failed"] == 1
    assert result["failed"][0]["node_label"] == "镜头 B" and result["failed"][0]["error"] == "供应商超时"
    assert result["projects"][0]["next_hint"] == "处理 1 个失败镜头"


@requires_pg
def test_needs_review_no_selection_and_newer_than_selected(pg):
    async def _run():
        async with pg() as s:
            user = await _user(s, "u")
            canvas = await _canvas(
                s,
                user,
                "p",
                [
                    _node("n1", "image", label="没选"),
                    _node("n2", "image", label="已选最新"),
                    _node("n3", "image", label="有新图"),
                ],
            )
            j1 = await _job(s, canvas, "n1", "succeeded", age=5)
            await _artifact(s, canvas, j1, age=5)
            j2 = await _job(s, canvas, "n2", "succeeded", age=5)
            picked2 = await _artifact(s, canvas, j2, age=5)
            j3a = await _job(s, canvas, "n3", "succeeded", age=20)
            picked3 = await _artifact(s, canvas, j3a, age=20)
            j3b = await _job(s, canvas, "n3", "succeeded", age=2)
            await _artifact(s, canvas, j3b, age=2)
            canvas.graph = {
                "nodes": [
                    _node("n1", "image", label="没选"),
                    _node("n2", "image", label="已选最新", selected=picked2.id),
                    _node("n3", "image", label="有新图", selected=picked3.id),
                ],
                "edges": [],
            }
            await s.flush()
            return await service.overview(s, user)

    result = asyncio.run(_run())
    reasons = {item["node_label"]: item["reason"] for item in result["needs_review"]}
    assert reasons == {"没选": "no_selection", "有新图": "new_result"}
    assert result["counts"]["needs_review"] == 2
    project = result["projects"][0]
    assert project["next_hint"] == "为 2 个镜头选图"
    assert project["cover_artifact_id"] is not None  # 有选中图时取选中图


@requires_pg
def test_project_steps_progress_cost_and_cover(pg):
    from app.db.models import UsageEvent

    async def _run():
        async with pg() as s:
            user = await _user(s, "u")
            canvas = await _canvas(
                s,
                user,
                "分镜",
                [
                    _node("t", "text", text="剧本"),
                    _node("v1", "video"),
                    _node("v2", "video"),
                    _node("i", "image"),
                ],
            )
            job = await _job(s, canvas, "v1", "succeeded", age=3, operation="generate_video")
            await _artifact(s, canvas, job, age=3, kind="video")
            img_job = await _job(s, canvas, "i", "succeeded", age=2)
            img = await _artifact(s, canvas, img_job, age=2)
            canvas.graph = {
                **canvas.graph,
                "nodes": [*canvas.graph["nodes"][:3], _node("i", "image", selected=img.id)],
            }
            for actual, estimated in ((Decimal("1.50"), None), (None, Decimal("0.25"))):
                s.add(
                    UsageEvent(
                        project_id=canvas.project_id,
                        user_id=user.id,
                        operation="generate_video",
                        provider="ark",
                        actual_cost=actual,
                        estimated_cost=estimated,
                    )
                )
            # 本周成片：一个 3 天前成功的渲染任务，一个 10 天前的不计
            for age_days, key in ((3, "recent"), (10, "old")):
                await _job(
                    s,
                    canvas,
                    "none",
                    "succeeded",
                    operation="render_episode",
                    age=age_days * 1440,
                    finished_at=NOW - timedelta(days=age_days),
                )
            await s.flush()
            return await service.overview(s, user), img.id

    result, img_id = asyncio.run(_run())
    project = result["projects"][0]
    assert (project["shots_done"], project["shots_total"]) == (1, 2)
    assert project["total_cost"] == "1.75"
    assert project["cover_artifact_id"] == img_id
    assert [(s["key"], s["status"]) for s in project["steps"]] == [
        ("script", "done"),
        ("characters", "todo"),
        ("storyboard", "active"),
        ("voice", "todo"),
        ("final", "todo"),
    ]
    assert project["steps"][2]["label"] == "分镜 1/2"
    assert result["counts"]["finished_this_week"] == 1


def test_steps_active_rules():
    def active(done, total, script):
        return [s["key"] for s in service._steps(done, total, script) if s["status"] == "active"]

    assert active(0, 0, False) == ["script"]
    assert active(0, 0, True) == ["storyboard"]
    assert active(2, 5, False) == ["storyboard"]
    assert active(5, 5, True) == ["voice"]
    assert all(s["status"] == "todo" for s in service._steps(1, 3, True) if s["key"] == "characters")


@requires_pg
def test_counts_cover_all_canvases_beyond_fifty(pg):
    async def _run():
        async with pg() as s:
            user = await _user(s, "many")
            oldest = await _canvas(s, user, "最旧", [_node("v", "video", label="镜头")])
            await _job(s, oldest, "v", "failed", operation="generate_video", error="boom")
            for index in range(55):
                await _canvas(s, user, f"新画布{index}", [])
            oldest.updated_at = NOW - timedelta(days=30)
            await s.flush()
            return await service.overview(s, user)

    result = asyncio.run(_run())
    assert result["counts"]["failed"] == 1 and result["failed"][0]["project_title"] == "最旧"
    assert len(result["projects"]) == 20
