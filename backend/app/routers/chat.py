"""
聊天路由 - 处理对话请求
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import SeriesProject, User
from app.services import asset_service, upload_guard
from app.services.agent_service import process_chat_stream
from app.services.connection_manager import canvas_channel, manager
from app.services.execution_context import (
    ExecutionContext,
    get_execution_context,
    reset_execution_context,
    set_execution_context,
)
from app.services.history_service import history_service

router = APIRouter(dependencies=[Depends(get_current_user)])


class ChatRequest(BaseModel):
    message: str
    messages: Optional[List[Dict[str, Any]]] = []
    session_id: Optional[str] = None
    canvas_id: Optional[str] = None


@router.get("/canvases")
async def get_canvases(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """获取当前用户的画布历史"""
    return await history_service.get_canvases(session, owner_id=user.id, is_admin=user.is_admin)


@router.post("/canvases")
async def save_canvas(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """保存或更新画布（项目）

    注意：前端会携带 Excalidraw 的 data(elements/appState/files)，
    用 Pydantic 模型解析容易因 extra 字段处理/热重载不同步而丢字段。
    这里直接存原始 JSON，避免 data 被过滤导致刷新后画布空白。
    """
    payload = await request.json()
    try:
        return await history_service.save_canvas(session, payload, owner_id=user.id, is_admin=user.is_admin)
    except PermissionError:
        raise HTTPException(status_code=403, detail="无权修改该画布")


@router.delete("/canvases/{canvas_id}")
async def delete_canvas(
    canvas_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """删除画布（owner 可删，历史无主画布仅管理员可删）"""
    deleted = await history_service.delete_canvas(
        session, canvas_id, owner_id=user.id, is_admin=user.is_admin
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="画布不存在或无权删除")
    return {"success": True}


# 聊天侧上传统一走 upload_guard：登录由路由器级依赖保证；大小边读边限；
# 类型按内容嗅探；落盘文件名与扩展名完全由服务端决定（防路径穿越 / 伪造类型）。
_STORAGE_DIR = Path(__file__).parent.parent.parent / "storage"


def _user_dir(user: User, kind: str) -> Path:
    """按用户分目录：uploads/<user_id>/<kind>/。/storage 鉴权据此放行上传者本人与 admin。"""
    return _STORAGE_DIR / "uploads" / str(user.id) / kind


async def _register_upload(
    session: AsyncSession, user: User, path: Path, kind: str, mime: str, original_name: Optional[str]
) -> str:
    """给聊天上传的文件建 Artifact（无项目，归属上传者），让画布等能按 artifact_id 引用。

    文件仍留在 uploads/<user_id>/<kind>s/ 原处（/storage 直链继续可用）；
    下载鉴权靠 meta.owner_user_id（见 routers/artifacts.py）。返回 artifact_id。
    """
    artifact = await asset_service.create_artifact(
        session,
        project_id=None,
        type=kind,
        storage_key=path.relative_to(_STORAGE_DIR).as_posix(),
        mime_type=mime,
        meta={"source": "chat_upload", "owner_user_id": user.id, "filename": (original_name or "")[:200]},
    )
    return artifact.id


@router.post("/upload-image")
async def upload_image(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    上传图片到 storage/uploads/<user_id>/images 目录（≤15MB；仅 png / jpeg / webp / gif，按内容识别）

    Returns:
        上传后的图片URL（相对路径，如 /storage/uploads/1/images/xxx.png）
    """
    original_name = file.filename
    try:
        path, mime, _size = await upload_guard.save_upload(
            file,
            _user_dir(user, "images"),
            sniff=upload_guard.sniff_image,
            max_bytes=upload_guard.IMAGE_MAX_BYTES,
            kind_label="图片",
            validate=upload_guard.validate_image,
        )
    finally:
        await file.close()
    artifact_id = await _register_upload(session, user, path, "image", mime, original_name)
    return {
        "url": f"/storage/uploads/{user.id}/images/{path.name}",
        "filename": path.name,
        "artifact_id": artifact_id,
    }


