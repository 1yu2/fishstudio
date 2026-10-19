"""节点式无限画布 API。"""

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import User
from app.services import node_canvas_service as service

router = APIRouter(dependencies=[Depends(get_current_user)])


class CanvasCreate(BaseModel):
    title: str = Field(default="", max_length=255)
    aspect_ratio: str = "9:16"
    idea: str = Field(default="", max_length=20_000)


class CanvasPatch(BaseModel):
    title: Optional[str] = Field(default=None, max_length=255)
    aspect_ratio: Optional[str] = None


class GraphSave(BaseModel):
    graph: dict
    revision: int


class SplitRequest(BaseModel):
    idea: str = Field(min_length=1, max_length=20_000)
    shots: int = Field(default=4, ge=1, le=12)


async def _owned(session: AsyncSession, canvas_id: str, user: User):
    try:
        return await service.get_canvas(session, canvas_id, user)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _detail(canvas) -> dict:
    return {**service.canvas_summary(canvas), "graph": canvas.graph or {}, "revision": canvas.revision}


@router.get("/node-canvases")
async def list_canvases(session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    return [service.canvas_summary(canvas) for canvas in await service.list_canvases(session, user)]


@router.post("/node-canvases", status_code=status.HTTP_201_CREATED)
async def create_canvas(
    body: CanvasCreate, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    try:
        canvas = await service.create_canvas(
            session, user, title=body.title, aspect_ratio=body.aspect_ratio, idea=body.idea
        )
    except service.CanvasError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await session.commit()
    return _detail(canvas)


@router.get("/node-canvases/models")
async def available_models(session: AsyncSession = Depends(get_session)):
    return await service.available_models(session)


@router.get("/node-canvases/{canvas_id}")
async def get_canvas(
    canvas_id: str, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    return _detail(await _owned(session, canvas_id, user))


@router.patch("/node-canvases/{canvas_id}")
async def update_canvas(
    canvas_id: str,
    body: CanvasPatch,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    canvas = await _owned(session, canvas_id, user)
    try:
        await service.update_meta(session, canvas, title=body.title, aspect_ratio=body.aspect_ratio)
    except service.CanvasError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await session.commit()
    return service.canvas_summary(canvas)


@router.put("/node-canvases/{canvas_id}/graph")
async def save_graph(
    canvas_id: str,
    body: GraphSave,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    canvas = await _owned(session, canvas_id, user)
    try:
        await service.save_graph(session, canvas, body.graph, body.revision)
    except service.CanvasConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except service.CanvasError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await session.commit()
    return {"revision": canvas.revision}


@router.delete("/node-canvases/{canvas_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_canvas(
    canvas_id: str, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    canvas = await _owned(session, canvas_id, user)
    await service.delete_canvas(session, canvas)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/node-canvases/{canvas_id}/runs")
async def node_runs(
    canvas_id: str, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    return await service.node_runs(session, await _owned(session, canvas_id, user))


@router.post("/node-canvases/{canvas_id}/quote")
async def quote(
    canvas_id: str,
    body: service.NodeRunRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    canvas = await _owned(session, canvas_id, user)
    try:
        return await service.quote_node(session, canvas, body)
    except service.CanvasError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/node-canvases/{canvas_id}/nodes/{node_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_node(
    canvas_id: str,
    node_id: str,
    body: service.NodeRunRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    canvas = await _owned(session, canvas_id, user)
    try:
        job = await service.run_node(session, canvas, node_id, body)
    except service.CanvasError as error:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    await session.commit()
    return {"job_id": job.id, "status": job.status}


@router.post("/node-canvases/{canvas_id}/uploads", status_code=status.HTTP_201_CREATED)
async def upload(
    canvas_id: str,
    node_id: str = Form(...),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    canvas = await _owned(session, canvas_id, user)
    data = await file.read(service.UPLOAD_LIMITS["video"] + 1)
    try:
        artifact = await service.upload(
            session,
            canvas,
            node_id=node_id,
            filename=file.filename or "upload",
            content_type=file.content_type or "",
            data=data,
        )
    except service.CanvasError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await session.commit()
    from app.services import asset_service

    return {"artifact_id": artifact.id, "type": artifact.type, "url": asset_service.artifact_url(artifact)}


@router.post("/node-canvases/{canvas_id}/copilot/split")
async def copilot_split(
    canvas_id: str,
    body: SplitRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    canvas = await _owned(session, canvas_id, user)
    try:
        result = await service.copilot_split(canvas, idea=body.idea, shots=body.shots)
    except service.CanvasError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return result.model_dump()
