"""Mock 视频 provider：本地兜底，ffmpeg 生成测试视频，不产生外部调用。"""

import asyncio
import logging
import os
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from app.providers.base import ProviderResult, VideoGenerationRequest, VideoProvider

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent
VIDEOS_DIR = BASE_DIR / "storage" / "videos"

# task_id -> {"request": VideoGenerationRequest, "polls": int}
_TASKS: dict[str, dict] = {}


def _make_test_video(path: Path, duration: int) -> bool:
    """用 imageio_ffmpeg 自带的 ffmpeg 生成 testsrc 测试视频。失败返回 False。"""
    try:
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f", "lavfi",
                "-i", f"testsrc=duration={max(1, min(duration, 2))}:size=320x568:rate=8",
                "-pix_fmt", "yuv420p",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return True
    except Exception as e:
        logger.warning(f"mock 测试视频生成失败，退化为仅元数据: {e}")
        return False


class MockVideoProvider(VideoProvider):
    """永远可用的兜底 provider（无真实 provider 时保证链路可跑通）。"""

    name = "mock"

    def is_available(self) -> bool:
        return True

    def supports(self, req: VideoGenerationRequest, *, model: Optional[str] = None) -> bool:
        return True  # 全部 mode

    async def submit(self, req: VideoGenerationRequest, *, model: str | None = None) -> ProviderResult:
        task_id = f"mock_{uuid.uuid4().hex[:12]}"
        _TASKS[task_id] = {"request": req, "polls": 0}
        return ProviderResult(
            success=True,
            provider=self.name,
            model="mock-video",
            external_request_id=task_id,
            raw_metadata={"mocked": True},
        )

    async def get_status(self, external_task_id: str, *, protocol: str | None = None) -> ProviderResult:
        task = _TASKS.get(external_task_id)
        if task is None:
            return ProviderResult(
                success=False,
                provider=self.name,
                model="mock-video",
                external_request_id=external_task_id,
                error_code="not_found",
                error_message=f"未知 mock task: {external_task_id}",
            )
        delay = int(os.getenv("MOCK_PROVIDER_DELAY_POLLS", "2"))
        task["polls"] += 1
        if task["polls"] <= delay:
            return ProviderResult(
                success=False,
                provider=self.name,
                model="mock-video",
                external_request_id=external_task_id,
                error_code="pending",
                error_message="mock 任务进行中",
                retryable=True,
                raw_metadata={"mocked": True},
            )
        req: VideoGenerationRequest = task["request"]
        # 状态转成功时生成测试视频；ffmpeg 失败退化：只返回 storage_key
        file_path = VIDEOS_DIR / f"{external_task_id}.mp4"
        generated = await asyncio.to_thread(_make_test_video, file_path, req.duration)
        return ProviderResult(
            success=True,
            provider=self.name,
            model="mock-video",
            external_request_id=external_task_id,
            artifacts=[{"storage_key": f"videos/{external_task_id}.mp4", "type": "video"}],
            usage={"output_video_seconds": float(req.duration)},
            raw_metadata={"mocked": True, "generated": generated},
        )

    async def fetch_result(self, external_task_id: str, *, protocol: str | None = None) -> ProviderResult:
        # get_status 成功分支已落盘，这里直接返回 storage_key
        task = _TASKS.get(external_task_id)
        req: VideoGenerationRequest = task["request"] if task else VideoGenerationRequest(prompt="")
        file_path = VIDEOS_DIR / f"{external_task_id}.mp4"
        return ProviderResult(
            success=True,
            provider=self.name,
            model="mock-video",
            external_request_id=external_task_id,
            artifacts=[
                {
                    "storage_key": f"videos/{external_task_id}.mp4",
                    "type": "video",
                    "duration_ms": req.duration * 1000,
                }
            ],
            usage={"output_video_seconds": float(req.duration)},
            raw_metadata={"mocked": True, "generated": file_path.is_file()},
        )
