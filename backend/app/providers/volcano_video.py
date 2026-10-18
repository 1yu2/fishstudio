"""火山引擎视频 provider：薄封装 app/tools/volcano_video_generation.py，不复制代码。

非阻塞轮询用 tools 里的 query_video_generation_task_once（单步版）；
旧的阻塞轮询 query_video_generation_task 已改为循环调用它，行为不变。
"""

import asyncio
import logging
from typing import Optional

from app.providers.base import ProviderResult, VideoGenerationRequest, VideoProvider
from app.tools import volcano_video_generation as vvg

logger = logging.getLogger(__name__)

# 统一 mode -> 火山工具 mode
_MODE_MAP = {
    "text": "text",
    "first_frame": "image",
    "first_last_frame": "start_end",
}

# 时长约束（读 tools/volcano_video_generation.py 确认：duration 可选，4-12 秒，默认 5 秒）
MIN_DURATION = 4
MAX_DURATION = 12


class VolcanoVideoProvider(VideoProvider):
    """火山 Seedance 视频生成（兜底）。无 VOLCANO_API_KEY 时 is_available()=False。"""

    name = "volcano"

    def is_available(self) -> bool:
        return bool(vvg.VOLCANO_API_KEY)

    def supports(self, req: VideoGenerationRequest, *, model: Optional[str] = None) -> bool:
        if req.mode not in _MODE_MAP:
            return False  # reference / regenerate 不支持
        if not (MIN_DURATION <= req.duration <= MAX_DURATION):
            return False
        return True

    async def submit(self, req: VideoGenerationRequest, *, model: str | None = None) -> ProviderResult:
        active_model = model or vvg.ACTIVE_VIDEO_MODEL

        def _submit():
            return vvg.submit_video_generation_task(
                prompt=req.prompt,
                duration=req.duration,
                ratio=req.aspect_ratio,
                image_url=req.first_frame,
                start_image_url=req.first_frame,
                end_image_url=req.last_frame,
                mode=_MODE_MAP[req.mode],
                model=active_model,
            )

        try:
            task_id_or_url, is_streaming = await asyncio.to_thread(_submit)
        except Exception as e:
            return ProviderResult(
                success=False,
                provider=self.name,
                model=active_model,
                error_code="server_error",
                error_message=str(e)[:500],
                retryable=True,
            )

        if is_streaming:
            video_url = task_id_or_url
            local_path = await asyncio.to_thread(vvg.download_and_save_video, video_url, req.prompt)
            storage_key = local_path[len("/storage/") :] if local_path.startswith("/storage/") else ""
            artifacts = [{"type": "video", "url": video_url}]
            if storage_key:
                artifacts[0]["storage_key"] = storage_key
            return ProviderResult(
                success=True,
                provider=self.name,
                model=active_model,
                external_request_id=video_url,
                artifacts=artifacts,
                usage={"output_video_seconds": float(req.duration)},
                raw_metadata={"streaming": True},
            )

        return ProviderResult(
            success=True,
            provider=self.name,
            model=active_model,
            external_request_id=str(task_id_or_url),
            usage={"output_video_seconds": float(req.duration)},
        )

    async def get_status(self, external_task_id: str, *, protocol: str | None = None) -> ProviderResult:
        model = vvg.ACTIVE_VIDEO_MODEL
        try:
            step = await asyncio.to_thread(vvg.query_video_generation_task_once, external_task_id)
        except Exception as e:
            # 单步查询异常（网络/5xx）视为可重试
            return ProviderResult(
                success=False,
                provider=self.name,
                model=model,
                external_request_id=external_task_id,
                error_code="server_error",
                error_message=str(e)[:500],
                retryable=True,
            )
        if step["state"] == "succeeded":
            return ProviderResult(
                success=True,
                provider=self.name,
                model=model,
                external_request_id=external_task_id,
                raw_metadata={"video_url": step["video_url"], "raw": step["raw"]},
            )
        if step["state"] == "failed":
            return ProviderResult(
                success=False,
                provider=self.name,
                model=model,
                external_request_id=external_task_id,
                error_code="task_failed",
                error_message=step["error"][:500],
                raw_metadata={"raw": step["raw"]},
            )
        return ProviderResult(
            success=False,
            provider=self.name,
            model=model,
            external_request_id=external_task_id,
            error_code="pending",
            error_message=f"任务进行中: {step['status']}",
            retryable=True,
            raw_metadata={"raw": step["raw"]},
        )

    async def fetch_result(self, external_task_id: str, *, protocol: str | None = None) -> ProviderResult:
        model = vvg.ACTIVE_VIDEO_MODEL
        status = await self.get_status(external_task_id)
        if not status.success:
            return status
        video_url = status.raw_metadata.get("video_url", "")
        local_path = await asyncio.to_thread(vvg.download_and_save_video, video_url, "")
        if not local_path.startswith("/storage/"):
            # 下载失败时 download_and_save_video 原样返回 URL
            return ProviderResult(
                success=False,
                provider=self.name,
                model=model,
                external_request_id=external_task_id,
                error_code="server_error",
                error_message=f"视频下载失败: {video_url}",
                retryable=True,
            )
        return ProviderResult(
            success=True,
            provider=self.name,
            model=model,
            external_request_id=external_task_id,
            artifacts=[{"storage_key": local_path[len("/storage/") :], "type": "video", "url": video_url}],
        )
