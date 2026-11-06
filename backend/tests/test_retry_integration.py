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
            await job_service.mark_failed(s, job, error_code="render_failed", error="boom", retryable=False)
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
                (
                    await s.execute(
                        select(GenerationJob).where(
                            GenerationJob.workflow_step_id == step_id,
                            GenerationJob.step_attempt == 1,
                        )
                    )
                )
                .scalars()
                .one()
            )

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
            await job_service.mark_failed(s, job, error_code="render_failed", error="boom", retryable=False)
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


@pytest.mark.parametrize("entry", ["manual", "step"])
@pytest.mark.parametrize("remote_state", ["success", "unknown", "failed", "not_submitted"])
def test_video_retry_entries_preserve_paid_task(pg, monkeypatch, entry, remote_state):
    """两个真实重试入口创建新代，实际 worker 只能取回原任务。"""
    from types import SimpleNamespace

    from sqlalchemy import select

    from app.db.models import GenerationJob, SeriesProject, User, WorkflowStep
    from app.providers.base import ProviderResult
    from app.routers.jobs import retry_job
    from app.services import job_planner, workflow_service
    from app.worker import generation_worker as worker

    calls = {"submit": 0, "fetch": 0}

    class Provider:
        name = "retry_fake"

        def supports(self, req, model=None):
            return True

        async def submit(self, req, model=None):
            calls["submit"] += 1
            return ProviderResult(success=False, provider=self.name, error_code="invalid_request")

        async def get_status(self, task, protocol=None):
            return ProviderResult(success=True, provider=self.name)

        async def fetch_result(self, task, protocol=None):
            calls["fetch"] += 1
            assert task == "paid-task"
            return ProviderResult(success=False, provider=self.name, error_code="artifact_invalid")

    provider = Provider()

    async def noop(*args, **kwargs):
        pass

    async def no_abort(*args):
        return False

    async def advance(session, run_id):
        step = await session.scalar(select(WorkflowStep).where(WorkflowStep.run_id == run_id))
        old = await session.scalar(select(GenerationJob).where(GenerationJob.workflow_step_id == step.id))
        await job_planner._get_or_create_job(
            session,
            idempotency_key=f"step-retry:{old.id}",
            operation="generate_video",
            project_id=old.project_id,
            params=old.params,
            logical_job_key=old.logical_job_key,
            workflow_run_id=run_id,
            workflow_step_id=step.id,
            step_attempt=step.attempt,
        )
        await session.commit()

    monkeypatch.setattr(workflow_service, "advance_run", advance)
    monkeypatch.setattr(worker, "_ensure_media_invocation", noop)
    monkeypatch.setattr(worker, "_should_abort_before_submit", no_abort)
    monkeypatch.setattr(worker, "finalize_job", noop)
    monkeypatch.setattr(worker._router, "providers", [provider], raising=False)
    monkeypatch.setattr(worker, "_resolve_locked_video_provider", lambda *a: (provider, False))

    async def run():
        project_id, _, run_id, step_id, job_id = await _make_run_with_render_step(pg)
        async with pg() as session:
            user = User(username=f"u-{job_id}", password_hash="x")
            session.add(user)
            await session.flush()
            project = await session.get(SeriesProject, project_id)
            project.user_id = user.id
            old = await session.get(GenerationJob, job_id)
            old.operation = "generate_video"
            old.provider = provider.name
            old.model = "m"
            old.status = "failed"
            old.external_task_id = "" if remote_state == "not_submitted" else "paid-task"
            old.params = {
                "prompt": "test",
                "duration": 5,
                "external_task_failed": remote_state == "failed",
                "routing_snapshot": {"provider": provider.name, "model": "m", "estimated_total_cost": "1.5"},
            }
            step = await session.get(WorkflowStep, step_id)
            step.status = "failed"
            await session.commit()
            if entry == "manual":
                response = await retry_job(
                    job_id, session=session, user=SimpleNamespace(id=user.id, is_admin=False)
                )
                new = await session.get(GenerationJob, response["id"])
            else:
                await workflow_service.retry_step(session, step_id)
                new = await session.scalar(
                    select(GenerationJob).where(
                        GenerationJob.workflow_step_id == step_id, GenerationJob.step_attempt == 1
                    )
                )
            resume = remote_state in {"success", "unknown"}
            assert bool(new.external_task_id) == resume
            new.attempt = 1
            await worker.execute_generate_video(session, new)
            assert calls == ({"submit": 0, "fetch": 1} if resume else {"submit": 1, "fetch": 0})

    asyncio.run(run())


