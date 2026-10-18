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
    def __init__(self, *, catalog_backed: bool = False) -> None:
        self._deployments: dict[str, MediaDeployment] = {}
        self._video_router = None
        self._video_router_revision = ""
        self._catalog_backed = catalog_backed

    def _catalog_deployments(self) -> list[MediaDeployment]:
        if not self._catalog_backed:
            return []
        from app.model_runtime.catalog import get_catalog

        snapshot = get_catalog().snapshot
        catalog_items = {
            item.slug: MediaDeployment(item.slug, item.modality, item.instance)
            for item in snapshot.list()
            if item.modality != "chat" and item.enabled
        }
        # 兼容期：缺失的 Deployment 用同一 Driver Registry 从旧 env 构造；
        # 同 slug 永远以数据库 Catalog 为准。
        from app.model_runtime.registry import DEFAULT_DRIVER_REGISTRY
        from app.services.model_deployment_bootstrap import legacy_deployment_specs

        legacy_items = {
            spec.payload.slug: MediaDeployment(
                spec.payload.slug,
                spec.payload.modality,
                DEFAULT_DRIVER_REGISTRY.build(spec.payload),
            )
            for spec in legacy_deployment_specs()
            if spec.payload.modality != "chat" and spec.payload.enabled
        }
        legacy_items.update(catalog_items)
        return list(legacy_items.values())

    def register(self, deployment: MediaDeployment) -> None:
        if deployment.name in self._deployments:
            raise ValueError(f"media deployment already registered: {deployment.name}")
        self._deployments[deployment.name] = deployment

    def upsert(self, deployment: MediaDeployment) -> None:
        self._deployments[deployment.name] = deployment

    def list(self, modality: Optional[str] = None) -> list[MediaDeployment]:
        merged = dict(self._deployments)
        merged.update({item.name: item for item in self._catalog_deployments()})
        values = list(merged.values())
        return [item for item in values if modality is None or item.modality == modality]

    def get(self, modality: str, name: str) -> Optional[MediaDeployment]:
        return next(
            (
                item
                for item in self.list(modality)
                if item.name == name or getattr(item.provider, "name", "") == name
            ),
            None,
        )

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
        from app.model_runtime.catalog import get_catalog

        revision = get_catalog().snapshot.catalog_revision
        if self._video_router is None or revision != self._video_router_revision:
            from app.providers.mock_video import MockVideoProvider
            from app.providers.video_router import VideoProviderRouter

            providers = [item.provider for item in self.list("video") if not item.mocked]
            if mock_providers_enabled():
                mock = MockVideoProvider()
                providers.append(mock)
                self.upsert(MediaDeployment(mock.name, "video", mock, True))
            self._video_router = VideoProviderRouter(providers)
            self._video_router_revision = revision
        return self._video_router


_RUNTIME: Optional[MediaRuntime] = None


def get_media_runtime() -> MediaRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = MediaRuntime(catalog_backed=True)
    return _RUNTIME
