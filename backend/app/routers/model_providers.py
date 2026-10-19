"""供应商管理 API（仅管理员）：供应商 → 模型 两级配置。

模型自身的编辑 / 启停 / 删除仍走 /api/model-deployments/{slug}；地址与密钥只能在这里改。
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.db.database import get_session
from app.db.models import AuditLog, ModelProvider, User
from app.model_runtime.provider_types import CAPABILITIES, PROVIDER_TYPES
from app.services import model_provider_service as service

router = APIRouter(dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)


class ProviderCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=64, description="Provider ID")
    name: str = Field(default="", max_length=128)
    provider_type: str = Field(min_length=1, max_length=64)
    base_url: str = Field(default="", max_length=2048)
    api_key_env: str = Field(default="", max_length=128)
    api_key: str = Field(default="", max_length=8192)
    models_endpoint: Optional[str] = Field(default=None, max_length=255)
    capabilities: Optional[list[str]] = None


class ProviderPatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128)
    base_url: Optional[str] = Field(default=None, max_length=2048)
    api_key_env: Optional[str] = Field(default=None, max_length=128)
    models_endpoint: Optional[str] = Field(default=None, max_length=255)
    capabilities: Optional[list[str]] = None
    enabled: Optional[bool] = None


class CredentialWrite(BaseModel):
    api_key: str = Field(min_length=1, max_length=8192)


class ModelCreate(BaseModel):
    capability: str = Field(min_length=1, max_length=32)
    model: str = Field(min_length=1, max_length=255)
    name: str = Field(default="", max_length=128)
    config: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    pricing: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


async def _reload_catalog(session: AsyncSession, *, force: bool = False) -> dict[str, bool]:
    from app.model_runtime.catalog import get_catalog

    try:
        snapshot = await get_catalog().refresh(session, force=force)
    except Exception:
        logger.exception("供应商已保存，但 Catalog 热加载失败")
        snapshot = get_catalog().snapshot
    return _availability(snapshot)


def _availability(snapshot) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for item in snapshot.list():
        check = getattr(item.instance, "is_available", None)
        enabled = bool(getattr(item.instance, "enabled", item.enabled))
        result[item.slug] = bool(check()) if callable(check) else enabled
    return result


async def _owned(session: AsyncSession, slug: str) -> ModelProvider:
    provider = await service.get_provider(session, slug)
    if provider is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    return provider


async def _dto(session: AsyncSession, provider: ModelProvider, availability: dict[str, bool]) -> dict:
    return service.provider_dto(provider, await service.models_of(session, provider), availability)


def _audit(admin: User, action: str, target: str, before=None, after=None) -> AuditLog:
    return AuditLog(
        user_id=admin.id, username=admin.username, action=action, target=target, before=before, after=after
    )


@router.get("/types")
async def list_types() -> dict:
    return {"capabilities": list(CAPABILITIES), "types": [item.summary() for item in PROVIDER_TYPES.values()]}


@router.get("")
async def list_providers(session: AsyncSession = Depends(get_session)) -> list[dict]:
    from app.model_runtime.catalog import get_catalog

    availability = _availability(get_catalog().snapshot)
    return [await _dto(session, provider, availability) for provider in await service.list_providers(session)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_provider(
    body: ProviderCreate, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> dict:
    try:
        provider = await service.create_provider(
            session,
            slug=body.slug,
            name=body.name,
            provider_type=body.provider_type,
            base_url=body.base_url,
            api_key_env=body.api_key_env,
            models_endpoint=body.models_endpoint,
            capabilities=body.capabilities,
        )
        if body.api_key.strip():
            from app.services.model_credential_service import write_env_credential

            write_env_credential(provider.credential_ref, body.api_key)
    except ValueError as error:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    session.add(
        _audit(admin, "model_provider.create", provider.slug, after={"provider_type": provider.provider_type})
    )
    await session.commit()
    return await _dto(session, provider, await _reload_catalog(session))


@router.patch("/{slug}")
async def update_provider(
    slug: str,
    body: ProviderPatch,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    provider = await _owned(session, slug)
    before = {"name": provider.name, "base_url": provider.base_url, "enabled": provider.enabled}
    try:
        provider = await service.update_provider(session, provider, **body.model_dump(exclude_unset=True))
    except ValueError as error:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    after = {"name": provider.name, "base_url": provider.base_url, "enabled": provider.enabled}
    session.add(_audit(admin, "model_provider.update", slug, before, after))
    await session.commit()
    return await _dto(session, provider, await _reload_catalog(session))


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    slug: str, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)
) -> Response:
    provider = await _owned(session, slug)
    try:
        await service.delete_provider(session, provider)
    except service.ProviderError as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    session.add(_audit(admin, "model_provider.delete", slug))
    await session.commit()
    await _reload_catalog(session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{slug}/credential", status_code=status.HTTP_204_NO_CONTENT)
async def write_credential(
    slug: str,
    body: CredentialWrite,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """密钥只写入，不回显；该供应商下所有模型立即生效。"""
    provider = await _owned(session, slug)
    from app.services.model_credential_service import write_env_credential

    try:
        write_env_credential(provider.credential_ref, body.api_key)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    session.add(_audit(admin, "model_provider.credential.update", slug, after={"configured": True}))
    await session.commit()
    await _reload_catalog(session, force=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{slug}/check")
async def check_provider(slug: str, session: AsyncSession = Depends(get_session)) -> dict:
    provider = await _owned(session, slug)
    return await service.check_provider(provider, await service.models_of(session, provider))


@router.get("/{slug}/remote-models")
async def remote_models(slug: str, session: AsyncSession = Depends(get_session)) -> list[dict]:
    provider = await _owned(session, slug)
    try:
        return await service.remote_models(provider, await service.models_of(session, provider))
    except service.ProviderError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/{slug}/models", status_code=status.HTTP_201_CREATED)
async def add_model(
    slug: str,
    body: ModelCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    provider = await _owned(session, slug)
    try:
        row = await service.add_model(session, provider, **body.model_dump())
    except ValueError as error:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    session.add(
        _audit(admin, "model_provider.model.create", row.slug, after={"provider": slug, "model": row.model})
    )
    await session.commit()
    return await _dto(session, provider, await _reload_catalog(session))
