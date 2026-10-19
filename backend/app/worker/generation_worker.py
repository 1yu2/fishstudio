"""Generation worker：轮询 generation_jobs，按 operation 分发执行。

启动方式:
    python -m app.worker.generation_worker
或由 main.py lifespan 内嵌启动（EMBEDDED_WORKER=true，默认开）。
"""

import asyncio
import logging
import os
import socket
import time
import uuid
from decimal import Decimal
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import mock_providers_enabled
from app.db.database import SessionLocal
from app.db.models import GenerationJob, RegenerationRequest, SeriesProject, WorkflowRun
from app.model_runtime.media import get_media_runtime
from app.providers.base import VideoGenerationRequest, VideoProvider
from app.providers.pricing import PRICE_TABLE_VERSION, actual_video_cost, estimate_video_cost
from app.providers.video_router import NoProviderError, RouteDecision
from app.services import (
    asset_service,
    job_event_service,
    job_service,
    model_invocation_service,
    provider_circuit_service,
    timeline_renderer,
    usage_service,
    workflow_service,
)

logger = logging.getLogger(__name__)

LEASE_SECONDS = 300
POLL_DELAY_SECONDS = 10.0  # 外部任务未完成时的重查间隔（run_after）
SUPPORTED_OPERATIONS = ["generate_video", "generate_image", "render_episode", "tts", "clone_voice"]
OPERATION_WORKER_CLASS = {
    "generate_video": "video",
    "generate_image": "image",
    "render_episode": "render",
    "tts": "audio",
    "clone_voice": "audio",
}
WORKER_ID = os.getenv("WORKER_ID", f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}")

# Provider 级并发上限（asyncio.Semaphore 限流）
PROVIDER_CONCURRENCY = {
    "local_h3": max(1, int(os.getenv("LOCAL_H3_MAX_CONCURRENCY", "1"))),
    "minimax": 3,
    "volcano": 2,
    "mock": 10,
}
_SEMAPHORES: dict[str, asyncio.Semaphore] = {
    name: asyncio.Semaphore(n) for name, n in PROVIDER_CONCURRENCY.items()
}


def _provider_semaphore(name: str) -> asyncio.Semaphore:
    return _SEMAPHORES.setdefault(name, asyncio.Semaphore(2))


_media_runtime = get_media_runtime()
_router = _media_runtime.video_router()


async def _resolve_worker_image(session, req, purpose: str, explicit_deployment: str | None = None):
    """Worker 图片入口；与 Agent Tool 延迟导入同一个 Resolver 函数。"""
    from app.model_runtime.image import resolve_and_generate_image

    return await resolve_and_generate_image(
        session, req, purpose=purpose, explicit_deployment=explicit_deployment
    )


def _resolve_locked_video_provider(
    provider_name: str, deployment_name: str = ""
) -> tuple[Optional[VideoProvider], bool]:
    """解析报价锁定 Provider；显式 Mock 模式下允许不可用真实 Provider 降级。

    新快照带 deployment_name（slug），同一 Driver 配了多个 Deployment 时据此区分；
    旧快照只有 Provider 名，按名称查找。
    """
    deployment = _media_runtime.get("video", deployment_name) if deployment_name else None
    if deployment is None:
        deployment = _media_runtime.get("video", provider_name)
    provider = deployment.provider if deployment else None
    if provider is not None and provider.name == "mock":
        return (provider, True) if mock_providers_enabled() else (None, False)
    if provider is not None and provider.is_available():
        return provider, False
    if mock_providers_enabled():
        mock_deployment = _media_runtime.get("video", "mock")
        if mock_deployment is not None:
            return mock_deployment.provider, True
    return None, False


