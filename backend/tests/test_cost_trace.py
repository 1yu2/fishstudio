"""节点级成本追踪：实际成本计算、用量上下文、成本报告与调用树。

成本报告与调用树依赖 PostgreSQL 聚合，用 TEST_DATABASE_URL 指向专用测试库运行；
测试会 drop/create 该库，**禁止指向任何有数据的库**。纯函数用例不需要数据库。
"""

import asyncio
import os
import uuid
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from app.providers.pricing import actual_video_cost

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
requires_pg = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL 未设置，跳过真实 PG 成本报告测试"
)


# ── 实际成本（纯函数） ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("unit_prices", "usage", "expected", "basis"),
    [
        ({"output_per_million_tokens": "15"}, {"completion_tokens": 108000}, Decimal("1.620000"), "tokens"),
        (
            {"gpu_per_second": "0.0001", "output_per_second": "0.01"},
            {"gpu_seconds": 300},
            Decimal("0.030000"),
            "gpu_seconds",
        ),
        ({"output_per_second": "0.5"}, {"output_video_seconds": 5.2}, Decimal("2.600000"), "video_seconds"),
        ({"per_piece": {"6": "2"}}, {"output_video_seconds": 6}, None, ""),
        ({"output_per_million_tokens": "15"}, {"output_video_seconds": 5}, None, ""),
        ({}, {"completion_tokens": 1}, None, ""),
    ],
)
def test_actual_video_cost(unit_prices, usage, expected, basis):
    assert actual_video_cost(unit_prices, usage) == (expected, basis)


def test_job_trace_context_reads_routing_snapshot():
    from app.worker import generation_worker as worker

    class _Session:
        async def scalar(self, stmt):
            return "ep-1"

    job = SimpleNamespace(
        shot_id="sh-1",
        workflow_run_id="run-1",
        workflow_step_id="step-1",
        operation="generate_video",
        params={"routing_snapshot": {"deployment_name": "seedance", "route": {"tier": "preview"}}},
    )

    context = asyncio.run(worker._job_trace_context(_Session(), job))

    assert context == {
        "shot_id": "sh-1",
        "episode_id": "ep-1",
        "workflow_run_id": "run-1",
        "workflow_step_id": "step-1",
        "purpose": "shot_video",
        "tier": "preview",
        "deployment_name": "seedance",
    }


# ── 成本报告与调用树（真实 PostgreSQL） ───────────────────────────────────


def _admin_dsn() -> dict:
    parsed = urlparse(TEST_DATABASE_URL.replace("postgresql+asyncpg", "postgresql"))
    return {
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
    }


def _target_db() -> str:
    return urlparse(TEST_DATABASE_URL.replace("postgresql+asyncpg", "postgresql")).path.lstrip("/")


async def _recreate_database(drop_only: bool = False) -> None:
    import asyncpg

    conn = await asyncpg.connect(database="postgres", **_admin_dsn())
    try:
        db = _target_db()
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid <> pg_backend_pid()",
            db,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db}"')
        if not drop_only:
            await conn.execute(f'CREATE DATABASE "{db}"')
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def pg():
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


