"""请求 Trace：上下文透传、产物血缘与按用户隔离的查询。"""

import asyncio
import hashlib
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from test_cost_trace import pg  # 复用专用 PG 建库/迁移 fixture

from app.db.models import Artifact, GenerationJob, ModelInvocation, SeriesProject, UsageEvent, User
from app.services import job_service, lineage_service
from app.services.execution_context import (
    ExecutionContext,
    get_execution_context,
    reset_execution_context,
    set_execution_context,
)
from app.worker import generation_worker as worker


def test_video_trace_is_removed_before_request(monkeypatch):
    def request(**params):
        assert "trace" not in params
        return params

    monkeypatch.setattr(worker, "VideoGenerationRequest", request)
    assert worker._build_video_request(
        SimpleNamespace(params={"prompt": "test", "trace": {"run_id": "r"}})
    ) == {"prompt": "test"}


@pytest.mark.parametrize("trace", [{"run_id": "run", "request_id": "request"}, None])
async def test_context_reset_on_executor_failure(monkeypatch, trace):
    outer = get_execution_context()
    job = SimpleNamespace(
        id="old",
        params={"trace": trace},
        project_id="p",
        workflow_run_id=None,
        workflow_step_id=None,
        provider="",
        operation="test",
    )

    async def fail(session, job):
        assert get_execution_context().run_id == ("run" if trace else "old")
        raise RuntimeError("test")

    monkeypatch.setitem(worker.EXECUTORS, "test", fail)
    monkeypatch.setattr(worker, "finalize_job", AsyncMock())
    await worker._process_job(AsyncMock(), job)
    assert get_execution_context() is outer


async def test_lineage_failure_is_non_blocking(monkeypatch):
    artifact = Artifact(artifact_metadata={"kept": True})
    monkeypatch.setattr(
        lineage_service, "async_object_session", lambda _: (_ for _ in ()).throw(RuntimeError("test"))
    )
    await lineage_service.attach_lineage(artifact, SimpleNamespace(params={}, id="j", operation="image"))
    assert artifact.artifact_metadata == {"kept": True}


