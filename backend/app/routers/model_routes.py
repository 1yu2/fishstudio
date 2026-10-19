"""管理员用途绑定 API。"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.db.database import get_session
from app.db.models import AuditLog, User
from app.model_runtime.routes import DEFAULT_TIER, PURPOSES, TIERS, ModelRouteDTO, ModelRouteInput
from app.services import model_route_service as service

router = APIRouter(dependencies=[Depends(require_admin)])


async def _refresh_catalog(session: AsyncSession) -> None:
    """让对话模型等同步调用方立即读到新的默认模型。"""
    from app.model_runtime.catalog import get_catalog

    try:
        await get_catalog().refresh(session)
    except Exception:  # noqa: BLE001 - 路由已保存；Catalog 保留旧快照并暴露失败状态
        pass


def _check_tier(tier: str) -> str:
    if tier not in TIERS:
        raise HTTPException(status_code=422, detail=f"未知档位: {tier}")
    return tier


@router.get("/tiers")
async def list_tiers() -> list[str]:
    return list(TIERS)


@router.get("/purposes")
async def list_purposes() -> list[dict]:
    return [
        {"purpose": purpose, "modality": definition[0], "required_capabilities": sorted(definition[1])}
        for purpose, definition in PURPOSES.items()
    ]


@router.get("", response_model=list[ModelRouteDTO])
async def list_routes(session: AsyncSession = Depends(get_session)):
    return [service.to_dto(row) for row in await service.list_routes(session)]


@router.put("/{purpose}", response_model=ModelRouteDTO)
async def save_route(
    purpose: str,
    body: ModelRouteInput,
    tier: str | None = Query(default=None, description="可选；提供时必须与请求体 tier 一致"),
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    if purpose != body.purpose:
        raise HTTPException(status_code=422, detail="路径 purpose 与请求体不一致")
    if tier is not None and _check_tier(tier) != body.tier:
        raise HTTPException(status_code=422, detail="查询参数 tier 与请求体不一致")
    tier = body.tier
    before_row = await service.get_route(session, purpose, tier)
    before = service.to_dto(before_row).model_dump(mode="json") if before_row else None
    try:
        row = await service.save_route(session, body)
    except ValueError as error:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    dto = service.to_dto(row)
    session.add(
        AuditLog(
            user_id=admin.id,
            username=admin.username,
            action="model_route.save",
            target=f"{purpose}:{tier}",
            before=before,
            after=dto.model_dump(mode="json"),
        )
    )
    await session.commit()
    await _refresh_catalog(session)
    return dto


@router.delete("/{purpose}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_route(
    purpose: str,
    tier: str = Query(default=DEFAULT_TIER),
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    row = await service.get_route(session, purpose, _check_tier(tier))
    if row is None:
        raise HTTPException(status_code=404, detail="用途绑定不存在")
    before = service.to_dto(row).model_dump(mode="json")
    await session.delete(row)
    session.add(
        AuditLog(
            user_id=admin.id,
            username=admin.username,
            action="model_route.delete",
            target=f"{purpose}:{tier}",
            before=before,
        )
    )
    await session.commit()
    await _refresh_catalog(session)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
