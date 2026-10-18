"""Model Runtime 调用生命周期账本。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import SessionLocal
from app.db.models import ModelInvocation

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def begin(
    session: AsyncSession,
    *,
    request_id: str,
    purpose: str,
    modality: str,
    deployment_name: str,
    provider: str,
    model: str,
    routing_snapshot: dict[str, Any],
    project_id: Optional[str] = None,
    workflow_run_id: Optional[str] = None,
    workflow_step_id: Optional[str] = None,
    job_id: Optional[str] = None,
) -> ModelInvocation:
    invocation = ModelInvocation(
        request_id=request_id,
        purpose=purpose,
        modality=modality,
        deployment_name=deployment_name,
        provider=provider,
        model=model,
        routing_snapshot=routing_snapshot,
        project_id=project_id,
        workflow_run_id=workflow_run_id,
        workflow_step_id=workflow_step_id,
        job_id=job_id,
        status="running",
        started_at=_utcnow(),
    )
    session.add(invocation)
    await session.flush()
    return invocation


async def finish(
    session: AsyncSession,
    invocation: ModelInvocation,
    *,
    status: str,
    latency_ms: int,
    external_request_id: str = "",
    error_code: str = "",
    error: str = "",
    result_metadata: Optional[dict[str, Any]] = None,
) -> ModelInvocation:
    if invocation.status in {"succeeded", "failed", "canceled", "timed_out"}:
        return invocation
    invocation.status = status
    invocation.latency_ms = max(0, latency_ms)
    invocation.external_request_id = external_request_id
    invocation.error_code = error_code
    invocation.error = error[:1000]
    invocation.result_metadata = result_metadata or {}
    invocation.finished_at = _utcnow()
    await session.flush()
    return invocation


async def finish_by_id(
    session: AsyncSession, invocation_id: Optional[str], **fields: Any
) -> Optional[ModelInvocation]:
    if not invocation_id:
        return None
    invocation = await session.get(ModelInvocation, invocation_id)
    if invocation is None:
        return None
    return await finish(session, invocation, **fields)


async def begin_managed(**fields: Any) -> Optional[str]:
    """独立短事务开始调用；审计失败不得阻断模型主调用。"""
    try:
        async with SessionLocal() as session:
            invocation = await begin(session, **fields)
            await session.commit()
            return invocation.id
    except Exception as exc:
        logger.warning("创建 ModelInvocation 失败（已忽略）: %s", exc)
        return None


async def finish_managed(invocation_id: Optional[str], **fields: Any) -> None:
    if not invocation_id:
        return
    try:
        async with SessionLocal() as session:
            invocation = await session.get(ModelInvocation, invocation_id)
            if invocation is not None:
                await finish(session, invocation, **fields)
                await session.commit()
    except Exception as exc:
        logger.warning("完成 ModelInvocation 失败（已忽略）: %s", exc)