async def _seed(factory) -> dict:
    """一集两镜：镜头 1 云端终版（有实际成本）+ 一次失败重试，镜头 2 本地预览（GPU 秒计价），
    另有一次挂在步骤上的 LLM 导演调用，以及另一集的一条用量（不应计入第 1 集）。"""
    from app.db.models import (
        Episode,
        GenerationJob,
        ModelInvocation,
        Scene,
        SeriesProject,
        Shot,
        UsageEvent,
        WorkflowRun,
        WorkflowStep,
    )

    async with factory() as s:
        project = SeriesProject(title="cost-trace")
        s.add(project)
        await s.flush()
        episode, other_episode = (
            Episode(project_id=project.id, episode_no=1),
            Episode(project_id=project.id, episode_no=2),
        )
        s.add_all([episode, other_episode])
        await s.flush()
        scene = Scene(episode_id=episode.id)
        s.add(scene)
        await s.flush()
        shot1, shot2 = Shot(scene_id=scene.id, shot_no=1), Shot(scene_id=scene.id, shot_no=2)
        s.add_all([shot1, shot2])
        run = WorkflowRun(
            project_id=project.id, status="running", input_snapshot={}, estimated_cost=Decimal("3")
        )
        s.add(run)
        await s.flush()
        video_step = WorkflowStep(
            run_id=run.id, node_key="shot.video.generate:ep", node_type="shot.video.generate", sort_order=2
        )
        director_step = WorkflowStep(
            run_id=run.id, node_key="episode.director", node_type="episode.director.review", sort_order=1
        )
        s.add_all([video_step, director_step])
        await s.flush()
        job1 = GenerationJob(
            project_id=project.id,
            shot_id=shot1.id,
            workflow_run_id=run.id,
            workflow_step_id=video_step.id,
            operation="generate_video",
            idempotency_key=f"cost-{uuid.uuid4()}",
            status="succeeded",
            provider="ark",
            model="seedance",
        )
        job2 = GenerationJob(
            project_id=project.id,
            shot_id=shot2.id,
            workflow_run_id=run.id,
            workflow_step_id=video_step.id,
            operation="generate_video",
            idempotency_key=f"cost-{uuid.uuid4()}",
            status="succeeded",
            provider="local_h3",
            model="MiniMax-H3",
        )
        s.add_all([job1, job2])
        await s.flush()
        s.add(
            ModelInvocation(
                request_id="r1",
                job_id=job1.id,
                workflow_run_id=run.id,
                purpose="shot_video",
                deployment_name="seedance",
                provider="ark",
                model="seedance",
                status="succeeded",
            )
        )
        common = {"project_id": project.id, "episode_id": episode.id, "workflow_run_id": run.id}
        s.add_all(
            [
                UsageEvent(
                    **common,
                    job_id=job1.id,
                    shot_id=shot1.id,
                    workflow_step_id=video_step.id,
                    operation="generate_video",
                    purpose="shot_video",
                    tier="final",
                    deployment_name="seedance",
                    provider="ark",
                    model="seedance",
                    estimated_cost=Decimal("2.00"),
                    actual_cost=Decimal("1.62"),
                    cost_source="provider_usage",
                    output_units=5,
                    output_tokens=108000,
                    status="success",
                ),
                UsageEvent(
                    **common,
                    job_id=job1.id,
                    shot_id=shot1.id,
                    workflow_step_id=video_step.id,
                    operation="generate_video.submit",
                    purpose="shot_video",
                    tier="final",
                    deployment_name="seedance",
                    provider="ark",
                    model="seedance",
                    estimated_cost=Decimal("2.00"),
                    status="failed",
                    error_code="rate_limited",
                ),
                UsageEvent(
                    **common,
                    job_id=job2.id,
                    shot_id=shot2.id,
                    workflow_step_id=video_step.id,
                    operation="generate_video",
                    purpose="shot_video",
                    tier="preview",
                    deployment_name="local-h3",
                    provider="local_h3",
                    model="MiniMax-H3",
                    estimated_cost=Decimal("0.05"),
                    actual_cost=Decimal("0.03"),
                    cost_source="provider_usage",
                    output_units=4,
                    gpu_seconds=300,
                    status="success",
                ),
                UsageEvent(
                    **common,
                    workflow_step_id=director_step.id,
                    operation="llm.director",
                    purpose="director",
                    provider="openai_compatible",
                    model="deepseek",
                    input_tokens=1200,
                    output_tokens=300,
                    cached_tokens=800,
                    estimated_cost=Decimal("0.01"),
                    actual_cost=Decimal("0.01"),
                    status="success",
                ),
                UsageEvent(
                    project_id=project.id,
                    episode_id=other_episode.id,
                    operation="generate_video",
                    purpose="shot_video",
                    provider="ark",
                    estimated_cost=Decimal("9"),
                    status="success",
                ),
            ]
        )
        await s.commit()
        return {
            "project": project.id,
            "episode": episode.id,
            "run": run.id,
            "shot1": shot1.id,
            "shot2": shot2.id,
            "video_step": video_step.id,
            "director_step": director_step.id,
            "job1": job1.id,
        }


