"""供应商类型（接口协议）：决定默认地址、模型列表端点、连接检查方式，以及每种模型能力用哪个 Driver。

供应商本身由用户填写 Provider ID / 名称 / Base URL / API Key Env / Models Endpoint / 能力；
这里只描述协议。前端通过 /api/model-providers/types 读取，前后端只有这一份定义。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional
from urllib.parse import urlparse

# 能力（模型类型）→ Deployment 的 modality
CAPABILITY_MODALITY: dict[str, str] = {
    "chat": "chat",
    "embedding": "embedding",
    "rerank": "rerank",
    "asr": "asr",
    "tts": "speech",
    "image": "image",
    "video": "video",
    "3d": "model_3d",
}
CAPABILITIES = tuple(CAPABILITY_MODALITY)
MODALITY_CAPABILITY = {modality: capability for capability, modality in CAPABILITY_MODALITY.items()}

CheckMethod = Literal["models_endpoint", "ark", "h3_health", "deployment_probe"]

PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$")
ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")


@dataclass(frozen=True)
class ProviderType:
    id: str
    label: str
    # 能力 → Driver；不在这里的能力该类型不支持
    drivers: dict[str, str]
    default_base_url: str = ""
    default_models_endpoint: str = "/models"
    check: CheckMethod = "models_endpoint"
    lists_models: bool = True
    hosts: tuple[str, ...] = ()

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(capability for capability in CAPABILITIES if capability in self.drivers)

    def driver_for(self, capability: str) -> Optional[str]:
        return self.drivers.get(capability)

    def summary(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "capabilities": list(self.capabilities),
            "drivers": dict(self.drivers),
            "default_base_url": self.default_base_url,
            "default_models_endpoint": self.default_models_endpoint,
            "lists_models": self.lists_models,
        }


PROVIDER_TYPES: dict[str, ProviderType] = {
    item.id: item
    for item in (
        ProviderType(
            id="openai",
            label="OpenAI Completions API",
            drivers={
                "chat": "openai_compatible",
                "embedding": "generic_model",
                "rerank": "generic_model",
                "asr": "generic_model",
            },
            default_base_url="https://api.openai.com/v1",
        ),
        ProviderType(
            id="volcengine-ark",
            label="Volcengine Ark API",
            drivers={
                "chat": "openai_compatible",
                "image": "volcano_image",
                "video": "ark_video",
                "embedding": "generic_model",
            },
            default_base_url="https://ark.cn-beijing.volces.com/api/v3",
            check="ark",
            hosts=("volces.com", "volcengine.com"),
        ),
        ProviderType(
            id="dashscope",
            label="DashScope API",
            drivers={
                "chat": "openai_compatible",
                "tts": "qwen_tts",
                "asr": "generic_model",
                "embedding": "generic_model",
                "rerank": "generic_model",
            },
            default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            hosts=("aliyuncs.com",),
        ),
        ProviderType(
            id="minimax",
            label="MiniMax API",
            drivers={"video": "minimax_video", "chat": "openai_compatible"},
            default_base_url="https://api.minimaxi.com",
            default_models_endpoint="",
            check="deployment_probe",
            lists_models=False,
            hosts=("minimaxi.com", "minimax.chat", "minimax.io"),
        ),
        ProviderType(
            id="comfyui-gateway",
            label="ComfyUI Gateway (H3)",
            drivers={"video": "local_h3_gateway"},
            default_models_endpoint="",
            check="h3_health",
            lists_models=False,
        ),
        ProviderType(
            id="dmxapi",
            label="DMXAPI (Seedance proxy)",
            drivers={"video": "volcano_video"},
            default_base_url="https://www.dmxapi.cn/v1",
            default_models_endpoint="",
            check="deployment_probe",
            lists_models=False,
            hosts=("dmxapi.cn",),
        ),
        ProviderType(
            id="tencent-hunyuan-3d",
            label="Tencent Hunyuan 3D API",
            drivers={"3d": "tencent_ai3d"},
            default_models_endpoint="",
            check="deployment_probe",
            lists_models=False,
            hosts=("tencentcloudapi.com",),
        ),
    )
}

# 存量 Deployment 无法按地址判断时，按 Driver 归类
_DRIVER_TYPE = {
    "local_h3_gateway": "comfyui-gateway",
    "minimax_video": "minimax",
    "qwen_tts": "dashscope",
    "tencent_ai3d": "tencent-hunyuan-3d",
    "ark_video": "volcengine-ark",
    "volcano_image": "volcengine-ark",
    "volcano_video": "volcengine-ark",
}


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower() if url else ""


def _matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes)


def type_for(driver: str, base_url: str) -> ProviderType:
    """存量模型属于哪种供应商类型：先按地址，再按 Driver，最后视为 OpenAI 兼容。"""
    host = host_of(base_url)
    if driver == "volcano_video" and _matches(host, ("dmxapi.cn",)):
        return PROVIDER_TYPES["dmxapi"]
    if host:
        for provider_type in PROVIDER_TYPES.values():
            if (
                provider_type.hosts
                and _matches(host, provider_type.hosts)
                and driver in provider_type.drivers.values()
            ):
                return provider_type
    return PROVIDER_TYPES[_DRIVER_TYPE.get(driver, "openai")]


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return (slug or "provider")[:64]


def default_env_name(provider_id: str) -> str:
    core = re.sub(r"[^A-Z0-9]+", "_", provider_id.upper()).strip("_") or "PROVIDER"
    return f"{core}_API_KEY"


def infer_capability(provider_type: ProviderType, model_id: str, enabled: tuple[str, ...]) -> Optional[str]:
    """拉取到的模型 ID → 能力；只返回该供应商已勾选、且类型支持的能力。"""
    lowered = model_id.lower()
    # 只看最后一段，忽略 "BAAI/" 这类组织前缀
    name = lowered.rsplit("/", 1)[-1]
    guesses = []
    if "seedance" in lowered or "video" in lowered or "hailuo" in lowered or "kling" in lowered:
        guesses.append("video")
    if "seedream" in lowered or "image" in lowered or "flux" in lowered or "sdxl" in lowered:
        guesses.append("image")
    if "rerank" in lowered:
        guesses.append("rerank")
    if "embed" in name or (name.startswith("bge") and "rerank" not in name):
        guesses.append("embedding")
    if "tts" in lowered or "speech" in lowered or "cosyvoice" in lowered:
        guesses.append("tts")
    if "asr" in lowered or "whisper" in lowered or "paraformer" in lowered or "sensevoice" in lowered:
        guesses.append("asr")
    guesses.append("chat")
    for capability in guesses:
        if capability in enabled and capability in provider_type.drivers:
            return capability
    return None
