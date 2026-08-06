"""图片生成 provider 抽象（最小接口，文档 S6 风格）。

真实图片 provider：VolcanoImageProvider（providers/volcano_image.py，薄封装
app/tools/volcano_image_generation.py）；MockImageProvider（ffmpeg 纯色占位图）
仅允许在 ALLOW_MOCK_PROVIDERS=true 时使用。
"""

import logging
import subprocess
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel

from app.providers.base import ProviderResult

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent
IMAGES_DIR = BASE_DIR / "storage" / "images"


class ImageGenerationRequest(BaseModel):
    """图片生成统一请求（首帧图/角色图/场景图共用）。"""

    prompt: str
    resolution: str = "768P"
    aspect_ratio: str = "9:16"
    candidate_count: int = 1
    provider_policy: Literal["cheapest", "balanced", "quality", "explicit"] = "balanced"
    # 源图（image+prompt→image 编辑：首帧/角色参考图），/storage/ 路径或本地路径，可选
    source_image: Optional[str] = None


class ImageProvider(ABC):
    """图片生成 provider 抽象基类。"""

    name: str = ""

    @abstractmethod
    def is_available(self) -> bool:
        """provider 当前是否可用（缺 API key 等返回 False）。"""

    @abstractmethod
    async def generate(self, req: ImageGenerationRequest) -> ProviderResult:
        """生成图片并落盘到 backend/storage/images/，返回含 storage_key 的 artifacts。"""


def _placeholder_png(file_path: Path, size: str = "576x1024") -> None:
    """ffmpeg 纯色占位图（mock 用）。"""
    import imageio_ffmpeg

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(), "-y",
            "-f", "lavfi", "-i", f"color=c=0x336699:size={size}",
            "-frames:v", "1", str(file_path),
        ],
        check=True, capture_output=True, timeout=30,
    )


class MockImageProvider(ImageProvider):
    """ffmpeg 占位图。仅测试/演示用，worker 侧受 ALLOW_MOCK_PROVIDERS 控制。"""

    name = "mock_image"

    def is_available(self) -> bool:
        return True

    async def generate(self, req: ImageGenerationRequest) -> ProviderResult:
        filename = f"mock_image_{uuid.uuid4().hex[:12]}.png"
        file_path = IMAGES_DIR / filename
        generated = False
        try:
            _placeholder_png(file_path)
            generated = True
        except Exception as e:
            logger.warning(f"mock 占位图生成失败，退化为仅元数据: {e}")
        return ProviderResult(
            success=True,
            provider=self.name,
            model="mock-image",
            artifacts=[{"storage_key": f"images/{filename}", "type": "image"}],
            raw_metadata={"mocked": True, "generated": generated},
        )


def available_image_providers() -> list[ImageProvider]:
    """当前可用的真实图片 provider（选择顺序：volcano → 其余后续注册）。

    MockImageProvider 不在此注册——mock 兜底由 worker 按 ALLOW_MOCK_PROVIDERS 控制。
    """
    from app.providers.volcano_image import VolcanoImageProvider

    providers: list[ImageProvider] = [VolcanoImageProvider()]
    return [p for p in providers if p.is_available()]
