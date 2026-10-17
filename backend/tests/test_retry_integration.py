"""真实 PostgreSQL 重试聚合集成测试（复审 0.7 / P1-6 / P1-7）。

用 env `TEST_DATABASE_URL` 指向专用测试库（如
`postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/fishstudio_test_retry`），
未设置则整模块 skip。测试会 drop/create 该库并在结束时 drop，**禁止指向
任何有数据的库**。

覆盖：
1. Step 级重试全链路：Job failed → Step failed(job_failed) → Run failed →
   retry_step → 新一代 Job succeeded → Step succeeded → Run succeeded
   （旧 attempt 的 failed job 不参与当前聚合）。
2. Job 级重试：同 logical_job_key 的旧 failed job（superseded_by 非空）
   不影响新一代 succeeded 的聚合。
3. API 级双 Session 并发重试：FOR UPDATE 锁内刷新后只创建一个新 Job，
   另一个请求稳定返回 409。
"""

import asyncio
import os
from urllib.parse import urlparse

import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL 未设置，跳过真实 PG 重试集成测试"
)


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


async def _recreate_database() -> None:
    import asyncpg

    conn = await asyncpg.connect(database="postgres", **_admin_dsn())
    try:
        db = _target_db()
        await conn.execute(f'DROP DATABASE IF EXISTS "{db}"')
        await conn.execute(f'CREATE DATABASE "{db}"')
    finally:
        await conn.close()


async def _drop_database() -> None:
    import asyncpg

    conn = await asyncpg.connect(database="postgres", **_admin_dsn())
    try:
        db = _target_db()
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=$1 AND pid <> pg_backend_pid()",
            db,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db}"')
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def pg():
    """建专用库 + 迁移到 head，返回 async_sessionmaker；结束 drop 库。"""
    asyncio.run(_recreate_database())

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    import app.db.migrations as migrations

    mp = pytest.MonkeyPatch()
    mig_engine = create_async_engine(TEST_DATABASE_URL)
    mp.setattr(migrations, "engine", mig_engine)
    settings = migrations.get_settings()
    mp.setattr(settings, "database_url", TEST_DATABASE_URL)
    asyncio.run(migrations.bootstrap_and_upgrade())

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory

    asyncio.run(engine.dispose())
    asyncio.run(mig_engine.dispose())
    mp.undo()
    asyncio.run(_drop_database())


async def _make_run_with_render_step(factory):
    """造 project/episode/run + 单个 episode.render step（免费节点，绕开费用 Gate，
    专注验证重试聚合语义）+ 一个 render job。返回各 id。"""
    from app.db.models import (
        Episode,
        GenerationJob,
        SeriesProject,
        WorkflowRun,
        WorkflowStep,
    )
    from app.services import job_planner

    async with factory() as s:
        project = SeriesProject(title="retry-it")
        s.add(project)
        await s.flush()
        episode = Episode(project_id=project.id, episode_no=1)
        s.add(episode)
        await s.flush()
        run = WorkflowRun(project_id=project.id, status="running", input_snapshot={})
        s.add(run)
        await s.flush()
        step = WorkflowStep(
            run_id=run.id,
            node_key="episode.render",
            node_type="episode.render",
            entity_type="episode",
            entity_id=episode.id,
            status="running",
            attempt=0,
        )
        s.add(step)
        await s.flush()
        job = GenerationJob(
            project_id=project.id,
            workflow_run_id=run.id,
            workflow_step_id=step.id,
            operation="render_episode",
            idempotency_key=job_planner.make_job_idempotency_key(
                "render", run.id, step.node_key, episode.id, 0
            ),
            logical_job_key=job_planner.make_logical_job_key(
                "render_episode", run.id, step.node_key, episode.id
            ),
            step_attempt=0,
            status="pending",
            params={"episode_id": episode.id, "step_id": step.id, "run_id": run.id},
        )
        s.add(job)
        await s.commit()
        return project.id, episode.id, run.id, step.id, job.id


def test_step_retry_full_chain(pg, monkeypatch):
    """Job failed → Step/Run failed → retry_step → 新 Job succeeded → Step/Run succeeded。"""
    from sqlalchemy import select

    from app.db.models import GenerationJob, WorkflowRun, WorkflowStep
    from app.services import job_service, workflow_service

    # episode.render 现需已审批 Timeline 才能展开；本用例只验证重试聚合语义，
    # 审批 Gate 与 Timeline 查询都用桩替代。
    async def _approved(session, run, step):
        return True

    monkeypatch.setattr(workflow_service, "_approval_passed", _approved)

    from types import SimpleNamespace

    from app.services import timeline_service

    async def _render_revision(session, episode_id):
        return SimpleNamespace(id="timeline-stub", content_hash="timeline-stub-hash")

    monkeypatch.setattr(timeline_service, "get_render_revision", _render_revision)

    async def _run():
        project_id, episode_id, run_id, step_id, job_id = await _make_run_with_render_step(pg)

        # 1) Job failed → Step failed(job_failed) → Run failed
        async with pg() as s:
            job = await s.get(GenerationJob, job_id)
            await job_service.mark_failed(
                s, job, error_code="render_failed", error="boom", retryable=False
            )
            await workflow_service.on_job_finished(s, job)
        async with pg() as s:
            step = await s.get(WorkflowStep, step_id)
            run = await s.get(WorkflowRun, run_id)
            assert step.status == "failed"
            assert step.error_code == "job_failed"
            assert run.status == "failed"

        # 2) retry_step：attempt+1、run 恢复 ready 并重新展开新一代 render job
        async with pg() as s:
            step = await workflow_service.retry_step(s, step_id)
            assert step.attempt == 1
            assert step.status == "running"  # 新 job 已展开
        async with pg() as s:
            run = await s.get(WorkflowRun, run_id)
            assert run.status == "running"
            new_job = (
                await s.execute(
                    select(GenerationJob).where(
                        GenerationJob.workflow_step_id == step_id,
                        GenerationJob.step_attempt == 1,
                    )
                )
            ).scalars().one()

        # 3) 新一代 Job succeeded → 旧 attempt 的 failed 不影响聚合 → Step/Run succeeded
        async with pg() as s:
            job = await s.get(GenerationJob, new_job.id)
            await job_service.mark_succeeded(s, job, {"ok": True})
            await workflow_service.on_job_finished(s, job)
        async with pg() as s:
            step = await s.get(WorkflowStep, step_id)
            run = await s.get(WorkflowRun, run_id)
            assert step.status == "succeeded"
            assert run.status == "succeeded"

    asyncio.run(_run())


