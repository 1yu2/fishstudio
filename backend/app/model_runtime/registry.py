"""代码级 Driver Registry：描述配置合同并构造分模态运行实例。"""

from __future__ import annotations

import inspect
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

from app.model_runtime.deployment_config import DRIVER_SPECS, DeploymentInput

DriverFactory = Callable[[DeploymentInput, str], Any]
HealthProbe = Callable[[Any], Awaitable[dict[str, Any]] | dict[str, Any]]


@dataclass(frozen=True)
class DriverRegistration:
    name: str
    modalities: frozenset[str]
    config_schema: type[BaseModel]
    capabilities_schema: type[BaseModel]
    factory: DriverFactory
    probe: HealthProbe | None = None


class DriverRegistry:
    def __init__(self) -> None:
        self._drivers: dict[str, DriverRegistration] = {}

    def register(self, registration: DriverRegistration) -> None:
        if registration.name in self._drivers:
            raise ValueError(f"Driver 已注册: {registration.name}")
        self._drivers[registration.name] = registration

    def get(self, name: str) -> DriverRegistration:
        try:
            return self._drivers[name]
        except KeyError as error:
            raise ValueError(f"未知 Driver: {name}") from error

    def list(self) -> tuple[DriverRegistration, ...]:
        return tuple(self._drivers[name] for name in sorted(self._drivers))

    def build(self, payload: DeploymentInput) -> Any:
        registration = self.get(payload.driver)
        if payload.modality not in registration.modalities:
            raise ValueError(f"Driver {payload.driver} 不支持模态 {payload.modality}")
        credential = resolve_credential(payload.credential_ref)
        return registration.factory(payload, credential)

    async def probe(self, driver: str | DeploymentInput, instance: Any) -> dict[str, Any]:
        driver_name = driver.driver if isinstance(driver, DeploymentInput) else driver
        registration = self.get(driver_name)
        if registration.probe is None:
            return {"available": True, "active_probe": False}
        result = registration.probe(instance)
        if inspect.isawaitable(result):
            result = await result
        return {"available": True, "active_probe": True, "details": result}


def resolve_credential(reference: str) -> str:
    """解析密钥引用；缺失密钥不妨碍配置进入 Catalog，由健康状态表达。"""
    if not reference:
        return ""
    kind, value = reference.split(":", 1)
    if kind == "env":
        return os.getenv(value, "").strip()
    # secret: 引用预留给后续 Secret Manager；绝不把引用当作密钥本身。
    return ""


def _build_openai(payload: DeploymentInput, credential: str):
    from app.model_runtime.core import ChatDeployment, ModelCapability, _openai_compatible_factory

    config = payload.config
    capabilities = payload.capabilities
    credential_env = payload.credential_ref.split(":", 1)[1]
    return ChatDeployment(
        name=payload.slug,
        provider=payload.driver,
        model=payload.model,
        factory=_openai_compatible_factory(
            provider=payload.driver,
            model=payload.model,
            base_url=payload.base_url,
            api_key_env=credential_env,
            temperature=float(config.get("temperature", 0.7)),
            max_tokens=int(config.get("max_tokens", 2048)),
        ),
        capabilities=ModelCapability(
            modalities=frozenset({"chat"}),
            supports_tools=bool(capabilities.get("tools")),
            supports_json_schema=bool(capabilities.get("json_mode")),
            supports_vision=bool(capabilities.get("vision")),
            supports_stream=bool(capabilities.get("streaming")),
            max_concurrency=int(config.get("max_concurrency", 0)),
        ),
        enabled=payload.enabled and bool(credential),
    )


def _build_local_h3(payload: DeploymentInput, credential: str):
    from app.providers.local_h3 import LocalH3Provider

    return LocalH3Provider(
        base_url=payload.base_url,
        api_key=credential,
        model_name=payload.model,
        deployment_name=payload.slug,
        config=payload.config,
        capabilities=payload.capabilities,
        enabled=payload.enabled,
    )


def _build_volcano_image(payload: DeploymentInput, credential: str):
    from app.providers.volcano_image import VolcanoImageProvider

    return VolcanoImageProvider(
        api_key=credential,
        base_url=payload.base_url,
        model_name=payload.model,
        deployment_name=payload.slug,
        timeout_seconds=float(payload.config.get("timeout_seconds", 120)),
        max_concurrency=int(payload.config.get("max_concurrency", 4)),
    )


def _build_minimax_video(payload: DeploymentInput, credential: str):
    from app.providers.minimax_video import MiniMaxVideoProvider

    instance = MiniMaxVideoProvider(api_key=credential, base_url=payload.base_url)
    instance.deployment_name = payload.slug
    return instance


def _build_volcano_video(payload: DeploymentInput, credential: str):
    from app.providers.volcano_video import VolcanoVideoProvider
    from app.tools import volcano_video_generation as vvg

    vvg.VOLCANO_API_KEY = credential
    vvg.VOLCANO_BASE_URL = payload.base_url
    vvg.ACTIVE_BASE_URL = payload.base_url
    vvg.VOLCANO_VIDEO_MODEL = payload.model
    vvg.ACTIVE_VIDEO_MODEL = payload.model
    instance = VolcanoVideoProvider()
    instance.deployment_name = payload.slug
    instance.model_name = payload.model
    instance.configured_credential = bool(credential)
    return instance


def _build_qwen_tts(payload: DeploymentInput, credential: str):
    from app.providers.tts_base import QwenTTSProvider

    instance = QwenTTSProvider(api_key=credential)
    instance.deployment_name = payload.slug
    instance.model_name = payload.model
    return instance


@dataclass(frozen=True)
class TencentAI3DDeployment:
    deployment_name: str
    model_name: str
    base_url: str
    api_key: str

    def is_available(self) -> bool:
        return bool(self.base_url and self.api_key)


def _build_tencent_ai3d(payload: DeploymentInput, credential: str):
    return TencentAI3DDeployment(payload.slug, payload.model, payload.base_url, credential)


async def _provider_health(instance: Any) -> dict[str, Any]:
    is_available = getattr(instance, "is_available", None)
    available = bool(is_available()) if is_available else bool(getattr(instance, "enabled", True))
    if not available:
        raise RuntimeError("Deployment 当前不可用或密钥未配置")
    health = getattr(instance, "health", None)
    if health is None:
        return {"available": True}
    return await health()


def build_default_registry() -> DriverRegistry:
    registry = DriverRegistry()
    factories = {
        "openai_compatible": _build_openai,
        "local_h3_gateway": _build_local_h3,
        "volcano_image": _build_volcano_image,
        "minimax_video": _build_minimax_video,
        "volcano_video": _build_volcano_video,
        "qwen_tts": _build_qwen_tts,
        "tencent_ai3d": _build_tencent_ai3d,
    }
    for name, spec in DRIVER_SPECS.items():
        registry.register(
            DriverRegistration(
                name=name,
                modalities=spec.modalities,
                config_schema=spec.config_schema,
                capabilities_schema=spec.capabilities_schema,
                factory=factories[name],
                probe=_provider_health,
            )
        )
    return registry


DEFAULT_DRIVER_REGISTRY = build_default_registry()
