"""Model Deployment DTO、Driver 配置校验与稳定哈希。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MODALITIES = {"chat", "image", "video", "speech", "model_3d"}
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,126}[a-z0-9]$")
CREDENTIAL_PATTERN = re.compile(r"^(env|secret):[A-Za-z0-9_./-]+$")
SECRET_KEYS = {"api_key", "apikey", "token", "secret", "password", "authorization"}


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocalH3Config(StrictConfigModel):
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    artifact_timeout_seconds: float = Field(default=900, gt=0, le=7200)
    max_concurrency: int = Field(default=1, ge=1, le=8)
    min_duration: int = Field(default=4, ge=1, le=60)
    max_duration: int = Field(default=6, ge=1, le=60)
    max_artifact_bytes: int = Field(default=536_870_912, ge=1_048_576, le=5_368_709_120)
    max_inline_image_bytes: int = Field(default=8_388_608, ge=1_024, le=33_554_432)
    artifact_allowed_hosts: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_duration(self):
        if self.min_duration > self.max_duration:
            raise ValueError("min_duration 不能大于 max_duration")
        return self


class LocalH3Capabilities(StrictConfigModel):
    modes: list[Literal["text", "first_frame", "first_last_frame"]] = Field(
        default_factory=lambda: ["text"], min_length=1
    )
    aspect_ratios: list[str] = Field(default_factory=lambda: ["16:9", "9:16", "1:1"], min_length=1)


class OpenAICompatibleConfig(StrictConfigModel):
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    max_concurrency: int = Field(default=8, ge=1, le=256)
    organization: str = Field(default="", max_length=255)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=2048, ge=1, le=1_000_000)


class OpenAICompatibleCapabilities(StrictConfigModel):
    streaming: bool = True
    tools: bool = True
    vision: bool = False
    json_mode: bool = True


class VolcanoImageConfig(StrictConfigModel):
    timeout_seconds: float = Field(default=120, gt=0, le=900)
    max_concurrency: int = Field(default=4, ge=1, le=64)
    region: str = Field(default="cn-beijing", min_length=1, max_length=64)


class VolcanoImageCapabilities(StrictConfigModel):
    operations: list[Literal["generate", "edit"]] = Field(default_factory=lambda: ["generate"], min_length=1)
    max_images: int = Field(default=4, ge=1, le=16)


class AsyncMediaConfig(StrictConfigModel):
    timeout_seconds: float = Field(default=60, gt=0, le=1800)
    max_concurrency: int = Field(default=2, ge=1, le=256)
    protocol: str = Field(default="", max_length=64)


class VideoCapabilities(StrictConfigModel):
    modes: list[str] = Field(default_factory=lambda: ["text"], min_length=1, max_length=16)
    min_duration: int = Field(default=4, ge=1, le=300)
    max_duration: int = Field(default=15, ge=1, le=300)

    @model_validator(mode="after")
    def validate_duration(self):
        if self.min_duration > self.max_duration:
            raise ValueError("min_duration 不能大于 max_duration")
        return self


ARK_RESOLUTIONS = ("480p", "720p", "1080p")
ARK_ASPECT_RATIOS = ("16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "9:21", "adaptive")


class ArkVideoConfig(StrictConfigModel):
    """火山方舟 Seedance：请求默认值与下载限制。"""

    timeout_seconds: float = Field(default=60, gt=0, le=600)
    artifact_timeout_seconds: float = Field(default=600, gt=0, le=3600)
    max_concurrency: int = Field(default=2, ge=1, le=64)
    max_artifact_bytes: int = Field(default=536_870_912, ge=1_048_576, le=5_368_709_120)
    max_inline_image_bytes: int = Field(default=10_485_760, ge=1_024, le=31_457_280)
    default_resolution: Literal["480p", "720p", "1080p"] = "720p"
    preview_resolution: Literal["480p", "720p", "1080p"] = "480p"
    generate_audio: bool = False
    watermark: bool = False
    return_last_frame: bool = True
    camera_fixed: bool | None = None
    service_tier: Literal["default", "flex"] = "default"
    execution_expires_after: int | None = Field(default=None, ge=3600, le=259_200)
    callback_url: str = Field(default="", max_length=2048)
    parameter_style: Literal["fields", "inline"] = "fields"
    artifact_allowed_host_suffixes: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("callback_url")
    @classmethod
    def validate_callback(cls, value: str) -> str:
        return _validate_url(value, required=False)


class ArkVideoCapabilities(StrictConfigModel):
    modes: list[Literal["text", "first_frame", "first_last_frame", "reference"]] = Field(
        default_factory=lambda: ["text", "first_frame", "first_last_frame"], min_length=1
    )
    min_duration: int = Field(default=4, ge=1, le=60)
    max_duration: int = Field(default=12, ge=1, le=60)
    resolutions: list[Literal["480p", "720p", "1080p"]] = Field(
        default_factory=lambda: list(ARK_RESOLUTIONS), min_length=1
    )
    aspect_ratios: list[str] = Field(default_factory=lambda: list(ARK_ASPECT_RATIOS), min_length=1)
    max_reference_images: int = Field(default=0, ge=0, le=9)

    @model_validator(mode="after")
    def validate_values(self):
        if self.min_duration > self.max_duration:
            raise ValueError("min_duration 不能大于 max_duration")
        unknown = set(self.aspect_ratios) - set(ARK_ASPECT_RATIOS)
        if unknown:
            raise ValueError(f"不支持的画幅: {sorted(unknown)}")
        if "reference" in self.modes and self.max_reference_images < 1:
            raise ValueError("启用 reference 模式时 max_reference_images 必须 ≥ 1")
        return self


class SpeechCapabilities(StrictConfigModel):
    languages: list[str] = Field(default_factory=lambda: ["zh"], min_length=1)
    voice_clone: bool = True


class Model3DCapabilities(StrictConfigModel):
    modes: list[Literal["text", "image"]] = Field(default_factory=lambda: ["text", "image"], min_length=1)
    formats: list[Literal["glb", "obj"]] = Field(default_factory=lambda: ["glb", "obj"], min_length=1)


class DriverSpec(BaseModel):
    modalities: frozenset[str]
    config_schema: type[BaseModel]
    capabilities_schema: type[BaseModel]
    base_url_required: bool = True
    credential_required: bool = True

    model_config = ConfigDict(arbitrary_types_allowed=True)


DRIVER_SPECS: dict[str, DriverSpec] = {
    "local_h3_gateway": DriverSpec(
        modalities=frozenset({"video"}),
        config_schema=LocalH3Config,
        capabilities_schema=LocalH3Capabilities,
    ),
    "openai_compatible": DriverSpec(
        modalities=frozenset({"chat"}),
        config_schema=OpenAICompatibleConfig,
        capabilities_schema=OpenAICompatibleCapabilities,
    ),
    "volcano_image": DriverSpec(
        modalities=frozenset({"image"}),
        config_schema=VolcanoImageConfig,
        capabilities_schema=VolcanoImageCapabilities,
    ),
    "minimax_video": DriverSpec(
        modalities=frozenset({"video"}),
        config_schema=AsyncMediaConfig,
        capabilities_schema=VideoCapabilities,
    ),
    "volcano_video": DriverSpec(
        modalities=frozenset({"video"}),
        config_schema=AsyncMediaConfig,
        capabilities_schema=VideoCapabilities,
    ),
    "ark_video": DriverSpec(
        modalities=frozenset({"video"}),
        config_schema=ArkVideoConfig,
        capabilities_schema=ArkVideoCapabilities,
    ),
    "qwen_tts": DriverSpec(
        modalities=frozenset({"speech"}),
        config_schema=AsyncMediaConfig,
        capabilities_schema=SpeechCapabilities,
        base_url_required=False,
    ),
    "tencent_ai3d": DriverSpec(
        modalities=frozenset({"model_3d"}),
        config_schema=AsyncMediaConfig,
        capabilities_schema=Model3DCapabilities,
    ),
}


def _contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in SECRET_KEYS or normalized.endswith("_api_key"):
                return True
            if _contains_secret(child):
                return True
    elif isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return False


def _validate_url(url: str, *, required: bool) -> str:
    value = url.strip().rstrip("/")
    if not value:
        if required:
            raise ValueError("base_url 不能为空")
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base_url 必须是有效的 http/https URL")
    if parsed.username or parsed.password:
        raise ValueError("base_url 禁止包含用户名或密码")
    return value


def _validate_pricing(pricing: dict[str, Any]) -> dict[str, Any]:
    if _contains_secret(pricing):
        raise ValueError("pricing 禁止包含密钥字段")
    for key, value in pricing.items():
        if key in {"currency", "version", "unit", "source", "status"}:
            continue
        if isinstance(value, (dict, list, bool)):
            raise ValueError(f"pricing.{key} 必须是非负数字或说明字符串")
        if isinstance(value, str) and not value.strip():
            continue
        try:
            if Decimal(str(value)) < 0:
                raise ValueError(f"pricing.{key} 不能为负数")
        except InvalidOperation as error:
            raise ValueError(f"pricing.{key} 必须是数字") from error
    return pricing


class DeploymentInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    slug: str = Field(min_length=3, max_length=128)
    driver: str = Field(min_length=1, max_length=64)
    modality: str = Field(min_length=1, max_length=32)
    model: str = Field(min_length=1, max_length=255)
    base_url: str = Field(default="", max_length=2048)
    credential_ref: str = Field(default="", max_length=255)
    enabled: bool = True
    priority: int = Field(default=100, ge=-10_000, le=10_000)
    config: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    pricing: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "model")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        value = value.strip().lower()
        if not SLUG_PATTERN.fullmatch(value):
            raise ValueError("slug 只能包含小写字母、数字、下划线和连字符")
        return value

    @model_validator(mode="after")
    def validate_driver_payload(self):
        spec = DRIVER_SPECS.get(self.driver)
        if spec is None:
            raise ValueError(f"未知 Driver: {self.driver}")
        if self.modality not in MODALITIES or self.modality not in spec.modalities:
            raise ValueError(f"Driver {self.driver} 不支持模态 {self.modality}")
        self.base_url = _validate_url(self.base_url, required=spec.base_url_required)
        self.credential_ref = self.credential_ref.strip()
        if spec.credential_required and not self.credential_ref:
            raise ValueError("credential_ref 不能为空")
        if self.credential_ref and not CREDENTIAL_PATTERN.fullmatch(self.credential_ref):
            raise ValueError("credential_ref 必须使用 env:NAME 或 secret:path 格式")
        if _contains_secret(self.config) or _contains_secret(self.capabilities):
            raise ValueError("config/capabilities 禁止包含密钥字段")
        self.config = spec.config_schema.model_validate(self.config).model_dump(mode="json")
        self.capabilities = spec.capabilities_schema.model_validate(self.capabilities).model_dump(mode="json")
        self.pricing = _validate_pricing(self.pricing)
        return self


class DeploymentPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    slug: str | None = Field(default=None, min_length=3, max_length=128)
    driver: str | None = Field(default=None, min_length=1, max_length=64)
    modality: str | None = Field(default=None, min_length=1, max_length=32)
    model: str | None = Field(default=None, min_length=1, max_length=255)
    base_url: str | None = Field(default=None, max_length=2048)
    credential_ref: str | None = Field(default=None, max_length=255)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=-10_000, le=10_000)
    config: dict[str, Any] | None = None
    capabilities: dict[str, Any] | None = None
    pricing: dict[str, Any] | None = None


class DeploymentEnable(BaseModel):
    enabled: bool


class DeploymentDTO(BaseModel):
    id: str
    name: str
    slug: str
    driver: str
    modality: str
    model: str
    base_url: str
    credential_configured: bool
    enabled: bool
    priority: int
    config: dict[str, Any]
    capabilities: dict[str, Any]
    pricing: dict[str, Any]
    revision: int
    config_hash: str
    created_at: datetime
    updated_at: datetime


def config_hash(payload: DeploymentInput) -> str:
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def driver_summaries() -> list[dict[str, Any]]:
    return [
        {
            "driver": name,
            "modalities": sorted(spec.modalities),
            "config_schema": spec.config_schema.model_json_schema(),
            "capabilities_schema": spec.capabilities_schema.model_json_schema(),
            "base_url_required": spec.base_url_required,
            "credential_required": spec.credential_required,
        }
        for name, spec in sorted(DRIVER_SPECS.items())
    ]