@router.post("/upload-audio")
async def upload_audio(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    上传音频到 storage/uploads/<user_id>/audios 目录（≤20MB；mp3 / wav / m4a / aac / ogg / flac，按内容识别）

    Returns:
        上传后的音频URL（相对路径，如 /storage/uploads/1/audios/xxx.mp3）
    """
    original_name = file.filename
    try:
        path, mime, _size = await upload_guard.save_upload(
            file,
            _user_dir(user, "audios"),
            sniff=upload_guard.sniff_audio,
            max_bytes=upload_guard.AUDIO_MAX_BYTES,
            kind_label="音频",
            validate=upload_guard.validate_audio,
        )
    finally:
        await file.close()
    artifact_id = await _register_upload(session, user, path, "audio", mime, original_name)
    return {
        "url": f"/storage/uploads/{user.id}/audios/{path.name}",
        "filename": path.name,
        "artifact_id": artifact_id,
    }


@router.post("/upload-video")
async def upload_video(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    上传视频到 storage/uploads/<user_id>/videos 目录（≤200MB；mp4 / mov / webm / mkv / avi，按内容识别）

    Returns:
        上传后的视频URL（相对路径，如 /storage/uploads/1/videos/xxx.mp4）
    """
    original_name = file.filename
    try:
        path, mime, _size = await upload_guard.save_upload(
            file,
            _user_dir(user, "videos"),
            sniff=upload_guard.sniff_video,
            max_bytes=upload_guard.VIDEO_MAX_BYTES,
            kind_label="视频",
            validate=upload_guard.validate_video,
        )
    finally:
        await file.close()
    artifact_id = await _register_upload(session, user, path, "video", mime, original_name)
    return {
        "url": f"/storage/uploads/{user.id}/videos/{path.name}",
        "filename": path.name,
        "artifact_id": artifact_id,
    }


@router.post("/chat")
async def chat(
    request: ChatRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """
    处理聊天请求，返回流式响应
    支持OpenAI格式的流式输出
    """
    try:
        # 原子预留新画布 owner，历史无主画布仅管理员可认领
        if request.canvas_id and session is not None:
            if not await history_service.can_access_canvas(
                session, request.canvas_id, user.id, is_admin=user.is_admin
            ):
                raise HTTPException(status_code=403, detail="无权访问该画布")

        # 构建消息历史
        messages = request.messages.copy() if request.messages else []
        messages.append({"role": "user", "content": request.message})

        # 中间件在返回 StreamingResponse 后就 reset 了 ExecutionContext，
        # SSE 生成器真正执行时上下文已失效；这里显式捕获一份（含 request_id /
        # user_id / canvas_id），在生成器内部重新 set，结束 reset。
        base_ctx = get_execution_context()
        project_id = None
        if request.canvas_id and session is not None:
            project_id = await session.scalar(
                select(SeriesProject.id).where(SeriesProject.canvas_id == request.canvas_id)
            )
        if session is not None:
            # 流式响应可能持续数分钟，进入流之前归还数据库连接，避免长聊天占满连接池
            await session.close()
        stream_ctx = ExecutionContext(
            request_id=base_ctx.request_id,
            run_id=base_ctx.run_id,
            user_id=str(user.id),
            canvas_id=request.canvas_id,
            project_id=project_id,
            creative_role="chat",
            prompt_version="agent_system_v1",
        )

        channel = canvas_channel(request.canvas_id, user.id) if request.canvas_id else None

        async def stream_and_broadcast():
            ctx_token = set_execution_context(stream_ctx)
            try:
                if request.canvas_id:
                    await manager.broadcast(
                        channel,
                        json.dumps({"type": "user_message", "content": request.message}, ensure_ascii=False),
                    )

                async for chunk in process_chat_stream(messages, request.session_id):
                    yield chunk
                    if not request.canvas_id or not chunk.startswith("data: "):
                        continue

                    data_str = chunk[len("data: ") :].strip()
                    if not data_str:
                        continue
                    if data_str == "[DONE]":
                        await manager.broadcast(channel, json.dumps({"type": "done"}, ensure_ascii=False))
                    else:
                        await manager.broadcast(channel, data_str)
            finally:
                reset_execution_context(ctx_token)

        # 返回流式响应 - 确保立即发送，不缓冲
        return StreamingResponse(
            stream_and_broadcast(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "Content-Type": "text/event-stream; charset=utf-8",
            },
        )
    except HTTPException:
        # 业务异常（403/404 等）不能吞成 500
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
