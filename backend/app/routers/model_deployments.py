"""统一 Model Deployment 管理 API（仅管理员）。"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.db.database import get_session
from app.db.models import AuditLog, ModelRoute, User
from app.model_runtime.deployment_config import (
    DeploymentDTO,
    DeploymentEnable,
    DeploymentInput,
    DeploymentPatch,
    driver_summaries,
)
from app.services import model_deployment_service as service

router = APIRouter(dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)


class CredentialWrite(BaseModel):
    api_key: str = Field(min_length=1, max_length=8192)


async def _reload_catalog(session: AsyncSession, *, force: bool = False) -> None:
    try:
        from app.model_runtime.catalog import get_catalog

        await get_catalog().refresh(session, force=force)
    except Exception:
        # DB 配置已保存；CatalogManager 会保留旧快照并暴露失败状态。
        logger.exception("Deployment 已保存，但 Catalog 热加载失败")


def _audit(admin: User, action: str, target: str, before=None, after=None) -> AuditLog:
    return AuditLog(
        user_id=admin.id,
        username=admin.username,
        action=action,
        target=target,
        before=before,
        after=after,
    )


@router.get("/drivers")
async def list_drivers() -> list[dict]:
    return driver_summaries()


@router.post("/validate")
async def validate_deployment(body: DeploymentInput) -> dict:
    return {"valid": True, "normalized": body.model_dump(mode="json")}


@router.get("", response_model=list[DeploymentDTO])
async def list_deployments(
    session: AsyncSession = Depends(get_session),
) -> list[DeploymentDTO]:
    return [service.to_dto(row) for row in await service.list_deployments(session)]


@router.post("", response_model=DeploymentDTO, status_code=status.HTTP_201_CREATED)
async def create_deployment(
    body: DeploymentInput,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DeploymentDTO:
    try:
        row = await service.create_deployment(session, body)
        # 旧接口直接创建的模型同样归入供应商（按地址 + 密钥引用）。
        from app.services.model_provider_service import assign_unowned_deployments

        await assign_unowned_deployments(session)
        dto = service.to_dto(row)
        session.add(_audit(admin, "model_deployment.create", row.slug, after=dto.model_dump(mode="json")))
        await session.commit()
        await _reload_catalog(session)
        return dto
    except (ValueError, IntegrityError) as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/{slug}", response_model=DeploymentDTO)
async def get_deployment(slug: str, session: AsyncSession = Depends(get_session)) -> DeploymentDTO:
    row = await service.get_deployment(session, slug)
    if row is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return service.to_dto(row)


@router.patch("/{slug}", response_model=DeploymentDTO)
async def update_deployment(
    slug: str,
    body: DeploymentPatch,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DeploymentDTO:
    row = await service.get_deployment(session, slug)
    if row is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    if row.provider_id and (body.base_url is not None or body.credential_ref is not None):
        raise HTTPException(status_code=409, detail="地址和密钥由供应商统一管理，请在供应商设置中修改")
    before = service.to_dto(row).model_dump(mode="json")
    try:
        row = await service.update_deployment(session, row, body)
        dto = service.to_dto(row)
        session.add(_audit(admin, "model_deployment.update", row.slug, before, dto.model_dump(mode="json")))
        await session.commit()
        await _reload_catalog(session)
        return dto
    except (ValueError, IntegrityError) as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/{slug}/enabled", response_model=DeploymentDTO)
async def set_enabled(
    slug: str,
    body: DeploymentEnable,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DeploymentDTO:
    return await update_deployment(slug, DeploymentPatch(enabled=body.enabled), admin, session)


@router.put("/{slug}/credential", status_code=status.HTTP_204_NO_CONTENT)
async def write_credential(
    slug: str,
    body: CredentialWrite,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    row = await service.get_deployment(session, slug)
    if row is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    try:
        from app.services.model_credential_service import write_env_credential

        write_env_credential(row.credential_ref, body.api_key)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    session.add(
        _audit(
            admin,
            "model_deployment.credential.update",
            slug,
            before={"configured": bool(row.credential_ref)},
            after={"configured": True},
        )
    )
    await session.commit()
    await _reload_catalog(session, force=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_deployment(
    slug: str,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    row = await service.get_deployment(session, slug)
    if row is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    references = (
        (
            await session.execute(
                select(ModelRoute.purpose).where(
                    or_(
                        ModelRoute.primary_deployment == slug,
                        ModelRoute.fallback_deployments.contains([slug]),
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    if references:
        raise HTTPException(
            status_code=409,
            detail=f"Deployment 仍被用途绑定引用：{'、'.join(references)}",
        )
    before = service.to_dto(row).model_dump(mode="json")
    await session.delete(row)
    session.add(_audit(admin, "model_deployment.delete", slug, before=before))
    await session.commit()
    await _reload_catalog(session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