def test_trace_end_to_end(pg, monkeypatch):
    from app.auth.dependencies import get_current_user
    from app.db.database import get_session
    from app.providers.base import ProviderResult
    from app.routers import traces

    monkeypatch.setattr(worker.media_probe, "attach_l1_check", AsyncMock())

    async def generate(*args, **kwargs):
        return SimpleNamespace(
            provider=SimpleNamespace(name="mock"),
            mocked=True,
            model="mock-image",
            deployment_name="mock",
            routing_snapshot={},
            result=ProviderResult(
                success=True, model="mock-image", artifacts=[{"storage_key": "trace/output.png"}]
            ),
        )

    monkeypatch.setattr(worker, "_resolve_worker_image", generate)

    async def run():
        async with pg() as session:
            owner = User(username=f"trace-{uuid.uuid4()}", password_hash="unused", is_admin=False)
            other = User(username=f"other-{uuid.uuid4()}", password_hash="unused", is_admin=False)
            session.add_all([owner, other])
            await session.flush()
            project = SeriesProject(title="trace", user_id=owner.id)
            session.add(project)
            await session.flush()
            source = Artifact(project_id=project.id, type="image", storage_key="trace/input.png")
            session.add(source)
            await session.flush()
            ctx = ExecutionContext.new_ids(user_id=str(owner.id), canvas_id="canvas", prompt_version="v1")
            token = set_execution_context(ctx)
            try:
                jobs = []
                for i in range(2):
                    jobs.append(
                        await job_service.create_job(
                            session,
                            operation="generate_image",
                            project_id=project.id,
                            params={
                                "prompt": "画一条鱼",
                                "first_frame": source.storage_key,
                                "reference_images": [source.storage_key, "https://example.test/ref.png"],
                                "reference_artifact_ids": [source.id],
                            },
                            idempotency_key=str(uuid.uuid4()),
                        )
                    )
                assert jobs[0].params["trace"] == {
                    key: getattr(ctx, key)
                    for key in (
                        "request_id",
                        "run_id",
                        "user_id",
                        "canvas_id",
                        "creative_role",
                        "prompt_version",
                    )
                }
                explicit = {"run_id": "explicit"}
                custom = await job_service.create_job(
                    session,
                    operation="generate_image",
                    project_id=project.id,
                    params={"trace": explicit},
                    idempotency_key=str(uuid.uuid4()),
                )
                assert custom.params["trace"] == explicit
            finally:
                reset_execution_context(token)
            script = await job_service.create_job(
                session,
                operation="generate_image",
                project_id=project.id,
                params={},
                idempotency_key=str(uuid.uuid4()),
            )
            assert script.params["trace"]["run_id"]
            await session.commit()
            outer = get_execution_context()
            for job in jobs:
                await worker._process_job(session, job)
                assert job.status == "succeeded"
                assert get_execution_context() is outer
                invocation = await session.scalar(
                    select(ModelInvocation).where(ModelInvocation.job_id == job.id)
                )
                usage = await session.scalar(select(UsageEvent).where(UsageEvent.job_id == job.id))
                assert invocation.request_id == ctx.request_id
                assert (usage.request_id, usage.run_id) == (ctx.request_id, ctx.run_id)
                usage.estimated_cost = Decimal("2")
                usage.actual_cost = Decimal("1.25") if job == jobs[0] else None
                artifact = await session.scalar(select(Artifact).where(Artifact.job_id == job.id))
                lineage = artifact.artifact_metadata["lineage"]
                assert lineage["job_id"] == job.id
                assert lineage["run_id"] == ctx.run_id
                assert lineage["model_invocation_id"] == invocation.id
                assert lineage["prompt_sha256"] == hashlib.sha256("画一条鱼".encode()).hexdigest()
                assert lineage["inputs"] == [
                    {"role": "first_frame", "ref": source.storage_key, "artifact_id": source.id},
                    {"role": "reference", "ref": source.storage_key, "artifact_id": source.id},
                    {"role": "reference", "ref": "https://example.test/ref.png"},
                ]
                assert artifact.artifact_metadata["prompt"] == "画一条鱼"
            await session.commit()
            app = FastAPI()
            app.include_router(traces.router, prefix="/api")

            async def db():
                yield session

            app.dependency_overrides[get_session] = db
            app.dependency_overrides[get_current_user] = lambda: owner
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(f"/api/traces/{ctx.run_id}")
                assert response.status_code == 200, response.text
                data = response.json()
                assert len(data["jobs"]) == 2
                assert float(data["total_cost"]) == 3.25
                for row in data["jobs"]:
                    assert all(row[key] for key in ("invocations", "usage", "events", "artifacts"))
                    assert row["artifacts"][0]["url"].startswith("/api/artifacts/")
                assert (await client.get(f"/api/jobs/{jobs[0].id}/trace")).json() == data
                assert (await client.get("/api/traces/missing")).status_code == 404
                app.dependency_overrides[get_current_user] = lambda: other
                assert (await client.get(f"/api/traces/{ctx.run_id}")).status_code == 404
                assert (await client.get(f"/api/jobs/{jobs[0].id}/trace")).status_code == 404
                other.is_admin = True
                assert (await client.get(f"/api/traces/{ctx.run_id}")).status_code == 200
            # 无 trace 的旧任务仍执行成功，并可按 job.id 查询。
            script.params = {"prompt": "legacy"}
            await session.commit()
            await worker._process_job(session, script)
            assert script.status == "succeeded"
            invocation = await session.scalar(
                select(ModelInvocation).where(ModelInvocation.job_id == script.id)
            )
            assert invocation.request_id == script.id
            legacy = await traces.get_job_trace(script.id, session, owner)
            assert legacy["run_id"] == script.id

    asyncio.run(run())


