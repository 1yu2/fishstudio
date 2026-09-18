"""Model Runtime 管理面：脱敏目录与调用审计。"""
from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.db.database import get_session
from app.db.models import ModelInvocation, User
from app.model_runtime import get_model_runtime
from app.model_runtime.media import get_media_runtime

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/model-runtime/deployments")
async def list_deployments(admin: User = Depends(require_admin)):
    runtime = get_model_runtime()
    media = get_media_runtime()
    media.video_router()  # 确保内置视频 Adapter 已完成注册。
    chat = [
        {
            "name": item.name,
            "provider": item.provider,
            "model": item.model,
            "modality": "chat",
            "enabled": item.enabled,
            "capabilities": asdict(item.capabilities),
            "scores": {
                "quality": item.quality_score,
                "cost": item.cost_score,
                "latency": item.latency_score,
            },
        }
        for item in runtime.list_chat()
    ]
    media_items = [
        {
            "name": item.name,
            "provider": item.name,
            "model": "",
            "modality": item.modality,
            "enabled": bool(item.provider.is_available()) if hasattr(item.provider, "is_available") else True,
            "mocked": item.mocked,
        }
        for item in media.list()
    ]
    return {"items": chat + media_items, "secret_fields_exposed": False}


@router.get("/model-runtime/invocations")
async def list_invocations(
    status: Optional[str] = None,
    modality: Optional[str] = None,
    project_id: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
):
    stmt = select(ModelInvocation).order_by(ModelInvocation.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(ModelInvocation.status == status)
    if modality:
        stmt = stmt.where(ModelInvocation.modality == modality)
    if project_id:
        stmt = stmt.where(ModelInvocation.project_id == project_id)
    rows = (await session.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": row.id,
                "request_id": row.request_id,
                "project_id": row.project_id,
                "workflow_run_id": row.workflow_run_id,
                "workflow_step_id": row.workflow_step_id,
                "job_id": row.job_id,
                "purpose": row.purpose,
                "modality": row.modality,
                "deployment_name": row.deployment_name,
                "provider": row.provider,
                "model": row.model,
                "status": row.status,
                "attempt": row.attempt,
                "external_request_id": row.external_request_id,
                "latency_ms": row.latency_ms,
                "error_code": row.error_code,
                "routing_snapshot": row.routing_snapshot,
                "result_metadata": row.result_metadata,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    }
