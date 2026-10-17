"""Model Runtime 管理面：统一 Catalog、健康状态与调用审计。"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.db.database import get_session
from app.db.models import ModelInvocation, User
from app.model_runtime.catalog import CatalogDeployment, get_catalog

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/model-runtime/deployments")
async def list_deployments(
    session: AsyncSession = Depends(get_session), admin: User = Depends(require_admin)
):
    catalog = get_catalog()
    try:
        snapshot = await catalog.refresh(session)
    except Exception:
        snapshot = catalog.snapshot
    return {
        "items": [_catalog_dto(item) for item in snapshot.list()],
        "catalog_revision": snapshot.catalog_revision,
        "loaded_at": snapshot.loaded_at,
        "last_reload_failure": _failure_dto(catalog.last_failure),
        "secret_fields_exposed": False,
    }


def _catalog_dto(item: CatalogDeployment) -> dict:
    available = getattr(item.instance, "is_available", None)
    return {
        "id": item.id,
        "name": item.name,
        "slug": item.slug,
        "provider": item.driver,
        "driver": item.driver,
        "model": item.model,
        "modality": item.modality,
        "enabled": item.enabled,
        "available": bool(available()) if available else item.enabled,
        "priority": item.priority,
        "revision": item.revision,
        "capabilities": dict(item.capabilities),
        "pricing": dict(item.pricing),
    }


def _failure_dto(failure) -> dict | None:
    if failure is None:
        return None
    return {
        "reason": failure.reason,
        "attempted_revision": failure.attempted_revision,
        "failed_at": failure.failed_at,
    }


@router.post("/model-runtime/reload")
async def reload_catalog(
    probe: bool = False,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
):
    try:
        snapshot = await get_catalog().refresh(session, probe=probe)
    except Exception as error:
        raise HTTPException(status_code=409, detail=f"Catalog reload 失败: {error}") from error
    return {
        "catalog_revision": snapshot.catalog_revision,
        "loaded_at": snapshot.loaded_at,
        "deployment_count": len(snapshot.deployments),
    }


@router.get("/model-runtime/deployments/{deployment_name}/health")
async def deployment_health(
    deployment_name: str, admin: User = Depends(require_admin),
):
    """主动探测媒体 Deployment；用于部署分层联调，不返回密钥。"""
    item = get_catalog().snapshot.get(deployment_name)
    if item is None:
        raise HTTPException(status_code=404, detail="Deployment 不存在")
    try:
        payload = await get_catalog().registry.probe(item.driver, item.instance)
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail={"deployment": deployment_name, "available": False, "error": str(error)},
        )
    return {
        "deployment": deployment_name, "available": True,
        "active_probe": payload.get("active_probe", False), "details": payload,
    }


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