async def test_create_job_trace_without_database(monkeypatch):
    from unittest.mock import Mock

    from app.services import job_event_service

    session = AsyncMock()
    session.execute.return_value.scalar_one_or_none = Mock(return_value=None)
    session.add = Mock()
    monkeypatch.setattr(job_event_service, "emit", AsyncMock())
    monkeypatch.setattr(job_event_service, "notify_ready", AsyncMock())
    ctx = ExecutionContext.new_ids(user_id="12", canvas_id="canvas")
    token = set_execution_context(ctx)
    try:
        params = {"prompt": "fish"}
        job = await job_service.create_job(
            session, operation="generate_image", project_id="p", params=params, idempotency_key="k"
        )
        assert "trace" not in params
        assert job.params["trace"]["request_id"] == ctx.request_id
        assert job.params["trace"]["run_id"] == ctx.run_id
        assert job.params["trace"]["user_id"] == "12"
        assert job.params["trace"]["canvas_id"] == "canvas"
        explicit = {"run_id": "explicit", "extra": "preserved"}
        job = await job_service.create_job(
            session,
            operation="generate_image",
            project_id="p",
            params={"trace": explicit},
            idempotency_key="k2",
        )
        assert job.params["trace"] == explicit
    finally:
        reset_execution_context(token)
    job = await job_service.create_job(
        session, operation="generate_image", project_id="p", params={}, idempotency_key="k3"
    )
    assert uuid.UUID(job.params["trace"]["run_id"])


@pytest.mark.parametrize("entry", ["canvas", "chat"])
async def test_video_entry_filters_trace(monkeypatch, entry):
    from app.model_runtime import video
    from app.services import chat_generation_service as chat
    from app.services import job_planner
    from app.services import node_canvas_service as canvas

    original = job_planner.build_video_job_params

    def build(**kwargs):
        return {**original(**kwargs), "trace": {"run_id": "must-not-leak"}}

    plan = {
        "provider": "mock",
        "model": "mock",
        "resolution": "768P",
        "billable_duration": 5,
        "estimated_total_cost": "0",
    }
    monkeypatch.setattr(job_planner, "build_video_job_params", build)
    monkeypatch.setattr(video, "resolve_video_plan", AsyncMock(return_value=plan))

    async def create(session, **kwargs):
        assert "trace" not in kwargs["params"]
        return SimpleNamespace(**kwargs)

    if entry == "canvas":
        monkeypatch.setattr(canvas, "_reserve_budget", AsyncMock())
        monkeypatch.setattr(job_service, "create_job", create)
        obj = SimpleNamespace(
            id="canvas", project_id="project", aspect_ratio="9:16", graph={"nodes": [{"id": "node"}]}
        )
        await canvas._create_node_job(
            AsyncMock(), obj, "node", canvas.NodeRunRequest(kind="video", prompt="fish")
        )
    else:
        monkeypatch.setattr(
            chat, "get_or_create_chat_project", AsyncMock(return_value=SimpleNamespace(id="project"))
        )
        monkeypatch.setattr(chat, "_reserve_and_create", create)
        await chat.submit_video_job(AsyncMock(), user_id=1, canvas_id="canvas", prompt="fish")


async def test_worker_restores_trace_for_invocation_and_usage(monkeypatch):
    from unittest.mock import Mock

    from app.services import model_invocation_service, usage_service

    trace = {
        "request_id": "request",
        "run_id": "run",
        "user_id": "12",
        "canvas_id": "canvas",
        "creative_role": "artist",
        "prompt_version": "v1",
    }
    job = SimpleNamespace(
        id="job",
        params={"trace": trace},
        project_id="project",
        workflow_run_id=None,
        workflow_step_id=None,
        provider="",
        model="image",
        operation="test",
        shot_id=None,
        attempt=1,
    )
    session = AsyncMock()
    session.add = Mock()
    session.scalar.return_value = 12
    begin = AsyncMock(return_value=SimpleNamespace(id="invocation"))
    monkeypatch.setattr(model_invocation_service, "begin", begin)

    async def execute(session, job):
        ctx = get_execution_context()
        assert all(getattr(ctx, key) == value for key, value in trace.items())
        await worker._ensure_media_invocation(session, job, modality="image", purpose="image_generation")
        await worker._record_job_usage(session, job, operation="generate_image", status="success")

    monkeypatch.setitem(worker.EXECUTORS, "test", execute)
    outer = get_execution_context()
    await worker._process_job(session, job)
    assert get_execution_context() is outer
    assert begin.call_args.kwargs["request_id"] == "request"
    usage = session.add.call_args.args[0]
    assert isinstance(usage, UsageEvent)
    assert (usage.request_id, usage.run_id, usage.invocation_id) == ("request", "run", "invocation")