@requires_pg
def test_episode_cost_report(pg):
    from app.services.cost_report_service import cost_report

    async def _run():
        ids = await _seed(pg)
        async with pg() as s:
            return ids, await cost_report(s, project_id=ids["project"], episode_id=ids["episode"])

    ids, report = asyncio.run(_run())

    totals = report["totals"]
    assert totals["calls"] == 4 and totals["failed"] == 1
    assert totals["estimated_cost"] == pytest.approx(4.06)
    assert totals["actual_cost"] == pytest.approx(1.66)
    # 实际优先：1.62 + 0.03 + 0.01，失败那次只有估算 2.00
    assert totals["effective_cost"] == pytest.approx(3.66)
    assert (
        totals["input_tokens"] == 1200
        and totals["output_tokens"] == 108300
        and totals["cached_tokens"] == 800
    )
    assert totals["gpu_seconds"] == 300 and totals["video_seconds"] == 9
    # 同时有估算与实际的三次调用：估 2.06、实 1.66
    assert totals["estimate_error_pct"] == pytest.approx(-19.42, abs=0.01)

    breakdown = report["breakdown"]
    by_purpose = {item["key"]: item for item in breakdown["purpose"]}
    assert by_purpose["shot_video"]["effective_cost"] == pytest.approx(3.65)
    assert by_purpose["director"]["input_tokens"] == 1200
    assert {item["key"]: item["effective_cost"] for item in breakdown["hosting"]} == pytest.approx(
        {"cloud": 3.63, "self_hosted": 0.03}
    )
    assert [item["key"] for item in breakdown["deployment"]][0] == "seedance"
    assert {item["key"] for item in breakdown["tier"]} == {"final", "preview", ""}
    assert [item["key"] for item in breakdown["shot"]] == [ids["shot1"], ids["shot2"]]


@requires_pg
def test_project_report_includes_all_episodes(pg):
    from app.services.cost_report_service import cost_report

    async def _run():
        ids = await _seed(pg)
        async with pg() as s:
            return await cost_report(s, project_id=ids["project"], dimensions=("purpose",))

    report = asyncio.run(_run())

    assert report["totals"]["calls"] == 5
    assert report["totals"]["effective_cost"] == pytest.approx(12.66)
    assert set(report["breakdown"]) == {"purpose"}


@requires_pg
def test_run_trace_tree(pg):
    from app.db.models import WorkflowRun
    from app.services.cost_report_service import run_trace

    async def _run():
        ids = await _seed(pg)
        async with pg() as s:
            return ids, await run_trace(s, await s.get(WorkflowRun, ids["run"]))

    ids, trace = asyncio.run(_run())

    assert trace["run"]["quoted_cost"] == 3.0
    assert trace["run"]["effective_cost"] == pytest.approx(3.66)
    assert [step["id"] for step in trace["steps"]] == [ids["director_step"], ids["video_step"]]
    director, video = trace["steps"]
    assert director["jobs"] == [] and director["usage"][0]["purpose"] == "director"
    assert len(video["jobs"]) == 2 and video["effective_cost"] == pytest.approx(3.65)
    job1 = next(job for job in video["jobs"] if job["id"] == ids["job1"])
    assert [item["status"] for item in job1["usage"]] == ["success", "failed"]
    assert job1["invocations"][0]["deployment"] == "seedance"
    assert job1["actual_cost"] == pytest.approx(1.62)
    assert trace["unassigned_jobs"] == []
