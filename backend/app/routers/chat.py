"""
聊天路由 - 处理对话请求
"""
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any, Optional
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from app.auth.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import User
from app.services.agent_service import process_chat_stream
from app.services.history_service import history_service
from app.services.connection_manager import manager

router = APIRouter(dependencies=[Depends(get_current_user)])


class ChatRequest(BaseModel):
    message: str
    messages: Optional[List[Dict[str, Any]]] = []
    session_id: Optional[str] = None
    canvas_id: Optional[str] = None


@router.get("/canvases")
async def get_canvases(session: AsyncSession = Depends(get_session)):
    """获取所有画布历史"""
    return await history_service.get_canvases(session)


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
    return await history_service.save_canvas(session, payload, owner_id=user.id)


@router.delete("/canvases/{canvas_id}")
async def delete_canvas(canvas_id: str, session: AsyncSession = Depends(get_session)):
    """删除画布"""
    await history_service.delete_canvas(session, canvas_id)
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
            "application/octet-stream"  # 有些浏览器可能不识别音频类型
        ]
        is_audio = any(content_type.startswith(t) for t in allowed_audio_types)
        
        # 也检查文件扩展名
        original_filename = file.filename or "audio"
        ext = os.path.splitext(original_filename)[1].lower()
        allowed_extensions = [".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".wma"]
        
        if not is_audio and ext not in allowed_extensions:
            raise HTTPException(
                status_code=400, 
                detail=f"只支持音频文件，支持的格式：{', '.join(allowed_extensions)}"
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
                status_code=400,
                detail=f"只支持视频文件，支持的格式：{', '.join(allowed_extensions)}"
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
async def chat(request: ChatRequest):
    """
    处理聊天请求，返回流式响应
    支持OpenAI格式的流式输出
    """
    try:
        # 构建消息历史
        messages = request.messages.copy() if request.messages else []
        messages.append({
            "role": "user",
            "content": request.message
        })

        async def stream_and_broadcast():
            if request.canvas_id:
                await manager.broadcast(
                    request.canvas_id,
                    json.dumps({"type": "user_message", "content": request.message}, ensure_ascii=False),
                )

            async for chunk in process_chat_stream(messages, request.session_id):
                yield chunk
                if not request.canvas_id or not chunk.startswith("data: "):
                    continue

                data_str = chunk[len("data: "):].strip()
                if not data_str:
                    continue
                if data_str == "[DONE]":
                    await manager.broadcast(request.canvas_id, json.dumps({"type": "done"}, ensure_ascii=False))
                else:
                    await manager.broadcast(request.canvas_id, data_str)

        # 返回流式响应 - 确保立即发送，不缓冲
        return StreamingResponse(
            stream_and_broadcast(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "Content-Type": "text/event-stream; charset=utf-8"
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