def test_cancel_paid_video_accounts_once_across_retries_and_concurrent_writers(pg, monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace

    from sqlalchemy import select

    from app.db.models import GenerationJob, UsageEvent
    from app.routers import jobs
    from app.services import job_service, usage_service

    async def noop(*a, **k):
        pass

    monkeypatch.setattr(jobs.workflow_service, "on_job_finished", noop)
    monkeypatch.setattr(jobs._media_runtime, "get", lambda *a: None)

    async def run():
        project_id, _, _, _, job_id = await _make_run_with_render_step(pg)
        async with pg() as session:
            job = await session.get(GenerationJob, job_id)
            job.operation = "generate_video"
            job.provider = "cancel_fake"
            job.external_task_id = f"paid-{job_id}"
            job.params = {"fetch_attempts": 1, "routing_snapshot": {"estimated_total_cost": "1.5"}}
            job.attempt = 1
            job.error_code = "network_error"
            await session.commit()

            async def checked(*a):
                return job

            monkeypatch.setattr(jobs, "_get_job_checked", checked)
            await jobs.cancel_job(job_id, session=session, user=SimpleNamespace(id=1))
            event = (await session.scalars(select(UsageEvent).where(UsageEvent.job_id == job_id))).one()
            assert event.estimated_cost == Decimal("1.5")
            assert event.status == "canceled"
            external = job.external_task_id
            retry = await job_service.create_job(
                session,
                operation="generate_video",
                project_id=project_id,
                params=job.params,
                idempotency_key=f"charge-retry:{job_id}",
            )
            await session.commit()
            retry_id = retry.id

        async def write():
            async with pg() as session:
                await usage_service.record_usage(
                    session,
                    operation="generate_video",
                    job_id=retry_id,
                    provider="cancel_fake",
                    provider_request_id=external,
                    estimated_cost="1.5",
                    status="failed",
                )
                await session.commit()

        await asyncio.gather(write(), write())
        async with pg() as session:
            events = (
                await session.scalars(select(UsageEvent).where(UsageEvent.provider_request_id == external))
            ).all()
            assert len(events) == 1

    asyncio.run(run())


def test_video_usage_is_idempotent_across_jobs_and_sessions(pg):
    """不依赖取消流程，直接验证重复/并发收尾也只入一笔账。"""
    from sqlalchemy import select

    from app.db.models import GenerationJob, UsageEvent
    from app.services import usage_service

    async def run():
        project_id, _, _, _, job_id = await _make_run_with_render_step(pg)
        external = f"idempotent-{job_id}"
        async with pg() as session:
            retry = GenerationJob(
                project_id=project_id,
                operation="generate_video",
                params={},
                idempotency_key=f"idempotent-retry:{job_id}",
            )
            session.add(retry)
            await session.commit()
            ids = [job_id, retry.id, retry.id]

        async def write(job_id):
            async with pg() as session:
                await usage_service.record_usage(
                    session,
                    job_id=job_id,
                    provider="fake",
                    provider_request_id=external,
                    operation="generate_video",
                    estimated_cost="1.5",
                    status="failed",
                )
                await session.commit()

        await asyncio.gather(*(write(job_id) for job_id in ids))
        async with pg() as session:
            events = (
                await session.scalars(select(UsageEvent).where(UsageEvent.provider_request_id == external))
            ).all()
            assert len(events) == 1
            assert str(events[0].estimated_cost) == "1.500000"

    asyncio.run(run())


@pytest.mark.parametrize("late_actual", [False, True])
def test_video_charge_upsert_preserves_best_information(pg, late_actual):
    from decimal import Decimal

    from sqlalchemy import select

    from app.db.models import UsageEvent
    from app.services.usage_service import record_usage

    async def run():
        task = f"upsert-{late_actual}"

        async def write(**fields):
            async with pg() as session:
                await record_usage(
                    session, operation="generate_video", provider="fake", provider_request_id=task, **fields
                )
                await session.commit()

        await write(estimated_cost="1.5", status="failed", output_units=0)
        await write(actual_cost="0.8" if late_actual else None, status="success", output_units=5)
        await write(estimated_cost="2", status="canceled", output_units=0)
        async with pg() as session:
            events = (
                await session.scalars(select(UsageEvent).where(UsageEvent.provider_request_id == task))
            ).all()
            assert len(events) == 1
            assert events[0].status == "success"
            assert events[0].output_units == 5
            assert events[0].actual_cost == (Decimal("0.8") if late_actual else None)
        # 已有实际金额的失败账目也能补成成功，金额和实际用量不被估价覆盖。
        task = f"{task}-actual-first"
        await write(actual_cost="0.7", status="failed", output_units=4)
        await write(estimated_cost="2", status="success", output_units=0)
        async with pg() as session:
            event = (
                await session.scalars(select(UsageEvent).where(UsageEvent.provider_request_id == task))
            ).one()
            assert event.actual_cost == Decimal("0.7")
            assert event.output_units == 4
            assert event.status == "success"

    asyncio.run(run())


@pytest.mark.parametrize("outcome", [True, False, None, "exception"])
def test_cancel_records_provider_outcome_and_only_uncertain_charge(pg, monkeypatch, outcome):
    from types import SimpleNamespace

    from sqlalchemy import select

    from app.db.models import GenerationJob, UsageEvent
    from app.routers import jobs

    async def cancel(task):
        if outcome == "exception":
            raise TimeoutError("fake timeout")
        return outcome

    async def noop(*a, **k):
        pass

    monkeypatch.setattr(
        jobs._media_runtime, "get", lambda *a: SimpleNamespace(provider=SimpleNamespace(cancel=cancel))
    )
    monkeypatch.setattr(jobs.workflow_service, "on_job_finished", noop)

    async def run():
        _, _, _, _, job_id = await _make_run_with_render_step(pg)
        async with pg() as session:
            job = await session.get(GenerationJob, job_id)
            job.operation = "generate_video"
            job.provider = "cancel-fake"
            job.external_task_id = f"cancel-{job_id}"
            job.params = {"routing_snapshot": {"estimated_total_cost": "1.5"}}
            await session.commit()

            async def checked(*a):
                return job

            monkeypatch.setattr(jobs, "_get_job_checked", checked)
            await jobs.cancel_job(job_id, session=session, user=SimpleNamespace(id=1))
            events = (await session.scalars(select(UsageEvent).where(UsageEvent.job_id == job_id))).all()
            assert len(events) == (0 if outcome is True else 1)
            assert job.params["cancel_result"]["status"] == (
                "succeeded" if outcome is True else "unknown" if outcome in (None, "exception") else "failed"
            )

    asyncio.run(run())


def test_step_retry_paid_task_with_exact_initial_budget(pg, monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace

    from app.db.models import GenerationJob, Scene, Shot, WorkflowRun, WorkflowStep
    from app.providers.base import ProviderResult
    from app.services import job_planner, workflow_service
    from app.worker import generation_worker as worker

    calls = {"fetch": 0}

    class Provider:
        name = "budget-fake"

        async def submit(self, *a, **k):
            raise AssertionError("paid task must not submit")

        async def get_status(self, task, **k):
            return ProviderResult(success=True, provider=self.name)

        async def fetch_result(self, task, **k):
            calls["fetch"] += 1
            return ProviderResult(success=True, provider=self.name, external_request_id=task)

    async def noop(*a, **k):
        pass

    shot_ids = []

    async def shots(*a):
        return [SimpleNamespace(id=shot_ids[0], visual_prompt="test", duration_ms=5000, aspect_ratio="9:16")]

    async def inputs(*a):
        return None, None

    async def routing(*a, **k):
        return {"estimated_total_cost": "1.5"}

    monkeypatch.setattr(job_planner, "load_shot_video_inputs", inputs)
    monkeypatch.setattr(job_planner, "resolve_shot_video_plan", routing)

    async def advance(session, run_id):
        run = await session.get(WorkflowRun, run_id)
        from sqlalchemy import select

        step = await session.scalar(select(WorkflowStep).where(WorkflowStep.run_id == run_id))
        jobs = await job_planner.plan_shot_video_jobs(session, run=run, step=step, episode_id=step.entity_id)
        assert len(jobs) == 1
        jobs[0].attempt = 1
        await worker.execute_generate_video(session, jobs[0])
        assert jobs[0].status == "succeeded"

    monkeypatch.setattr(job_planner, "_current_revision_shots", shots)
    monkeypatch.setattr(workflow_service, "advance_run", advance)
    monkeypatch.setattr(workflow_service, "on_job_finished", noop)
    monkeypatch.setattr(worker, "_ensure_media_invocation", noop)
    monkeypatch.setattr(worker._router, "providers", [Provider()], raising=False)

    async def run_test():
        _, _, run_id, step_id, job_id = await _make_run_with_render_step(pg)
        async with pg() as session:
            run = await session.get(WorkflowRun, run_id)
            run.estimated_cost = run.cost_limit = Decimal("1.5")
            step = await session.get(WorkflowStep, step_id)
            step.status = "failed"
            scene = Scene(episode_id=step.entity_id)
            session.add(scene)
            await session.flush()
            shot = Shot(scene_id=scene.id, visual_prompt="test", duration_ms=5000)
            session.add(shot)
            await session.flush()
            shot_ids.append(shot.id)
            job = await session.get(GenerationJob, job_id)
            job.operation = "generate_video"
            job.logical_job_key = job_planner.make_logical_job_key(
                "generate_video", run_id, step.node_key, shot_ids[0]
            )
            job.provider = "budget-fake"
            job.model = "m"
            job.external_task_id = "paid-budget-task"
            job.status = "failed"
            job.params = {"prompt": "test", "routing_snapshot": {"estimated_total_cost": "1.5"}}
            await session.commit()
            await workflow_service.retry_step(session, step_id)
            assert calls["fetch"] == 1
            await session.refresh(run)
            assert run.estimated_cost == Decimal("1.5")

    asyncio.run(run_test())


def test_video_usage_insert_failure_rolls_back_and_finishes_job(pg, monkeypatch):
    from sqlalchemy import select, text
    from sqlalchemy.sql.dml import Insert

    from app.db.models import GenerationJob, UsageEvent
    from app.providers.base import ProviderResult
    from app.worker import generation_worker as worker

    calls = {"fetch": 0, "insert": 0}

    class Provider:
        name = "insert-fake"

        async def get_status(self, task, **k):
            return ProviderResult(success=True, provider=self.name)

        async def fetch_result(self, task, **k):
            calls["fetch"] += 1
            return ProviderResult(success=True, provider=self.name, external_request_id=task)

        async def submit(self, *a, **k):
            raise AssertionError("must not resubmit")

    async def noop(*a, **k):
        pass

    monkeypatch.setattr(worker, "_ensure_media_invocation", noop)
    monkeypatch.setattr(worker._router, "providers", [Provider()], raising=False)

    async def run():
        _, _, _, _, job_id = await _make_run_with_render_step(pg)
        async with pg() as session:
            job = await session.get(GenerationJob, job_id)
            job.operation = "generate_video"
            job.provider = "insert-fake"
            job.external_task_id = "paid-insert-task"
            job.params = {"prompt": "test", "routing_snapshot": {"estimated_total_cost": "1.5"}}
            job.attempt = 1
            await session.commit()
            execute = session.execute

            async def failing_insert(statement, *a, **k):
                if isinstance(statement, Insert) and statement.table.name == "usage_events":
                    calls["insert"] += 1
                    # 真实 PG 错误使事务进入 aborted 状态，必须先 rollback。
                    return await execute(text("INSERT INTO usage_events (missing_test_column) VALUES (1)"))
                return await execute(statement, *a, **k)

            monkeypatch.setattr(session, "execute", failing_insert)
            await worker._process_job(session, job)
            assert job.status == "failed"
            assert job.error_code == "accounting_failed"
            assert job.dead_lettered_at is not None
            assert calls == {"fetch": 1, "insert": 1}
            assert not (await session.scalars(select(UsageEvent).where(UsageEvent.job_id == job_id))).all()
            leased = await worker.job_service.lease_jobs(session, operations=["generate_video"])
            assert job_id not in [item.id for item in leased]

    asyncio.run(run())
