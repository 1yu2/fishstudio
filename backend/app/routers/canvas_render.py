"""画布成片 API：成片页数据、建成片节点、渲染、BGM 上传。全部只允许画布 owner，其余一律 404。"""

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import User
from app.services import canvas_render_service as service
from app.services import node_canvas_service as canvas_service

router = APIRouter(dependencies=[Depends(get_current_user)])


class EnsureNode(BaseModel):
    base_revision: int


class RenderRequest(BaseModel):
    base_revision: int
    client_request_id: Optional[str] = Field(default=None, min_length=1, max_length=64)


async def _owned(session: AsyncSession, canvas_id: str, user: User):
    try:
        return await canvas_service.get_canvas(session, canvas_id, user)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/node-canvases/{canvas_id}/final")
async def get_final(
    canvas_id: str, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    return await service.final_state(session, await _owned(session, canvas_id, user))


@router.post("/node-canvases/{canvas_id}/final/ensure-node")
async def ensure_node(
    canvas_id: str,
    body: EnsureNode,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    canvas = await _owned(session, canvas_id, user)
    try:
        result = await service.ensure_episode_node(session, canvas, body.base_revision)
    except canvas_service.CanvasConflict as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    await session.commit()
    return result


@router.post("/node-canvases/{canvas_id}/final/render", status_code=status.HTTP_202_ACCEPTED)
async def render_final(
    canvas_id: str,
    body: RenderRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    canvas = await _owned(session, canvas_id, user)
    try:
        job = await service.start_render(
            session, canvas, base_revision=body.base_revision, client_request_id=body.client_request_id
        )
    except canvas_service.CanvasConflict as error:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except service.RenderBlocked as error:
        await session.rollback()
        # 带结构化 problems，页面据此给出“去分镜”等入口；HTTPException 的 detail 会被压成字符串，所以直接返回响应
        return JSONResponse(
            status_code=422,
            content={
                "error": {"code": "unprocessable_entity", "message": str(error)},
                "problems": error.problems,
            },
        )
    except canvas_service.CanvasError as error:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    await session.commit()
    return {"job_id": job.id, "status": job.status}


@router.post("/node-canvases/{canvas_id}/bgm", status_code=status.HTTP_201_CREATED)
async def upload_bgm(
    canvas_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    canvas = await _owned(session, canvas_id, user)
    data = await file.read(service.BGM_MAX_BYTES + 1)
    try:
        return await service.upload_bgm(session, canvas, filename=file.filename or "", data=data)
    except canvas_service.CanvasError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
