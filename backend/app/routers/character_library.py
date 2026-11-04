"""角色库 API：个人角色资产（上传 / 列表 / 改名 / 删除），以及项目角色与角色库互转。

库角色只允许本人访问（别人的与不存在的一视同仁 404）；画布接口只允许画布 owner。
"""

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import NodeCanvas, User
from app.services import character_library_service as service
from app.services import node_canvas_service as canvas_service

router = APIRouter(dependencies=[Depends(get_current_user)])

CANVAS_NOT_FOUND = "项目不存在或无权访问"
CHARACTER_NOT_FOUND = "角色不存在"


class CharacterPatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)


class SaveToLibrary(BaseModel):
    character: str = Field(min_length=1, max_length=255)


class FromLibrary(BaseModel):
    asset_id: str
    base_revision: int


async def _owned_canvas(session: AsyncSession, canvas_id: str, user: User) -> NodeCanvas:
    """只允许画布 owner（管理员也只看自己的），否则统一 404。"""
    canvas = await session.get(NodeCanvas, canvas_id)
    if canvas is None or canvas.owner_id != user.id:
        raise HTTPException(status_code=404, detail=CANVAS_NOT_FOUND)
    return canvas


@router.get("/library/characters")
async def list_characters(
    canvas_id: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    canvas = await _owned_canvas(session, canvas_id, user) if canvas_id else None
    return await service.list_library(session, user, canvas)


@router.post("/library/characters", status_code=status.HTTP_201_CREATED)
async def upload_character(
    name: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    data = await file.read(service.LIBRARY_UPLOAD_LIMIT + 1)
    try:
        return await service.upload_character(
            session,
            user,
            name=name,
            description=description,
            filename=file.filename or "upload",
            content_type=file.content_type or "",
            data=data,
        )
    except service.UnsupportedImage as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except service.LibraryError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.patch("/library/characters/{asset_id}")
async def update_character(
    asset_id: str,
    body: CharacterPatch,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        return await service.update_character(
            session, user, asset_id, name=body.name, description=body.description
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=CHARACTER_NOT_FOUND) from error
    except service.LibraryError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.delete("/library/characters/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_character(
    asset_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        await service.delete_character(session, user, asset_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=CHARACTER_NOT_FOUND) from error


@router.post("/workspace/projects/{canvas_id}/characters/save-to-library")
async def save_to_library(
    canvas_id: str,
    body: SaveToLibrary,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    canvas = await _owned_canvas(session, canvas_id, user)
    try:
        return await service.save_to_library(session, user, canvas, body.character)
    except service.LibraryError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/workspace/projects/{canvas_id}/characters/from-library")
async def add_from_library(
    canvas_id: str,
    body: FromLibrary,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    canvas = await _owned_canvas(session, canvas_id, user)
    try:
        result = await service.add_from_library(session, user, canvas, body.asset_id, body.base_revision)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=CHARACTER_NOT_FOUND) from error
    except canvas_service.CanvasConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (service.LibraryError, canvas_service.CanvasError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await session.commit()
    return result