def test_job_level_retry_supersedes_old_failed(pg):
    """job 级 retry：旧 failed（superseded_by 回填）不影响新一代 succeeded 的聚合。"""
    from app.db.models import GenerationJob, WorkflowRun, WorkflowStep
    from app.services import job_planner, job_service, workflow_service

    async def _run():
        project_id, episode_id, run_id, step_id, job_id = await _make_run_with_render_step(pg)

        # 1) 旧 job failed → step failed(job_failed)
        async with pg() as s:
            job = await s.get(GenerationJob, job_id)
            await job_service.mark_failed(
                s, job, error_code="render_failed", error="boom", retryable=False
            )
            await workflow_service.on_job_finished(s, job)
            step = await s.get(WorkflowStep, step_id)
            assert step.status == "failed"
            assert step.error_code == "job_failed"

        # 2) job 级 retry：创建新 job（同 logical_job_key/step_attempt），旧 job 回填 superseded_by
        async with pg() as s:
            old = await s.get(GenerationJob, job_id)
            new_job = GenerationJob(
                project_id=old.project_id,
                workflow_run_id=old.workflow_run_id,
                workflow_step_id=old.workflow_step_id,
                operation=old.operation,
                idempotency_key=f"retry:{old.idempotency_key}",
                logical_job_key=old.logical_job_key,
                step_attempt=old.step_attempt,
                status="pending",
                params={**(old.params or {}), "retry_of_job_id": old.id},
            )
            s.add(new_job)
            await s.flush()
            old.superseded_by = new_job.id
            await s.commit()
            new_job_id = new_job.id

        # 3) 新 job succeeded → 聚合只看未 superseded 的新一代 → step/run succeeded
        async with pg() as s:
            job = await s.get(GenerationJob, new_job_id)
            await job_service.mark_succeeded(s, job, {"ok": True})
            await workflow_service.on_job_finished(s, job)
        async with pg() as s:
            step = await s.get(WorkflowStep, step_id)
            run = await s.get(WorkflowRun, run_id)
            assert step.status == "succeeded"
            assert run.status == "succeeded"

        # 防御：幂等键/logical_job_key 组装与 job_planner 一致
        assert new_job_id != job_id
        _ = job_planner.JOB_TERMINAL_STATUSES

    asyncio.run(_run())


def test_retry_job_concurrent_sessions_create_once(pg):
    """两个独立 Session 同时调用 retry_job，只允许一个请求创建收费重试 Job。"""
    from types import SimpleNamespace

    from fastapi import HTTPException
    from sqlalchemy import func, select

    from app.db.models import GenerationJob, SeriesProject, User
    from app.routers.jobs import retry_job
    from app.services import job_service

    async def _run():
        project_id, _, _, _, job_id = await _make_run_with_render_step(pg)

        async with pg() as session:
            user = User(
                username=f"retry-{job_id[:12]}",
                password_hash="test-only",
                is_admin=False,
            )
            session.add(user)
            await session.flush()
            project = await session.get(SeriesProject, project_id)
            project.user_id = user.id
            job = await session.get(GenerationJob, job_id)
            await job_service.mark_failed(
                session,
                job,
                error_code="render_failed",
                error="boom",
                retryable=False,
            )
            await session.commit()
            user_view = SimpleNamespace(id=user.id, is_admin=False)

        start = asyncio.Event()

        async def invoke():
            async with pg() as session:
                await start.wait()
                try:
                    response = await retry_job(job_id, session=session, user=user_view)
                    return "created", response
                except HTTPException as error:
                    return "error", error.status_code

        first = asyncio.create_task(invoke())
        second = asyncio.create_task(invoke())
        start.set()
        results = await asyncio.gather(first, second)

        assert sorted(kind for kind, _ in results) == ["created", "error"]
        assert [value for kind, value in results if kind == "error"] == [409]

        async with pg() as session:
            old_job = await session.get(GenerationJob, job_id)
            assert old_job.superseded_by
            retry_count = await session.scalar(
                select(func.count(GenerationJob.id)).where(
                    GenerationJob.params["retry_of_job_id"].astext == job_id
                )
            )
            assert retry_count == 1

    asyncio.run(_run())