async def test_lineage_resolves_inputs_and_preserves_metadata(monkeypatch):
    from contextlib import asynccontextmanager

    source = Artifact(id="source", storage_key="inputs/source.png")

    @asynccontextmanager
    async def nested():
        yield

    session = SimpleNamespace(begin_nested=nested, scalars=AsyncMock(return_value=[source]))
    monkeypatch.setattr(lineage_service, "async_object_session", lambda _: session)
    artifact = Artifact(artifact_metadata={"l1_check": {"ok": True}, "custom": "kept"})
    job = SimpleNamespace(
        id="job",
        project_id="project",
        operation="generate_image",
        params={
            "trace": {"run_id": "run", "request_id": "request", "prompt_version": "v1"},
            "prompt": "fish",
            "model_invocation_id": "invocation",
            "first_frame": "/storage/inputs/source.png",
            "last_frame": "https://example.test/end.png",
            "source_image": source.id,
            "reference_images": [source.storage_key],
            "reference_artifact_ids": [source.id],
        },
    )
    await lineage_service.attach_lineage(artifact, job)
    lineage = artifact.artifact_metadata["lineage"]
    assert artifact.artifact_metadata["l1_check"] == {"ok": True}
    assert artifact.artifact_metadata["custom"] == "kept"
    assert lineage["inputs"] == [
        {"role": "first_frame", "ref": source.storage_key, "artifact_id": source.id},
        {"role": "last_frame", "ref": "https://example.test/end.png"},
        {"role": "reference", "ref": source.storage_key, "artifact_id": source.id},
        {"role": "source_image", "ref": source.storage_key, "artifact_id": source.id},
    ]
    assert lineage["prompt_sha256"] == hashlib.sha256(b"fish").hexdigest()
    assert lineage["prompt_version"] == "v1"
    assert "prompt" not in lineage


