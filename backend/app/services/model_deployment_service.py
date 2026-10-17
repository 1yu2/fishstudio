"""Model Deployment CRUD；统一校验、哈希和脱敏 DTO。"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelDeployment
from app.model_runtime.deployment_config import (
    DeploymentDTO,
    DeploymentInput,
    DeploymentPatch,
    config_hash,
)


def to_dto(row: ModelDeployment) -> DeploymentDTO:
    if row.credential_ref.startswith("env:"):
        credential_configured = bool(os.getenv(row.credential_ref.split(":", 1)[1], "").strip())
    else:
        credential_configured = bool(row.credential_ref)
    return DeploymentDTO(
        id=row.id,
        name=row.name,
        slug=row.slug,
        driver=row.driver,
        modality=row.modality,
        model=row.model,
        base_url=row.base_url,
        credential_configured=credential_configured,
        enabled=row.enabled,
        priority=row.priority,
        config=row.config,
        capabilities=row.capabilities,
        pricing=row.pricing,
        revision=row.revision,
        config_hash=row.config_hash,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def list_deployments(session: AsyncSession) -> list[ModelDeployment]:
    result = await session.execute(
        select(ModelDeployment).order_by(
            ModelDeployment.modality, ModelDeployment.priority, ModelDeployment.slug
        )
    )
    return list(result.scalars().all())


async def get_deployment(session: AsyncSession, slug: str) -> ModelDeployment | None:
    result = await session.execute(
        select(ModelDeployment).where(ModelDeployment.slug == slug)
    )
    return result.scalar_one_or_none()


async def create_deployment(session: AsyncSession, payload: DeploymentInput) -> ModelDeployment:
    if await get_deployment(session, payload.slug):
        raise ValueError(f"Deployment slug 已存在: {payload.slug}")
    values = payload.model_dump()
    row = ModelDeployment(**values, revision=1, config_hash=config_hash(payload))
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def update_deployment(
    session: AsyncSession, row: ModelDeployment, patch: DeploymentPatch
) -> ModelDeployment:
    current: dict[str, Any] = {
        key: getattr(row, key)
        for key in DeploymentInput.model_fields
    }
    current.update(patch.model_dump(exclude_unset=True, exclude_none=True))
    payload = DeploymentInput.model_validate(current)
    if payload.slug != row.slug:
        existing = await get_deployment(session, payload.slug)
        if existing is not None and existing.id != row.id:
            raise ValueError(f"Deployment slug 已存在: {payload.slug}")
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    row.revision += 1
    row.config_hash = config_hash(payload)
    await session.flush()
    await session.refresh(row)
    return row
