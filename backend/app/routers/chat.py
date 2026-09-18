"""
聊天路由 - 处理对话请求
"""

import json
import os
import uuid
from datetime import datetime
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
from app.services.agent_service import process_chat_stream
from app.services.connection_manager import manager
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
    return await history_service.get_canvases(session, owner_id=user.id)


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
        return await history_service.save_canvas(session, payload, owner_id=user.id)
    except PermissionError:
        raise HTTPException(status_code=403, detail="无权修改该画布")


@router.delete("/canvases/{canvas_id}")
async def delete_canvas(
    canvas_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """删除画布（仅 owner 可删）"""
    deleted = await history_service.delete_canvas(session, canvas_id, owner_id=user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="画布不存在或无权删除")
    return {"success": True}


@router.post("/upload-image")
async def upload_image(file: UploadFile = File(...)):
    """
    上传图片到 storage/images 目录

    Returns:
        上传后的图片URL（相对路径，如 /storage/images/xxx.jpg）
    """
    try:
        # 确保上传目录存在
        BASE_DIR = Path(__file__).parent.parent.parent
        IMAGES_DIR = BASE_DIR / "storage" / "images"
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)

        # 验证文件类型
        content_type = file.content_type or ""
        if not content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="只支持图片文件")

        # 生成唯一文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]

        # 获取文件扩展名
        original_filename = file.filename or "image"
        ext = os.path.splitext(original_filename)[1] or ".jpg"
        if not ext.startswith("."):
            ext = ".jpg"

        filename = f"upload_{timestamp}_{unique_id}{ext}"
        file_path = IMAGES_DIR / filename

        # 保存文件
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # 返回相对路径
        image_url = f"/storage/images/{filename}"
        return {"url": image_url, "filename": filename}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")


@router.post("/upload-audio")
async def upload_audio(file: UploadFile = File(...)):
    """
    上传音频到 storage/audios 目录
    支持的格式：mp3, wav, m4a, aac, ogg, flac, wma

    Returns:
        上传后的音频URL（相对路径，如 /storage/audios/xxx.mp3 或 /storage/audios/xxx.wav）
    """
    try:
        # 确保上传目录存在
        BASE_DIR = Path(__file__).parent.parent.parent
        AUDIOS_DIR = BASE_DIR / "storage" / "audios"
        AUDIOS_DIR.mkdir(parents=True, exist_ok=True)

        # 验证文件类型
        content_type = file.content_type or ""
        # 支持的音频 MIME 类型
        allowed_audio_types = [
            "audio/",  # 通用音频类型（audio/mpeg, audio/wav, audio/mp4 等）
            "audio/wav",
            "audio/x-wav",
            "audio/wave",
            "audio/mpeg",  # mp3
            "audio/mp4",  # m4a
            "audio/aac",
            "audio/ogg",
            "audio/flac",
            "application/octet-stream",  # 有些浏览器可能不识别音频类型
        ]
        is_audio = any(content_type.startswith(t) for t in allowed_audio_types)

        # 也检查文件扩展名
        original_filename = file.filename or "audio"
        ext = os.path.splitext(original_filename)[1].lower()
        allowed_extensions = [".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".wma"]

        if not is_audio and ext not in allowed_extensions:
            raise HTTPException(
                status_code=400, detail=f"只支持音频文件，支持的格式：{', '.join(allowed_extensions)}"
            )

        # 生成唯一文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]

        # 获取文件扩展名，如果没有则默认使用 .mp3
        if not ext or ext not in allowed_extensions:
            ext = ".mp3"

        filename = f"upload_{timestamp}_{unique_id}{ext}"
        file_path = AUDIOS_DIR / filename

        # 保存文件
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # 返回相对路径
        audio_url = f"/storage/audios/{filename}"
        return {"url": audio_url, "filename": filename}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")


@router.post("/upload-video")
async def upload_video(file: UploadFile = File(...)):
    """
    上传视频到 storage/videos 目录
    支持的格式：mp4, mov, avi, mkv, webm

    Returns:
        上传后的视频URL（相对路径，如 /storage/videos/xxx.mp4）
    """
    try:
        BASE_DIR = Path(__file__).parent.parent.parent
        VIDEOS_DIR = BASE_DIR / "storage" / "videos"
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

        content_type = file.content_type or ""
        original_filename = file.filename or "video"
        ext = os.path.splitext(original_filename)[1].lower()
        allowed_extensions = [".mp4", ".mov", ".avi", ".mkv", ".webm"]

        is_video = content_type.startswith("video/") or content_type == "application/octet-stream"
        if not is_video and ext not in allowed_extensions:
            raise HTTPException(
                status_code=400, detail=f"只支持视频文件，支持的格式：{', '.join(allowed_extensions)}"
            )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        if not ext or ext not in allowed_extensions:
            ext = ".mp4"

        filename = f"upload_{timestamp}_{unique_id}{ext}"
        file_path = VIDEOS_DIR / filename

        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        video_url = f"/storage/videos/{filename}"
        return {"url": video_url, "filename": filename}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")


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
        # canvas 归属校验：canvas 存在且属于别的用户时拒绝
        if request.canvas_id and session is not None:
            owner_id = await history_service.get_canvas_owner(session, request.canvas_id)
            if owner_id is not None and owner_id != user.id:
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
        stream_ctx = ExecutionContext(
            request_id=base_ctx.request_id,
            run_id=base_ctx.run_id,
            user_id=str(user.id),
            canvas_id=request.canvas_id,
            project_id=project_id,
            creative_role="chat",
            prompt_version="agent_system_v1",
        )

        async def stream_and_broadcast():
            ctx_token = set_execution_context(stream_ctx)
            try:
                if request.canvas_id:
                    await manager.broadcast(
                        request.canvas_id,
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
                        await manager.broadcast(
                            request.canvas_id, json.dumps({"type": "done"}, ensure_ascii=False)
                        )
                    else:
                        await manager.broadcast(request.canvas_id, data_str)
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
