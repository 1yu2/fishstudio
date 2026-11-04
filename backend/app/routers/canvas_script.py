"""画布剧本 API：读取 / 保存（带版本合并）/ 恢复历史 / 文本导入 / AI 起草与改写建议。

全部只允许画布 owner；别人的与不存在的画布一律 404。起草 / 导入 / 改写只返回结果，不保存。
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import NodeCanvas, User
from app.services import canvas_script_service as service

router = APIRouter(dependencies=[Depends(get_current_user)])


class ScriptSave(BaseModel):
    payload: service.ScriptPayload
    based_on: Optional[int] = None
    # 这一版当前的 edit_seq；合并覆盖不改版本号，靠它发现别处的覆盖
    based_on_seq: Optional[int] = None
    # 本次保存的客户端标识；keepalive 写入用 supersedes 指向在途保存的标识
    save_id: Optional[str] = Field(default=None, max_length=64)
    supersedes: Optional[str] = Field(default=None, max_length=64)
    # 前端确认 AI 起草 / 导入结果后保存时标明来源；普通编辑保持 user
    source: Literal["user", "ai_draft", "import"] = "user"


class ScriptRestore(BaseModel):
    revision: int = Field(ge=1)


class ScriptImport(BaseModel):
    text: str = Field(max_length=service.MAX_IMPORT_CHARS)


class ScriptDraft(BaseModel):
    idea: str = Field(min_length=1, max_length=20_000)
    scenes: int = Field(default=4, ge=2, le=8)


class ScriptRewrite(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    mode: Literal["rewrite", "shorten", "tone"]
    tone: Optional[str] = Field(default=None, max_length=20)
    context: Optional[str] = Field(default=None, max_length=2000)


async def _owned(session: AsyncSession, canvas_id: str, user: User) -> NodeCanvas:
    try:
        return await service.get_owned_canvas(session, canvas_id, user)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _llm_http(error: service.ScriptError) -> HTTPException:
    if isinstance(error, service.ScriptLLMError):
        return HTTPException(status_code=502, detail=str(error))
    return HTTPException(status_code=422, detail=str(error))


@router.get("/workspace/projects/{canvas_id}/script")
async def get_script(
    canvas_id: str, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    return await service.get_script(session, await _owned(session, canvas_id, user))


@router.get("/workspace/projects/{canvas_id}/script/revisions/{revision}")
async def get_revision(
    canvas_id: str,
    revision: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    canvas = await _owned(session, canvas_id, user)
    try:
        return await service.get_revision(session, canvas, revision)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.put("/workspace/projects/{canvas_id}/script")
async def save_script(
    canvas_id: str,
    body: ScriptSave,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    canvas = await _owned(session, canvas_id, user)
    try:
        current = await service.save_script(
            session,
            canvas,
            user,
            body.payload,
            based_on=body.based_on,
            based_on_seq=body.based_on_seq,
            source=body.source,
            save_id=body.save_id,
            supersedes=body.supersedes,
        )
    except service.ScriptConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except service.ScriptError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    await session.commit()
    return current


@router.post("/workspace/projects/{canvas_id}/script/restore")
async def restore_script(
    canvas_id: str,
    body: ScriptRestore,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    canvas = await _owned(session, canvas_id, user)
    try:
        current = await service.restore_script(session, canvas, user, body.revision)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    await session.commit()
    return current


@router.post("/workspace/projects/{canvas_id}/script/import")
async def import_script(
    canvas_id: str,
    body: ScriptImport,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await _owned(session, canvas_id, user)
    try:
        return {"payload": service.parse_text(body.text)}
    except service.ScriptError as error:
        raise _llm_http(error) from error


@router.post("/workspace/projects/{canvas_id}/script/draft")
async def draft_script(
    canvas_id: str,
    body: ScriptDraft,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    canvas = await _owned(session, canvas_id, user)
    try:
        return {"payload": await service.draft_script(canvas, idea=body.idea, scenes=body.scenes)}
    except service.ScriptError as error:
        raise _llm_http(error) from error


@router.post("/workspace/projects/{canvas_id}/script/rewrite")
async def rewrite_script(
    canvas_id: str,
    body: ScriptRewrite,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await _owned(session, canvas_id, user)
    try:
        return await service.rewrite_text(
            text=body.text, mode=body.mode, tone=body.tone or "", context=body.context or ""
        )
    except service.ScriptError as error:
        raise _llm_http(error) from error