def _build_video_request(job: GenerationJob) -> VideoGenerationRequest:
    """job.params -> VideoGenerationRequest；剔除编排字段，duration_ms 折算 duration。"""
    params = dict(job.params or {})
    for key in ("shot_id", "step_id", "run_id", "price_snapshot"):
        params.pop(key, None)
    if "duration" not in params and params.get("duration_ms"):
        params["duration"] = max(1, int(params.pop("duration_ms")) // 1000)
    else:
        params.pop("duration_ms", None)
    return VideoGenerationRequest(**params)


async def _job_user_id(session: AsyncSession, job: GenerationJob) -> Optional[int]:
    """worker 进程没有 ExecutionContext，从项目归属补 user_id（usage/summary 按用户隔离）。"""
    if not job.project_id:
        return None
    return await session.scalar(select(SeriesProject.user_id).where(SeriesProject.id == job.project_id))


_PURPOSE_BY_OPERATION = {
    "generate_video": "shot_video",
    "generate_image": "image_generation",
    "tts": "speech",
    "clone_voice": "speech",
    "render_episode": "render",
}


async def _job_trace_context(session: AsyncSession, job: GenerationJob) -> dict:
    """用量记录的业务上下文：归属的集/镜头/工作流节点、用途、档位与 Deployment。"""
    params = job.params or {}
    routing = params.get("routing_snapshot") or {}
    shot_id = job.shot_id or params.get("shot_id") or None
    episode_id = params.get("episode_id") or None
    if episode_id is None and shot_id:
        from app.db.models import Scene, Shot

        episode_id = await session.scalar(
            select(Scene.episode_id).join(Shot, Shot.scene_id == Scene.id).where(Shot.id == shot_id)
        )
    return {
        "shot_id": shot_id,
        "episode_id": episode_id,
        "workflow_run_id": job.workflow_run_id,
        "workflow_step_id": job.workflow_step_id,
        "purpose": _PURPOSE_BY_OPERATION.get(job.operation, job.operation),
        "tier": str((routing.get("route") or {}).get("tier") or ""),
        "deployment_name": str(routing.get("deployment_name") or ""),
    }


async def _record_job_usage(
    session: AsyncSession,
    job: GenerationJob,
    *,
    operation: str,
    status: str,
    latency_ms: int = 0,
    estimated_cost=None,
    actual_cost=None,
    cost_source: str = "estimate",
    output_units: float = 0,
    unit_type: str = "second",
    provider_request_id: str = "",
    error_code: str = "",
    event_metadata: Optional[dict] = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    gpu_seconds: float = 0,
) -> None:
    await usage_service.record_usage(
        session,
        **await _job_trace_context(session, job),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        gpu_seconds=gpu_seconds,
        job_id=job.id,
        invocation_id=(job.params or {}).get("model_invocation_id"),
        project_id=job.project_id,
        user_id=await _job_user_id(session, job),
        operation=operation,
        provider=job.provider,
        model=job.model,
        input_units=0,
        output_units=output_units,
        unit_type=unit_type,
        estimated_cost=estimated_cost,
        actual_cost=actual_cost,
        cost_source=cost_source,
        currency="CNY",
        latency_ms=latency_ms,
        status=status,
        attempt=job.attempt,
        provider_request_id=provider_request_id,
        error_code=error_code,
        event_metadata={"price_table_version": PRICE_TABLE_VERSION, **(event_metadata or {})},
    )


async def _ensure_media_invocation(
    session: AsyncSession, job: GenerationJob, *, modality: str, purpose: str
) -> str:
    params = dict(job.params or {})
    existing = params.get("model_invocation_id")
    if existing:
        return existing
    snapshot = params.get("routing_snapshot") or {
        "deployment_name": f"{job.provider}.{job.model or 'default'}",
        "provider": job.provider,
        "model": job.model,
        "price_snapshot": params.get("price_snapshot") or params.get("quote_snapshot") or {},
    }
    invocation = await model_invocation_service.begin(
        session,
        request_id=job.id,
        purpose=purpose,
        modality=modality,
        deployment_name=snapshot.get("deployment_name") or f"{job.provider}.default",
        provider=job.provider,
        model=job.model,
        routing_snapshot=snapshot,
        project_id=job.project_id,
        workflow_run_id=job.workflow_run_id,
        workflow_step_id=job.workflow_step_id,
        job_id=job.id,
    )
    job.params = {**params, "model_invocation_id": invocation.id}
    await session.flush()
    return invocation.id


async def finalize_job(
    session: AsyncSession,
    job: GenerationJob,
    *,
    success: bool,
    result: Optional[dict] = None,
    error_code: str = "",
    error: str = "",
    retryable: bool = False,
    artifact: Optional[dict] = None,
    usage_fields: Optional[dict] = None,
) -> None:
    """统一终态入口（P1-8）：usage → mark_succeeded/mark_failed → workflow 回推 → commit。

    所有"标失败后直接 return"的分支都必须走这里，保证任何终态（含
    unsupported_operation / no_provider / invalid_params / submit 终态失败）
    都回推 workflow，step/run 不会停在 running。

    - usage_fields：透传 _record_job_usage 的关键字参数（可选）。
    - artifact：已建档产物的 {artifact_id, storage_key, url} 等，并入 result（可选）。
    - retryable=True 且未达 max_attempts 时 mark_failed 会重置 pending（非终态），
      此时不回推 workflow（job 还会再跑）。

    并发保护（P1-9）：成功路径在 mark_succeeded 前重新读 job.status——
    若执行期间已被用户取消（canceled），不写成功、不回推 workflow，
    只落 usage 记录；防止 canceled 后被 worker 覆盖回 succeeded。
    """
    invocation_id = (job.params or {}).get("model_invocation_id")
    if usage_fields:
        await _record_job_usage(session, job, **usage_fields)
    if success:
        await session.refresh(job)
        if job.status == "canceled":
            # 已取消：不写成功、不回推（取消端点已按 canceled 聚合回推）
            await model_invocation_service.finish_by_id(
                session, invocation_id, status="canceled", latency_ms=0
            )
            await session.commit()
            return
        await job_service.mark_succeeded(session, job, {**(result or {}), **(artifact or {})})
        if job.provider:
            await provider_circuit_service.record_success(session, job.provider, job.model)
    else:
        await job_service.mark_failed(session, job, error_code=error_code, error=error, retryable=retryable)
        if job.provider and retryable:
            await provider_circuit_service.record_failure(session, job.provider, job.model, error=error)
    terminal_status = {
        "succeeded": "succeeded",
        "failed": "failed",
        "canceled": "canceled",
    }.get(job.status)
    if terminal_status:
        await model_invocation_service.finish_by_id(
            session,
            invocation_id,
            status=terminal_status,
            latency_ms=int((usage_fields or {}).get("latency_ms") or 0),
            external_request_id=job.external_task_id or "",
            error_code=job.error_code or "",
            error=job.error or "",
            result_metadata={"artifact": artifact or {}, "operation": job.operation},
        )
    if job.status in job_service.TERMINAL_STATES:
        await workflow_service.on_job_finished(session, job)
        await job_event_service.emit_batch_progress(session, job)
    request_id = (job.params or {}).get("regeneration_request_id")
    if request_id:
        regen = await session.get(RegenerationRequest, request_id)
        if regen:
            regen.status = "succeeded" if job.status == "succeeded" else "failed"
            if job.status == "succeeded" and regen.actual_cost is None:
                cost = (usage_fields or {}).get("actual_cost")
                if cost is None:
                    cost = (usage_fields or {}).get("estimated_cost")
                regen.actual_cost = Decimal(str(cost)) if cost is not None else regen.quoted_cost
    await session.commit()


async def _should_abort_before_submit(session: AsyncSession, job: GenerationJob) -> bool:
    """提交外部任务前的二次校验（P1-9/P1-10 并发保护）。

    lease 到执行之间用户可能 cancel job 或 pause/cancel run：
    - job 已 canceled → 放弃执行（取消端点已回推 workflow，这里不动）；
    - run 已 canceled → job 标 canceled 并回推（run 终态，job 不会再被需要）；
    - run 已 paused → job 置回 pending 清租约，等 resume 后重新被领取；
    返回 True 表示应放弃本次执行。
    """
    await session.refresh(job)
    if job.status == "canceled":
        return True
    if job.workflow_run_id:
        run = await session.get(WorkflowRun, job.workflow_run_id)
        if run is not None and run.status == "canceled":
            job.status = "canceled"
            job.finished_at = job_service._utcnow()
            await session.flush()
            await workflow_service.on_job_finished(session, job)
            await session.commit()
            return True
        if run is not None and run.status == "paused":
            job.status = "pending"
            job.lease_until = None
            await session.commit()
            return True
    return False


def _record_remote_progress(job: GenerationJob, remote: dict[str, Any]) -> None:
    """把供应商返回的进度（0～100）和排队位置记到 job 上；没有这些字段的供应商保持原样。"""
    progress = remote.get("progress")
    if isinstance(progress, (int, float)) and not isinstance(progress, bool):
        job.progress = max(job.progress or 0, min(99, int(progress)))
    remote_status = str(remote.get("status") or "").lower()
    queue_position = remote.get("queue_position")
    job.result = {
        **(job.result or {}),
        "remote_status": remote_status,
        "queue_position": int(queue_position) if isinstance(queue_position, int) and queue_position > 0 else 0,
    }


async def execute_generate_video(session: AsyncSession, job: GenerationJob) -> None:
    """generate_video executor：路由 → submit（如未提交）→ get_status → fetch_result。"""
    if await _should_abort_before_submit(session, job):
        return
    started = time.monotonic()
    try:
        req = _build_video_request(job)
        req.idempotency_key = job.id
    except Exception as e:
        await finalize_job(session, job, success=False, error_code="invalid_params", error=str(e))
        return

    # 1) 未提交过 → submit（首次路由并锁定 Provider）
    # R1.4：优先使用 job.params.routing_snapshot（来自已确认 Quote 的锁定快照）；
    # 仅当无快照时动态路由（重试/回退等场景）
    if not job.external_task_id:
        submit_model: Optional[str] = None
        routing_snapshot = (job.params or {}).get("routing_snapshot")
        if routing_snapshot:
            # 使用已锁定 Provider/Model
            locked_prov_name = routing_snapshot.get("provider", job.provider)
            decision_prov, mocked = _resolve_locked_video_provider(
                locked_prov_name, routing_snapshot.get("deployment_name", "")
            )
            if decision_prov is None:
                await finalize_job(
                    session,
                    job,
                    success=False,
                    error_code="provider_unavailable",
                    error=f"报价锁定的 Provider {locked_prov_name} 当前不可用，请重新确认费用",
                    usage_fields={
                        "operation": "generate_video",
                        "status": "failed",
                        "error_code": "provider_unavailable",
                    },
                )
                return
            provider = decision_prov
            # R6.3：先按报价快照规范化请求，再校验锁定模型能力。
            billable = routing_snapshot.get("billable_duration")
            if billable and billable != req.duration:
                req.duration = billable
            locked_model = routing_snapshot.get("model", "")
            if not mocked and not provider.supports(req, model=locked_model):
                await finalize_job(
                    session,
                    job,
                    success=False,
                    error_code="routing_snapshot_invalid",
                    error=(
                        f"锁定模型 {routing_snapshot.get('model')} 不支持当前请求 "
                        f"(duration={req.duration}, mode={req.mode})"
                    ),
                    usage_fields={
                        "operation": "generate_video",
                        "status": "failed",
                        "error_code": "routing_snapshot_invalid",
                    },
                )
                return
            job.provider = provider.name
            job.model = "mock-video" if mocked else locked_model
            job.params = {
                **(job.params or {}),
                "resolution": routing_snapshot.get("resolution", "768P"),
                "price_snapshot": routing_snapshot.get("price_snapshot", {}),
            }
            if mocked:
                job.params = {
                    **job.params,
                    "mock_execution": {
                        "quoted_provider": locked_prov_name,
                        "quoted_model": locked_model,
                    },
                }
            else:
                submit_model = locked_model
            await session.flush()
            estimated = routing_snapshot.get("estimated_total_cost") or estimate_video_cost(
                locked_prov_name,
                locked_model,
                routing_snapshot.get("resolution", "768P"),
                output_seconds=req.duration,
                input_image_count=len(req.reference_images) + (1 if req.first_frame else 0),
                regeneration_output_seconds=req.duration if req.mode == "regenerate" else 0,
            )
            sem = _provider_semaphore(provider.name)

        else:
            try:
                decision: RouteDecision = _router.select(req)
            except NoProviderError as e:
                await finalize_job(session, job, success=False, error_code="no_provider", error=str(e))
                return
            except ValueError as e:
                await finalize_job(session, job, success=False, error_code="no_provider", error=str(e))
                return
            provider = decision.provider

            # 路由结果写回 job（价格快照、模型/分辨率进 params，便于审计；protocol_version
            # 在 submit 后持久化，确保 Worker 重启后查询仍按正确协议）
            job.provider = provider.name
            job.model = decision.model
            job.params = {
                **(job.params or {}),
                "resolution": decision.resolution,
                "price_snapshot": decision.price_snapshot,
            }
            await session.flush()

            estimated = estimate_video_cost(
                provider.name,
                decision.model,
                decision.resolution,
                output_seconds=req.duration,
                input_image_count=len(req.reference_images) + (1 if req.first_frame else 0),
                regeneration_output_seconds=req.duration if req.mode == "regenerate" else 0,
            )
            mocked = decision.mocked
            submit_model = decision.model
            sem = _provider_semaphore(provider.name)

        await _ensure_media_invocation(session, job, modality="video", purpose="shot_video")
        async with sem:
            result = await provider.submit(req, model=submit_model)
        latency_ms = int((time.monotonic() - started) * 1000)
        if not result.success:
            await finalize_job(
                session,
                job,
                success=False,
                error_code=result.error_code or "submit_failed",
                error=result.error_message,
                retryable=result.retryable,
                usage_fields={
                    "operation": "generate_video.submit",
                    "status": "failed",
                    "latency_ms": latency_ms,
                    "estimated_cost": estimated,
                    "error_code": result.error_code,
                },
            )
            return
        job.external_task_id = result.external_request_id
        protocol_version = (
            "mock"
            if mocked
            else (
                (routing_snapshot or {}).get("protocol")
                or (result.raw_metadata or {}).get("protocol")
                or "v1"
            )
        )
        job.params = {**(job.params or {}), "protocol_version": protocol_version}
        # R5.5：验证 Provider 实际使用的模型与锁定快照一致，不一致时标失败
        expected_model = job.model
        if expected_model and result.model and result.model != expected_model:
            await finalize_job(
                session,
                job,
                success=False,
                error_code="model_mismatch",
                error=(f"Provider 返回模型 {result.model} 与执行模型 {expected_model} 不一致"),
                usage_fields={
                    "operation": "generate_video.submit",
                    "status": "failed",
                    "latency_ms": latency_ms,
                    "estimated_cost": estimated,
                    "error_code": "model_mismatch",
                },
            )
            return
        # R0.11：外部 submit 成功后立即持久化 task_id/provider/model/协议，
        # 防止进程崩溃丢失已提交任务，导致租约恢复后重复提交收费任务
        await session.commit()
        await session.refresh(job)
        await _record_job_usage(
            session,
            job,
            operation="generate_video.submit",
            status="submitted",
            latency_ms=latency_ms,
            provider_request_id=result.external_request_id,
            event_metadata={
                "mocked": mocked,
                "estimated_cost_snapshot": str(estimated) if estimated is not None else "",
            },
        )
        if result.artifacts:
            await _finish_job(session, job, req, result, estimated, started)
            return
    else:
        # 已提交的任务：按 job 上已持久化的 provider 继续轮询，不重新路由
        provider = next((p for p in _router.providers if p.name == job.provider), None)
        if provider is None:
            await finalize_job(
                session,
                job,
                success=False,
                error_code="provider_adapter_missing",
                error=f"job 记录的 provider {job.provider} 在当前 Router 中不可用，任务无法恢复",
                usage_fields={
                    "operation": "generate_video",
                    "status": "failed",
                    "error_code": "provider_adapter_missing",
                },
            )
            return
        from app.providers.pricing import estimate_video_cost as evc

        estimated = ((job.params or {}).get("routing_snapshot") or {}).get("estimated_total_cost") or evc(
            job.provider or "minimax",
            job.model or "MiniMax-H3",
            (job.params or {}).get("resolution", "768P"),
            output_seconds=req.duration,
            input_image_count=len(req.reference_images) + (1 if req.first_frame else 0),
            regeneration_output_seconds=req.duration if req.mode == "regenerate" else 0,
        )

        sem = _provider_semaphore(provider.name)

    await _ensure_media_invocation(session, job, modality="video", purpose="shot_video")
    # 2) 查询状态
    await job_service.mark_running(session, job)
    protocol = (job.params or {}).get("protocol_version", "v1")
    async with sem:
        status = await provider.get_status(job.external_task_id, protocol=protocol)
    if not status.success:
        if status.error_code == "pending":
            # 正常进行中：记下远端进度与排队位置供界面展示，重新入队，轮询不烧 attempt
            _record_remote_progress(job, status.raw_metadata)
            await job_service.requeue_for_poll(session, job, delay_seconds=POLL_DELAY_SECONDS)
            return
        await finalize_job(
            session,
            job,
            success=False,
            error_code=status.error_code or "task_failed",
            error=status.error_message,
            retryable=status.retryable,
            usage_fields={
                "operation": "generate_video",
                "status": "failed",
                "estimated_cost": estimated,
                "error_code": status.error_code or "query_error",
            },
        )
        return

    # 3) 完成 → 下载产物
    protocol = (job.params or {}).get("protocol_version", "v1")
    async with sem:
        fetched = await provider.fetch_result(job.external_task_id, protocol=protocol)
    if not fetched.success:
        await finalize_job(
            session,
            job,
            success=False,
            error_code=fetched.error_code or "fetch_failed",
            error=fetched.error_message,
            retryable=fetched.retryable,
            usage_fields={
                "operation": "generate_video",
                "status": "failed",
                "estimated_cost": estimated,
                "error_code": fetched.error_code or "fetch_failed",
            },
        )
        return
    await _finish_job(session, job, req, fetched, estimated, started)


async def _finish_job(
    session: AsyncSession,
    job: GenerationJob,
    req: VideoGenerationRequest,
    result,
    estimated,
    started: float,
) -> None:
    """产物建档 → mark_succeeded → 记成功用量 → 回推 workflow。"""
    artifact_id = ""
    storage_key = ""
    url = ""
    meta = {
        "provider": job.provider,
        "model": job.model,
        "prompt": req.prompt,
        "shot_id": (job.params or {}).get("shot_id", ""),
        "price_snapshot": (job.params or {}).get("price_snapshot", {}),
    }
    for a in result.artifacts:
        storage_key = a.get("storage_key", "")
        if not storage_key:
            continue
        artifact = await asset_service.create_artifact(
            session,
            project_id=job.project_id,
            job_id=job.id,
            type=a.get("type", "video"),
            storage_key=storage_key,
            mime_type="video/mp4" if a.get("type", "video") == "video" else "",
            duration_ms=a.get("duration_ms", req.duration * 1000),
            # 尾帧可作为下一镜首帧（镜头接力）；只有支持返回尾帧的 Provider 才有。
            meta={
                **meta,
                **(
                    {"last_frame_storage_key": a["last_frame_storage_key"]}
                    if a.get("last_frame_storage_key")
                    else {}
                ),
            },
        )
        artifact_id = artifact.id
        url = asset_service.artifact_url(artifact)
        shot_id = (job.params or {}).get("shot_id") or job.shot_id
        if shot_id:
            from app.services import candidate_service

            await candidate_service.register_candidate(
                session, artifact=artifact, shot_id=shot_id, candidate_index=0
            )
        request_id = (job.params or {}).get("regeneration_request_id")
        if request_id:
            from app.db.models import RegenerationRequest

            regen = await session.get(RegenerationRequest, request_id)
            if regen:
                regen.result_artifact_id = artifact.id
                regen.status = "succeeded"
        break  # MVP 单产物

    usage = result.usage or {}
    mocked = result.raw_metadata.get("mocked", False)
    # 按厂商返回的实际用量（Token / GPU 秒 / 视频秒）计算实际成本；算不出时只保留估算。
    unit_prices = ((job.params or {}).get("price_snapshot") or {}).get("unit_prices") or {}
    actual, actual_basis = (None, "") if mocked else actual_video_cost(unit_prices, usage)
    await finalize_job(
        session,
        job,
        success=True,
        result={
            "provider": result.provider,
            "model": result.model,
            "external_task_id": result.external_request_id,
            "mocked": mocked,
        },
        artifact={"artifact_id": artifact_id, "storage_key": storage_key, "url": url},
        usage_fields={
            "operation": "generate_video",
            "status": "success",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "estimated_cost": estimated,
            "actual_cost": actual,
            "cost_source": "provider_usage" if actual is not None else "estimate",
            "output_units": float(usage.get("output_video_seconds", 0) or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "gpu_seconds": float(usage.get("gpu_seconds") or 0),
            "provider_request_id": result.external_request_id,
            "event_metadata": {"mocked": mocked, "actual_cost_basis": actual_basis},
        },
    )


async def execute_generate_image(session: AsyncSession, job: GenerationJob) -> None:
    """generate_image executor：真实图片 provider（volcano → …）优先；无 provider 且
    未允许 mock 时 mark_failed(no_provider) 且不得创建 Artifact 伪装成功（P1-1）。

    真实图片 provider 由 app.providers.image_base.available_image_providers 注册
    （当前为火山图片 VolcanoImageProvider）；Mock 路径仅 ALLOW_MOCK_PROVIDERS=true
    时启用（测试/演示）。
    """
    from app.providers.image_base import ImageGenerationRequest, ImageProvider

    if await _should_abort_before_submit(session, job):
        return

    params = dict(job.params or {})
    req = ImageGenerationRequest(
        prompt=params.get("prompt", ""),
        resolution=params.get("resolution", "768P"),
        aspect_ratio=params.get("aspect_ratio", "9:16"),
        candidate_count=int(params.get("candidate_count", 1)),
        source_image=params.get("source_image") or None,
    )

    purpose = "image_edit" if req.source_image else "image_generation"
    try:
        execution = await _resolve_worker_image(
            session, req, purpose=purpose, explicit_deployment=params.get("deployment") or None
        )
    except RuntimeError as error:
        await finalize_job(
            session,
            job,
            success=False,
            error_code="no_provider",
            error=str(error),
        )
        return

    provider = execution.provider
    mocked = execution.mocked
    job.provider = provider.name
    job.model = execution.model
    job.params = {
        **(job.params or {}),
        "routing_snapshot": execution.routing_snapshot,
        "deployment_name": execution.deployment_name,
    }
    await _ensure_media_invocation(session, job, modality="image", purpose=purpose)
    await session.flush()
    result = execution.result
    job.model = result.model or execution.model
    # 图片计费（pricing 图片单价表；无条目按 None，不按 0 入账）
    from app.providers.pricing import estimate_image_cost

    image_cost = (
        None
        if mocked
        else estimate_image_cost(provider.name, result.model or "", count=max(1, req.candidate_count))
    )
    if not result.success:
        await finalize_job(
            session,
            job,
            success=False,
            error_code=result.error_code or "generate_failed",
            error=result.error_message,
            retryable=result.retryable,
            usage_fields={
                "operation": "generate_image",
                "status": "failed",
                "estimated_cost": image_cost,
                "error_code": result.error_code or "generate_failed",
            },
        )
        return

    # 为每个候选图片创建独立 Artifact（复审 0.4）
    artifact_ids: list[str] = []
    storage_keys: list[str] = []
    for a in result.artifacts:
        sk = (a or {}).get("storage_key", "")
        if not sk:
            continue
        artifact = await asset_service.create_artifact(
            session,
            project_id=job.project_id,
            job_id=job.id,
            type="image",
            storage_key=sk,
            mime_type="image/png",
            meta={"prompt": req.prompt, "mocked": mocked},
        )
        artifact_ids.append(artifact.id)
        storage_keys.append(sk)
        shot_id = (job.params or {}).get("shot_id") or job.shot_id
        if shot_id:
            from app.services import candidate_service

            await candidate_service.register_candidate(
                session, artifact=artifact, shot_id=shot_id, candidate_index=len(artifact_ids) - 1
            )
        request_id = (job.params or {}).get("regeneration_request_id")
        if request_id:
            from app.db.models import RegenerationRequest

            regen = await session.get(RegenerationRequest, request_id)
            if regen:
                regen.result_artifact_id = artifact.id
                regen.status = "succeeded"

    # 按成功数量重新估算图片费用（partial 场景只计实际成功数）
    actual_cost = (
        estimate_image_cost(provider.name, result.model or "", count=len(artifact_ids))
        if not mocked
        else None
    )

    usage_meta = {
        "mocked": mocked,
        "candidate_count": req.candidate_count,
        "success_count": len(artifact_ids),
        "artifact_ids": artifact_ids,
        "partial": (result.raw_metadata or {}).get("partial", False),
        **(result.raw_metadata or {}),
    }
    await finalize_job(
        session,
        job,
        success=True,
        result={
            "mocked": mocked,
            "candidate_count": req.candidate_count,
            "artifact_ids": artifact_ids,
            "success_count": len(artifact_ids),
            "partial": usage_meta.get("partial", False),
            **{
                k: v
                for k, v in result.raw_metadata.items()
                if k not in ("mocked", "candidate_count", "success_count", "failed_count", "partial")
            },
        },
        artifact={
            "artifact_id": artifact_ids[0] if artifact_ids else "",
            "storage_key": storage_keys[0] if storage_keys else "",
            "url": f"/api/artifacts/{artifact_ids[0]}/download" if artifact_ids else "",
        },
        usage_fields={
            "operation": "generate_image",
            "status": "success",
            "estimated_cost": actual_cost,
            "cost_source": "estimate",
            "output_units": float(
                (result.usage or {}).get("output_image_count", len(artifact_ids)) or len(artifact_ids)
            ),
            "provider_request_id": result.external_request_id,
            "event_metadata": usage_meta,
        },
    )


async def execute_render_episode(session: AsyncSession, job: GenerationJob) -> None:
    """render_episode executor：调用 timeline_renderer 整集合成（本地 ffmpeg，不计费）。

    render_mode=final 时任何镜头缺视频产物 → timeline_renderer 抛 ValueError，
    这里 finalize 为 failed（error_code=missing_shot_video, retryable=False，P1-11）。
    """
    started = time.monotonic()
    params = dict(job.params or {})
    episode_id = params.get("episode_id")
    if not episode_id:
        await finalize_job(
            session,
            job,
            success=False,
            error_code="invalid_params",
            error="render_episode 缺少 params.episode_id",
        )
        return

    try:
        await job_service.mark_running(session, job)
        result = await timeline_renderer.render_episode(
            session,
            project_id=job.project_id,
            episode_id=episode_id,
            output_aspect=params.get("output_aspect"),
            burn_subtitles=params.get("burn_subtitles", True),
            bgm_storage_key=params.get("bgm_storage_key"),
            bgm_volume_db=params.get("bgm_volume_db", -18.0),
            render_mode=params.get("render_mode", "preview"),
            timeline_revision_id=params.get("timeline_revision_id"),
            export_profile=params.get("export_profile", params.get("render_mode", "preview")),
        )
    except ValueError as e:
        # final 模式缺镜头产物等确定性错误：终态失败不可重试（P1-11）
        logger.warning(f"render_episode 校验失败 (episode={episode_id}): {e}")
        await finalize_job(
            session,
            job,
            success=False,
            error_code="missing_shot_video",
            error=str(e),
            retryable=False,
            usage_fields={
                "operation": "render_episode",
                "status": "failed",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error_code": "missing_shot_video",
            },
        )
        return
    except Exception as e:
        logger.exception(f"render_episode 渲染失败 (episode={episode_id})")
        await finalize_job(
            session,
            job,
            success=False,
            error_code="render_failed",
            error=str(e),
            retryable=True,
            usage_fields={
                "operation": "render_episode",
                "status": "failed",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error_code": "render_failed",
            },
        )
        return

    await usage_service.record_usage(
        session,
        **await _job_trace_context(session, job),
        job_id=job.id,
        project_id=job.project_id,
        user_id=await _job_user_id(session, job),
        operation="render_episode",
        provider="local",
        model="ffmpeg",
        output_units=float(result["duration_ms"]) / 1000.0,
        unit_type="second",
        estimated_cost=0,
        actual_cost=0,
        currency="CNY",
        latency_ms=int((time.monotonic() - started) * 1000),
        status="success",
        attempt=job.attempt,
        event_metadata={
            "render_mode": result["manifest"].get("render_mode", "preview"),
            "is_publishable": result["manifest"].get("is_publishable", False),
            "placeholder_shots": result["manifest"].get("placeholder_shots", []),
            "subtitles_burned": result["manifest"]["subtitles"]["subtitles_burned"],
        },
    )
    await finalize_job(
        session,
        job,
        success=True,
        result={},
        artifact={
            "artifact_id": result["artifact_id"],
            "storage_key": result["storage_key"],
            "url": f"/api/artifacts/{result['artifact_id']}/download" if result["artifact_id"] else "",
        },
    )


async def execute_tts(session: AsyncSession, job: GenerationJob) -> None:
    """tts executor（P1-12）：合成单条对白/旁白轨并回写 Shot track.audio_storage_key。

    Provider 选择：有 DASHSCOPE_API_KEY 用 QwenTTSProvider；否则 mock 受
    ALLOW_MOCK_PROVIDERS 控制；都不允许 → no_provider 终态失败。
    """
    from app.db.models import Shot
    from app.providers.tts_base import TTSRequest

    if await _should_abort_before_submit(session, job):
        return

    params = dict(job.params or {})
    text = (params.get("text") or "").strip()
    if not text:
        await finalize_job(
            session,
            job,
            success=False,
            error_code="invalid_params",
            error="tts 缺少 params.text",
        )
        return

    try:
        provider, mocked = _media_runtime.resolve_speech()
    except RuntimeError:
        await finalize_job(
            session,
            job,
            success=False,
            error_code="no_provider",
            error="无可用 TTS provider 且未开启 ALLOW_MOCK_PROVIDERS",
        )
        return

    voice_reference = None
    voice_asset_id = params.get("voice_asset_id")
    voice_id = (params.get("voice_id") or "").strip()
    voice_model = (params.get("voice_model") or "").strip()
    if voice_asset_id or voice_id:
        from app.db.models import AssetCard

        card = None
        if voice_asset_id:
            card = await session.get(AssetCard, voice_asset_id)
        if card is not None and card.status == "approved":
            provider_meta = card.provider_meta or {}
            voice_id = voice_id or (provider_meta.get("voice_id") or "").strip()
            voice_model = voice_model or (provider_meta.get("voice_model") or "").strip()
            voice_reference = provider_meta.get("reference_audio")
        if voice_asset_id and (card is None or card.status != "approved"):
            await finalize_job(
                session,
                job,
                success=False,
                error_code="invalid_params",
                error="TTS 需要已批准的 voice 资产；请先完成 clone-voice → 试听 → 审批",
            )
            return
        if not voice_id and not voice_reference:
            await finalize_job(
                session,
                job,
                success=False,
                error_code="invalid_params",
                error="已批准的 voice 资产缺少 voice_id/reference_audio，请重新复刻音色",
            )
            return

    req = TTSRequest(
        text=text,
        voice_id=voice_id,
        voice_model=voice_model,
        lang=params.get("lang", "zh"),
        voice_reference=voice_reference,
        filename_prefix=f"tts_{params.get('track_type', 'track')}",
    )
    job.provider = provider.name
    await _ensure_media_invocation(session, job, modality="speech", purpose="tts")
    await session.flush()
    started = time.monotonic()
    result = await provider.synthesize(req)
    if not result.success:
        await finalize_job(
            session,
            job,
            success=False,
            error_code=result.error_code or "tts_failed",
            error=result.error_message,
            retryable=result.retryable,
            usage_fields={
                "operation": "tts",
                "status": "failed",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error_code": result.error_code or "tts_failed",
            },
        )
        return

    storage_key = result.artifacts[0]["storage_key"]
    duration_ms = result.artifacts[0].get("duration_ms")
    artifact = await asset_service.create_artifact(
        session,
        project_id=job.project_id,
        job_id=job.id,
        type="audio",
        storage_key=storage_key,
        mime_type="audio/wav",
        duration_ms=duration_ms,
        meta={
            "shot_id": params.get("shot_id", ""),
            "track_type": params.get("track_type", ""),
            "track_index": params.get("track_index"),
            "text": text,
            "mocked": mocked,
        },
    )

    # 回写 Shot 的 track：JSONB 变更检测需要整体重新赋值 list
    shot = await session.get(Shot, params.get("shot_id") or "")
    if shot is not None:
        field = "dialogue_tracks" if params.get("track_type") == "dialogue" else "narration_tracks"
        tracks = [dict(t) for t in (getattr(shot, field) or [])]
        index = params.get("track_index")
        if isinstance(index, int) and 0 <= index < len(tracks):
            tracks[index]["audio_storage_key"] = storage_key
            setattr(shot, field, tracks)
            await session.flush()
    else:
        logger.warning(f"tts job {job.id}: shot {params.get('shot_id')} 不存在，跳过 track 回写")

    await finalize_job(
        session,
        job,
        success=True,
        result={
            "provider": result.provider,
            "model": result.model,
            "mocked": mocked,
        },
        artifact={"artifact_id": artifact.id, "storage_key": storage_key},
        usage_fields={
            "operation": "tts",
            "status": "success",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "output_units": float(result.usage.get("output_audio_seconds", 0) or 0),
            "provider_request_id": result.external_request_id,
            "event_metadata": {"mocked": mocked},
        },
    )


async def execute_clone_voice(session: AsyncSession, job: GenerationJob) -> None:
    """clone_voice executor（P1-12）：用参考音频复刻音色并回写 voice 资产。

    params = {asset_id, reference_audio_storage_key, voice_name}：
    provider.clone_voice -> 更新 asset.provider_meta（voice_id 等）+ asset.status
    → preview_ready + 试听样本 create_artifact(type=audio) + bind_artifact(role="preview")。
    Provider 选择：有 DASHSCOPE_API_KEY 用 QwenTTSProvider；否则 mock 受
    ALLOW_MOCK_PROVIDERS 控制；都不允许 → no_provider 终态失败。
    """
    from app.db.models import AssetCard
    from app.providers.tts_base import TTSProvider

    if await _should_abort_before_submit(session, job):
        return

    params = dict(job.params or {})
    quoted_cost = params.get("estimated_cost")
    usage_estimated_cost = None if params.get("quote_snapshot") else quoted_cost
    asset_id = params.get("asset_id")
    storage_key = (params.get("reference_audio_storage_key") or "").strip()
    voice_name = (params.get("voice_name") or "").strip()
    if not asset_id or not storage_key:
        await finalize_job(
            session,
            job,
            success=False,
            error_code="invalid_params",
            error="clone_voice 缺少 params.asset_id / reference_audio_storage_key",
        )
        return
    asset = await session.get(AssetCard, asset_id)
    if asset is None or asset.type != "voice":
        await finalize_job(
            session,
            job,
            success=False,
            error_code="invalid_params",
            error=f"voice 资产不存在或类型不符: {asset_id}",
        )
        return

    locked_provider = (job.provider or (params.get("quote_snapshot") or {}).get("provider") or "").strip()
    if locked_provider == "qwen_tts":
        deployment = _media_runtime.get("speech", locked_provider)
        if deployment is None or not deployment.provider.is_available():
            await finalize_job(
                session,
                job,
                success=False,
                error_code="provider_unavailable",
                error="报价锁定的 qwen_tts 当前不可用，请重新获取报价",
                usage_fields={
                    "operation": "clone_voice",
                    "status": "failed",
                    "estimated_cost": usage_estimated_cost,
                    "unit_type": "request",
                    "error_code": "provider_unavailable",
                },
            )
            return
        provider = deployment.provider
        mocked = False
    elif locked_provider == "mock_tts":
        if not mock_providers_enabled():
            await finalize_job(
                session,
                job,
                success=False,
                error_code="provider_unavailable",
                error="报价锁定的 mock_tts 当前不可用",
            )
            return
        provider, mocked = _media_runtime.resolve_speech(explicit_name="mock_tts")
    else:
        try:
            provider, mocked = _media_runtime.resolve_speech()
        except RuntimeError:
            await finalize_job(
                session,
                job,
                success=False,
                error_code="no_provider",
                error="无可用 TTS provider 且未开启 ALLOW_MOCK_PROVIDERS",
                usage_fields={
                    "operation": "clone_voice",
                    "status": "failed",
                    "estimated_cost": usage_estimated_cost,
                    "unit_type": "request",
                    "error_code": "no_provider",
                },
            )
            return

    job.provider = provider.name
    await _ensure_media_invocation(session, job, modality="speech", purpose="voice_clone")
    await session.flush()
    started = time.monotonic()
    # R5.4：使用绝对 STORAGE_DIR + is_relative_to() 做路径 confinement
    from app.services.asset_service import STORAGE_DIR as _STORAGE_DIR

    storage_root = _STORAGE_DIR.resolve()
    ref_path = (storage_root / storage_key).resolve()
    if not ref_path.is_relative_to(storage_root):
        await finalize_job(
            session,
            job,
            success=False,
            error_code="invalid_params",
            error="参考音频路径非法（超出 storage 目录范围）",
        )
        return
    result = await provider.clone_voice(f"/storage/{storage_key}", voice_name or asset.name)
    if not result.success:
        await finalize_job(
            session,
            job,
            success=False,
            error_code=result.error_code or "clone_failed",
            error=result.error_message,
            retryable=result.retryable,
            usage_fields={
                "operation": "clone_voice",
                "status": "failed",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "estimated_cost": usage_estimated_cost,
                "unit_type": "request",
                "error_code": result.error_code or "clone_failed",
            },
        )
        return

    voice_id = result.raw_metadata.get("voice_id", "")
    # 回写 voice 资产：音色标识 + 参考音频 + 状态 preview_ready（待用户试听确认）
    asset.provider_meta = {
        **(asset.provider_meta or {}),
        "voice_id": voice_id,
        "voice_name": result.raw_metadata.get("voice_name", voice_name or asset.name),
        "voice_model": result.raw_metadata.get("target_model", ""),
        "reference_audio": f"/storage/{storage_key}",
        "provider": provider.name,
        "mocked": mocked,
    }
    asset.status = "preview_ready"
    await session.flush()

    # 试听样本建档并绑定为资产的 preview 产物
    artifact_id = ""
    sample_key = ""
    if result.artifacts:
        sample_key = result.artifacts[0].get("storage_key", "")
        artifact = await asset_service.create_artifact(
            session,
            project_id=job.project_id,
            job_id=job.id,
            type="audio",
            storage_key=sample_key,
            mime_type="audio/wav",
            meta={"asset_id": asset_id, "voice_id": voice_id, "mocked": mocked},
        )
        artifact_id = artifact.id
        await asset_service.bind_artifact(session, asset_id, artifact.id, role="preview")
        asset.provider_meta = {
            **(asset.provider_meta or {}),
            "preview_artifact_id": artifact.id,
            "preview_url": asset_service.artifact_url(artifact),
        }

    await finalize_job(
        session,
        job,
        success=True,
        result={
            "provider": result.provider,
            "model": result.model,
            "voice_id": voice_id,
            "mocked": mocked,
        },
        artifact={
            "artifact_id": artifact_id,
            "storage_key": sample_key,
            "url": f"/api/artifacts/{artifact_id}/download" if artifact_id else "",
        },
        usage_fields={
            "operation": "clone_voice",
            "status": "success",
            "latency_ms": int((time.monotonic() - started) * 1000),
            "estimated_cost": usage_estimated_cost,
            "actual_cost": 0 if mocked else quoted_cost,
            "cost_source": "estimate",
            "output_units": 1,
            "unit_type": "request",
            "provider_request_id": result.external_request_id,
            "event_metadata": {
                "mocked": mocked,
                "voice_id": voice_id,
                "quote_id": (params.get("quote_snapshot") or {}).get("quote_id", ""),
                "price_table_version": params.get("price_table_version", PRICE_TABLE_VERSION),
            },
        },
    )


# operation -> executor 注册表
EXECUTORS: dict[str, Callable[[AsyncSession, GenerationJob], Awaitable[None]]] = {
    "generate_video": execute_generate_video,
    "generate_image": execute_generate_image,
    "render_episode": execute_render_episode,
    "tts": execute_tts,
    "clone_voice": execute_clone_voice,
}


async def _process_job(session: AsyncSession, job: GenerationJob) -> None:
    if job.provider and not await provider_circuit_service.allow_request(session, job.provider, job.model):
        await job_service.requeue_for_poll(session, job, delay_seconds=30)
        await session.commit()
        return
    executor = EXECUTORS.get(job.operation)
    if executor is None:
        await finalize_job(
            session,
            job,
            success=False,
            error_code="unsupported_operation",
            error=f"暂不支持的 operation: {job.operation}",
        )
    else:
        heartbeat_stop = asyncio.Event()

        async def keep_lease_alive() -> None:
            while not heartbeat_stop.is_set():
                try:
                    await asyncio.wait_for(heartbeat_stop.wait(), timeout=LEASE_SECONDS / 3)
                    return
                except TimeoutError:
                    async with SessionLocal() as heartbeat_session:
                        await job_service.heartbeat(
                            heartbeat_session,
                            job.id,
                            WORKER_ID,
                            lease_seconds=LEASE_SECONDS,
                        )
                        await heartbeat_session.commit()

        heartbeat_task = asyncio.create_task(keep_lease_alive())
        try:
            await executor(session, job)
        except Exception as e:
            logger.exception(f"job {job.id} ({job.operation}) 执行异常")
            await session.rollback()  # executor 可能把事务置于失败态，先回滚再标失败
            await finalize_job(
                session,
                job,
                success=False,
                error_code="executor_exception",
                error=str(e),
                retryable=True,
            )
        finally:
            heartbeat_stop.set()
            await heartbeat_task
    # service 层大多只 flush 不 commit，由 worker 按 job 粒度提交（不能用
    # session.begin() 包整个循环：executor 内部的 commit 会关闭事务，后续操作报
    # "Can't operate on closed transaction inside context manager"）。
    # finalize_job 内部已 commit，这里兜底一次（无事务时 commit 是 no-op）。
    await session.commit()


async def run_worker(poll_interval: float = 2.0, once: bool = False) -> None:
    """worker 主循环：recover 过期租约 → lease → 分发 executor。"""
    capabilities = {
        item.strip()
        for item in os.getenv("WORKER_CAPABILITIES", "video,image,audio,render").split(",")
        if item.strip()
    }
    operations = [
        operation for operation in SUPPORTED_OPERATIONS if OPERATION_WORKER_CLASS[operation] in capabilities
    ]
    logger.info(
        "generation worker 启动 (id=%s capabilities=%s poll_interval=%ss once=%s)",
        WORKER_ID,
        sorted(capabilities),
        poll_interval,
        once,
    )
    while True:
        try:
            async with SessionLocal() as session:
                # API 与 Worker 使用相同 Catalog 刷新路径；失败时 CatalogManager
                # 保留上一版快照，任务执行仍可继续使用已验证的配置。
                try:
                    from app.model_runtime.catalog import get_catalog

                    await get_catalog().refresh(session)
                except Exception:
                    logger.exception("model catalog 刷新失败，继续使用上一版快照")
                await job_service.recover_expired_leases(session)
                jobs = await job_service.lease_jobs(
                    session,
                    operations=operations,
                    limit=5,
                    lease_seconds=LEASE_SECONDS,
                    worker_id=WORKER_ID,
                    worker_classes=[*capabilities, "media"],
                )
                await session.commit()  # 租约落库后再执行
                for job in jobs:
                    await _process_job(session, job)
        except asyncio.CancelledError:
            logger.info("generation worker 收到取消，退出")
            raise
        except Exception:
            logger.exception("generation worker 主循环异常，稍后重试")
        if once:
            return
        await job_event_service.wait_for_work(poll_interval)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
