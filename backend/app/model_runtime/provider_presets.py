"""供应商预设：名称、可用接入类型、默认地址与密钥变量、检查与拉取模型列表的方式。

前端通过 /api/model-providers/presets 读取，前后端只有这一份定义。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Literal, Optional
from urllib.parse import urlparse

CheckMethod = Literal["openai_models", "ark", "h3_health", "deployment_probe"]


@dataclass(frozen=True)
class ProviderPreset:
    id: str
    name: str
    description: str
    drivers: tuple[str, ...]
    default_base_url: str = ""
    # 预设默认沿用的密钥变量（兼容旧 .env）；同一变量已被其他供应商占用时改用独立变量
    credential_env: str = ""
    hosts: tuple[str, ...] = ()
    check: CheckMethod = "deployment_probe"
    # 是否支持 GET {base_url}/models 拉取模型列表
    lists_models: bool = False
    self_hosted: bool = False
    custom: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> dict:
        data = asdict(self)
        data["drivers"] = list(self.drivers)
        data["hosts"] = list(self.hosts)
        data["tags"] = list(self.tags)
        return data


PRESETS: dict[str, ProviderPreset] = {
    preset.id: preset
    for preset in (
        ProviderPreset(
            id="volcano-ark",
            name="火山方舟",
            description="Seedance 视频、Seedream 图片、豆包对话",
            drivers=("ark_video", "volcano_image", "openai_compatible", "volcano_video"),
            default_base_url="https://ark.cn-beijing.volces.com/api/v3",
            credential_env="VOLCANO_API_KEY",
            hosts=("volces.com", "volcengine.com", "dmxapi.cn"),
            check="ark",
            lists_models=True,
            tags=("视频", "图片", "对话"),
        ),
        ProviderPreset(
            id="local-h3",
            name="自建 GPU · H3",
            description="本机或局域网 ComfyUI 上的量化 MiniMax H3",
            drivers=("local_h3_gateway",),
            credential_env="LOCAL_H3_API_KEY",
            check="h3_health",
            self_hosted=True,
            tags=("视频", "自建"),
        ),
        ProviderPreset(
            id="minimax",
            name="MiniMax 海螺",
            description="海螺视频",
            drivers=("minimax_video",),
            default_base_url="https://api.minimaxi.com",
            credential_env="MINIMAX_API_KEY",
            hosts=("minimaxi.com", "minimax.chat", "minimax.io"),
            tags=("视频",),
        ),
        ProviderPreset(
            id="dashscope",
            name="阿里云百炼",
            description="Qwen 语音合成与声音复刻",
            drivers=("qwen_tts",),
            credential_env="DASHSCOPE_API_KEY",
            hosts=("aliyuncs.com",),
            tags=("语音",),
        ),
        ProviderPreset(
            id="deepseek",
            name="DeepSeek",
            description="DeepSeek 对话模型",
            drivers=("openai_compatible",),
            default_base_url="https://api.deepseek.com/v1",
            hosts=("deepseek.com",),
            check="openai_models",
            lists_models=True,
            tags=("对话",),
        ),
        ProviderPreset(
            id="siliconflow",
            name="硅基流动",
            description="开源模型对话（Qwen、GLM、DeepSeek 等）",
            drivers=("openai_compatible",),
            default_base_url="https://api.siliconflow.cn/v1",
            credential_env="OPENAI_API_KEY",
            hosts=("siliconflow.cn", "siliconflow.com"),
            check="openai_models",
            lists_models=True,
            tags=("对话",),
        ),
        ProviderPreset(
            id="moonshot",
            name="月之暗面 Kimi",
            description="Kimi 对话模型",
            drivers=("openai_compatible",),
            default_base_url="https://api.moonshot.cn/v1",
            hosts=("moonshot.cn",),
            check="openai_models",
            lists_models=True,
            tags=("对话",),
        ),
        ProviderPreset(
            id="zhipu",
            name="智谱 GLM",
            description="GLM 对话模型",
            drivers=("openai_compatible",),
            default_base_url="https://open.bigmodel.cn/api/paas/v4",
            hosts=("bigmodel.cn",),
            check="openai_models",
            lists_models=True,
            tags=("对话",),
        ),
        ProviderPreset(
            id="openrouter",
            name="OpenRouter",
            description="聚合多家模型的统一接口",
            drivers=("openai_compatible",),
            default_base_url="https://openrouter.ai/api/v1",
            hosts=("openrouter.ai",),
            check="openai_models",
            lists_models=True,
            tags=("对话",),
        ),
        ProviderPreset(
            id="tencent",
            name="腾讯混元 3D",
            description="文本 / 图片生成 3D 模型",
            drivers=("tencent_ai3d",),
            credential_env="TENCENT_AI3D_API_KEY",
            hosts=("tencentcloudapi.com",),
            tags=("3D",),
        ),
        ProviderPreset(
            id="openai-compatible",
            name="自定义 OpenAI 兼容",
            description="任意兼容 OpenAI 接口的服务，如自部署 vLLM、Ollama、代理",
            drivers=("openai_compatible",),
            check="openai_models",
            lists_models=True,
            custom=True,
            tags=("对话",),
        ),
    )
}

# 无法按地址识别时，按 Driver 归入的预设
_DRIVER_HOME = {
    "local_h3_gateway": "local-h3",
    "minimax_video": "minimax",
    "qwen_tts": "dashscope",
    "tencent_ai3d": "tencent",
    "ark_video": "volcano-ark",
    "volcano_image": "volcano-ark",
    "volcano_video": "volcano-ark",
}


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower() if url else ""


def _matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes)


def preset_for(driver: str, base_url: str) -> ProviderPreset:
    """按服务地址与 Driver 判断一个已有模型属于哪个预设。"""
    host = host_of(base_url)
    if host:
        for preset in PRESETS.values():
            if driver in preset.drivers and preset.hosts and _matches(host, preset.hosts):
                return preset
    home = _DRIVER_HOME.get(driver)
    if home:
        return PRESETS[home]
    return PRESETS["openai-compatible"]


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return (slug or "provider")[:120]


def provider_env_name(slug: str) -> str:
    """新供应商独立的密钥变量名。"""
    core = re.sub(r"[^A-Z0-9]+", "_", slug.upper()).strip("_") or "CUSTOM"
    return f"FS_PROVIDER_{core}_API_KEY"


def infer_model_kind(preset: ProviderPreset, model_id: str) -> Optional[tuple[str, str]]:
    """拉取到的模型 ID → (driver, modality)；不支持的类型（如 embedding）返回 None。"""
    lowered = model_id.lower()
    if any(word in lowered for word in ("embedding", "embed", "rerank", "moderation", "whisper", "tts")):
        return None
    if "seedance" in lowered and "ark_video" in preset.drivers:
        return "ark_video", "video"
    if "seedream" in lowered and "volcano_image" in preset.drivers:
        return "volcano_image", "image"
    if "openai_compatible" in preset.drivers:
        return "openai_compatible", "chat"
    return None
