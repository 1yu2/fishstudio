"""用途绑定 CRUD 与 Catalog 保存期校验。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelRoute
from app.model_runtime.catalog import get_catalog
from app.model_runtime.routes import ModelRouteDTO, ModelRouteInput, validate_route


def to_dto(row: ModelRoute) -> ModelRouteDTO:
    return ModelRouteDTO(
        purpose=row.purpose,
        modality=row.modality,
        primary_deployment=row.primary_deployment,
        fallback_deployments=list(row.fallback_deployments),
        enabled=row.enabled,
        revision=row.revision,
    )


async def list_routes(session: AsyncSession) -> list[ModelRoute]:
    result = await session.execute(select(ModelRoute).order_by(ModelRoute.purpose))
    return list(result.scalars().all())


async def get_route(session: AsyncSession, purpose: str) -> ModelRoute | None:
    return await session.get(ModelRoute, purpose)


async def save_route(session: AsyncSession, payload: ModelRouteInput) -> ModelRoute:
    snapshot = await get_catalog().refresh(session)
    validate_route(payload, snapshot)
    row = await get_route(session, payload.purpose)
    if row is None:
        row = ModelRoute(**payload.model_dump(), revision=1)
        session.add(row)
    else:
        for key, value in payload.model_dump().items():
            setattr(row, key, value)
        row.revision += 1
    await session.flush()
    await session.refresh(row)
    return row