async def test_regeneration_preserves_request_trace(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from unittest.mock import Mock

    from app.db.models import Shot
    from app.services import candidate_service, job_event_service

    quote = SimpleNamespace(
        id="quote",
        status="quoted",
        quote_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        price_table_version=candidate_service.PRICE_TABLE_VERSION,
        reason_code="",
        instruction="",
        preserve_fields=[],
        replace_fields=[],
        quoted_cost=0,
        quote_id="q",
        currency="CNY",
    )
    session = AsyncMock()
    session.add = Mock()
    session.execute.side_effect = [
        SimpleNamespace(scalar_one_or_none=lambda: quote),
        SimpleNamespace(scalar_one_or_none=lambda: None),
    ]
    session.get.side_effect = [Shot(id="shot", visual_prompt="fish"), Artifact(type="image")]
    monkeypatch.setattr(job_event_service, "emit", AsyncMock())
    monkeypatch.setattr(job_event_service, "notify_ready", AsyncMock())
    ctx = ExecutionContext.new_ids()
    token = set_execution_context(ctx)
    try:
        job = await candidate_service.create_regeneration_job(
            session,
            candidate=SimpleNamespace(
                id="candidate", shot_id="shot", artifact_id="art", project_id="p", status="approved"
            ),
            confirmed_quote_id="q",
        )
    finally:
        reset_execution_context(token)
    assert job.params["trace"]["run_id"] == ctx.run_id
    assert job.params["regeneration_request_id"] == quote.id


def test_trace_isolation_retry_and_limit(pg):
    from datetime import datetime, timedelta, timezone

    from app.routers import jobs as jobs_router
    from app.routers import traces

    async def run():
        async with pg() as session:
            users = [
                User(username=str(uuid.uuid4()), password_hash="unused", is_admin=False) for _ in range(2)
            ]
            session.add_all(users)
            await session.flush()
            projects = [SeriesProject(title="trace isolation", user_id=user.id) for user in users]
            session.add_all(projects)
            await session.flush()
            run_id = str(uuid.uuid4())
            jobs = []
            for i, project in enumerate(projects):
                job = await job_service.create_job(
                    session,
                    operation="generate_image",
                    project_id=project.id,
                    params={
                        "trace": {
                            "run_id": run_id,
                            "request_id": "old",
                            "user_id": "old",
                            "canvas_id": "canvas",
                        }
                    },
                    idempotency_key=str(uuid.uuid4()),
                )
                jobs.append(job)
                session.add(
                    UsageEvent(
                        job_id=job.id,
                        project_id=project.id,
                        operation="generate_image",
                        estimated_cost=Decimal(i + 1),
                    )
                )
            foreign = Artifact(project_id=projects[1].id, type="image", storage_key="foreign/input.png")
            output = Artifact(project_id=projects[0].id, type="image", storage_key="own/output.png")
            session.add_all([foreign, output])
            await session.flush()
            jobs[0].params = {
                **jobs[0].params,
                "first_frame": foreign.storage_key,
                "reference_artifact_ids": [foreign.id],
            }
            await lineage_service.attach_lineage(output, jobs[0])
            assert output.artifact_metadata["lineage"]["inputs"] == [
                {"role": "first_frame", "ref": foreign.storage_key},
                {"role": "reference", "ref": foreign.id},
            ]
            await session.commit()
            for i, user in enumerate(users):
                data = await traces.get_trace(run_id, session, user)
                assert [row["id"] for row in data["jobs"]] == [jobs[i].id]
                assert data["total_cost"] == Decimal(i + 1)
                assert data["truncated"] is False
            for original_trace in (jobs[0].params["trace"], None):
                old = await job_service.create_job(
                    session,
                    operation="generate_image",
                    project_id=projects[0].id,
                    params={"trace": original_trace},
                    idempotency_key=str(uuid.uuid4()),
                )
                old.status = "failed"
                await session.commit()
                ctx = ExecutionContext.new_ids(user_id=str(users[0].id))
                token = set_execution_context(ctx)
                try:
                    result = await jobs_router.retry_job(old.id, session, users[0])
                finally:
                    reset_execution_context(token)
                retried = await session.get(GenerationJob, result["id"])
                trace = retried.params["trace"]
                assert trace["run_id"] == (run_id if original_trace else old.id)
                assert trace["request_id"] == ctx.request_id
                assert trace["user_id"] == ctx.user_id
                assert old.params["trace"] == original_trace
                if original_trace:
                    assert trace["canvas_id"] == "canvas"
            limited_run = str(uuid.uuid4())
            base = datetime.now(timezone.utc)
            batch = [
                GenerationJob(
                    project_id=projects[0].id,
                    operation="generate_image",
                    params={"trace": {"run_id": limited_run}},
                    idempotency_key=str(uuid.uuid4()),
                    created_at=base + timedelta(seconds=i),
                )
                for i in range(501)
            ]
            session.add_all(batch[:500])
            await session.flush()
            data = await traces.get_trace(limited_run, session, users[0])
            assert len(data["jobs"]) == 500 and data["truncated"] is False
            session.add(batch[500])
            await session.flush()
            data = await traces.get_trace(limited_run, session, users[0])
            assert data["truncated"] is True
            assert [row["id"] for row in data["jobs"]] == [job.id for job in batch[:500]]

    asyncio.run(run())
