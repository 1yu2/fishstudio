"""管理员用途绑定 API。"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_admin
from app.db.database import get_session
from app.db.models import AuditLog, User
from app.model_runtime.routes import PURPOSES, ModelRouteDTO, ModelRouteInput
from app.services import model_route_service as service

router = APIRouter(dependencies=[Depends(require_admin)])


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
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    if purpose != body.purpose:
        raise HTTPException(status_code=422, detail="路径 purpose 与请求体不一致")
    before_row = await service.get_route(session, purpose)
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
            target=purpose,
            before=before,
            after=dto.model_dump(mode="json"),
        )
    )
    await session.commit()
    return dto


@router.delete("/{purpose}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_route(
    purpose: str,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    row = await service.get_route(session, purpose)
    if row is None:
        raise HTTPException(status_code=404, detail="用途绑定不存在")
    before = service.to_dto(row).model_dump(mode="json")
    await session.delete(row)
    session.add(
        AuditLog(
            user_id=admin.id,
            username=admin.username,
            action="model_route.delete",
            target=purpose,
            before=before,
        )
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
