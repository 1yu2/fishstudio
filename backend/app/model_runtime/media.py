"""统一的图片/视频/语音 Provider Registry。

Worker 只依赖本模块；具体供应商和协议在这里集中注册，后续可通过插件或配置扩展。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.config import mock_providers_enabled


@dataclass(frozen=True)
class MediaDeployment:
    name: str
    modality: str
    provider: Any
    mocked: bool = False


class MediaRuntime:
    def __init__(self) -> None:
        self._deployments: dict[str, MediaDeployment] = {}
        self._video_router = None

    def register(self, deployment: MediaDeployment) -> None:
        if deployment.name in self._deployments:
            raise ValueError(f"media deployment already registered: {deployment.name}")
        self._deployments[deployment.name] = deployment

    def upsert(self, deployment: MediaDeployment) -> None:
        self._deployments[deployment.name] = deployment

    def list(self, modality: Optional[str] = None) -> list[MediaDeployment]:
        values = list(self._deployments.values())
        return [item for item in values if modality is None or item.modality == modality]

    def get(self, modality: str, name: str) -> Optional[MediaDeployment]:
        item = self._deployments.get(name)
        return item if item and item.modality == modality else None

    def resolve_image(self) -> tuple[Any, bool]:
        from app.providers.image_base import MockImageProvider, available_image_providers

        providers = [p for p in self.list("image") if p.provider.is_available() and not p.mocked]
        if not providers:
            providers = [MediaDeployment("mock_image", "image", MockImageProvider(), True)]
        chosen = providers[0]
        if chosen.mocked and not mock_providers_enabled():
            raise RuntimeError("没有可用图片 Provider，且未允许 Mock Provider")
        return chosen.provider, chosen.mocked

    def resolve_speech(self, *, explicit_name: Optional[str] = None) -> tuple[Any, bool]:
        from app.providers.tts_base import MockTTSProvider, QwenTTSProvider

        # 可用性可能随运行时密钥注入而改变（测试、Secret rotation），每次解析都刷新内置 Qwen。
        candidate = QwenTTSProvider()
        qwen = None
        if candidate.is_available():
            qwen = MediaDeployment(candidate.name, "speech", candidate)
            self.upsert(qwen)
        else:
            self._deployments.pop("qwen_tts", None)
        if explicit_name:
            chosen = self.get("speech", explicit_name)
            if chosen is None and explicit_name == "mock_tts":
                chosen = MediaDeployment("mock_tts", "speech", MockTTSProvider(), True)
            if chosen is None or (chosen.mocked and not mock_providers_enabled()):
                raise RuntimeError(f"语音 Provider 不可用: {explicit_name}")
            return chosen.provider, chosen.mocked
        providers = [p for p in self.list("speech") if p.provider.is_available() and not p.mocked]
        if not providers:
            providers = [MediaDeployment("mock_tts", "speech", MockTTSProvider(), True)]
        chosen = providers[0]
        if chosen.mocked and not mock_providers_enabled():
            raise RuntimeError("没有可用语音 Provider，且未允许 Mock Provider")
        return chosen.provider, chosen.mocked

    def video_router(self):
        if self._video_router is None:
            from app.providers.minimax_video import MiniMaxVideoProvider
            from app.providers.mock_video import MockVideoProvider
            from app.providers.video_router import VideoProviderRouter
            from app.providers.volcano_video import VolcanoVideoProvider

            providers = [MiniMaxVideoProvider(), VolcanoVideoProvider(), MockVideoProvider()]
            for provider in providers:
                self.upsert(MediaDeployment(provider.name, "video", provider, provider.name == "mock"))
            self._video_router = VideoProviderRouter(providers)
        return self._video_router


_RUNTIME: Optional[MediaRuntime] = None


def get_media_runtime() -> MediaRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = MediaRuntime()
        # 图片默认端点集中注册；可继续追加其它适配器而无需修改 Worker。
        from app.providers.image_base import available_image_providers
        for provider in available_image_providers():
            _RUNTIME.register(MediaDeployment(provider.name, "image", provider))
        from app.providers.tts_base import QwenTTSProvider
        qwen = QwenTTSProvider()
        if qwen.is_available():
            _RUNTIME.register(MediaDeployment(qwen.name, "speech", qwen))
    return _RUNTIME
