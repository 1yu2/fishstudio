"""把旧环境变量一次性、幂等地迁移为 Model Deployment。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import mock_providers_enabled
from app.db.models import ModelDeployment, Setting
from app.model_runtime.deployment_config import DeploymentInput, config_hash

logger = logging.getLogger(__name__)

BOOTSTRAP_VERSION = 2
BOOTSTRAP_SETTING_KEY = "model_deployment_bootstrap_version"


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _enabled(name: str) -> bool:
    return _env(name).lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class BootstrapSpec:
    payload: DeploymentInput
    source_variables: tuple[str, ...]


def legacy_deployment_specs() -> list[BootstrapSpec]:
    """显式旧变量映射；只返回当前环境实际配置或明确启用的服务。"""
    specs: list[BootstrapSpec] = []

    def add(source_variables: tuple[str, ...], **values: Any) -> None:
        specs.append(BootstrapSpec(DeploymentInput.model_validate(values), source_variables))

    if _env("OPENAI_API_KEY"):
        add(
            ("OPENAI_API_KEY", "OPENAI_BASE_URL", "MODEL_NAME"),
            name="SiliconFlow Chat",
            slug="siliconflow-default",
            driver="openai_compatible",
            modality="chat",
            model=_env("MODEL_NAME", "deepseek-ai/DeepSeek-V3.1-Terminus"),
            base_url=_env("OPENAI_BASE_URL", "https://api.siliconflow.cn/v1"),
            credential_ref="env:OPENAI_API_KEY",
            config={},
            capabilities={"tools": True},
            pricing={},
        )
    if _env("VOLCANO_API_KEY"):
        base_url = _env("VOLCANO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
        add(
            ("VOLCANO_API_KEY", "VOLCANO_BASE_URL", "VOLCANO_MODEL_NAME"),
            name="Volcano Chat",
            slug="volcano-chat",
            driver="openai_compatible",
            modality="chat",
            model=_env("VOLCANO_MODEL_NAME", "doubao-seed-1-6-251015"),
            base_url=base_url,
            credential_ref="env:VOLCANO_API_KEY",
            config={},
            capabilities={"tools": True},
            pricing={},
            priority=110,
        )
        add(
            ("VOLCANO_API_KEY", "VOLCANO_BASE_URL", "VOLCANO_IMAGE_MODEL"),
            name="Volcano Image",
            slug="volcano-image",
            driver="volcano_image",
            modality="image",
            model=_env("VOLCANO_IMAGE_MODEL", "seedream-4.5"),
            base_url=base_url,
            credential_ref="env:VOLCANO_API_KEY",
            config={},
            capabilities={"operations": ["generate", "edit"]},
            pricing={"currency": "CNY", "output_per_image": "0.20"},
        )
        add(
            ("VOLCANO_API_KEY", "VOLCANO_BASE_URL", "VOLCANO_VIDEO_MODEL"),
            name="Volcano Video",
            slug="volcano-video",
            driver="volcano_video",
            modality="video",
            model=_env("VOLCANO_VIDEO_MODEL", "doubao-seedance-1-5-pro"),
            base_url=base_url,
            credential_ref="env:VOLCANO_API_KEY",
            config={"protocol": "volcano"},
            capabilities={"min_duration": 4, "max_duration": 12},
            pricing={"currency": "CNY", "output_per_second": "0.50"},
            priority=120,
        )
    else:
        # 保留旧 Runtime 的未配置火山视频占位：管理页显示“未配置”，
        # 测试/显式 Mock 模式仍可锁定该协议后在执行前回退到 Mock。
        add(
            ("VOLCANO_API_KEY", "VOLCANO_BASE_URL", "VOLCANO_VIDEO_MODEL"),
            name="Volcano Video",
            slug="volcano-video",
            driver="volcano_video",
            modality="video",
            model=_env("VOLCANO_VIDEO_MODEL", "doubao-seedance-1-5-pro"),
            base_url=_env("VOLCANO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            credential_ref="env:VOLCANO_API_KEY",
            config={"protocol": "volcano"},
            capabilities={"min_duration": 4, "max_duration": 12},
            pricing={"currency": "CNY", "output_per_second": "0.50"},
            priority=120,
            enabled=False,
        )
    if _env("MINIMAX_API_KEY"):
        add(
            ("MINIMAX_API_KEY", "MINIMAX_BASE_URL", "MINIMAX_V1_MODEL"),
            name="MiniMax Video",
            slug="minimax-video",
            driver="minimax_video",
            modality="video",
            model=_env("MINIMAX_V1_MODEL", "MiniMax-Hailuo-2.3"),
            base_url=_env("MINIMAX_BASE_URL", "https://api.minimaxi.com"),
            credential_ref="env:MINIMAX_API_KEY",
            config={"protocol": "v1"},
            capabilities={"modes": ["text", "first_frame"], "min_duration": 6, "max_duration": 10},
            pricing={"currency": "CNY", "per_piece_6": "2.00", "per_piece_10": "4.00"},
            priority=130,
        )
    if _enabled("LOCAL_H3_ENABLED") or _env("LOCAL_H3_BASE_URL"):
        cost = _env("LOCAL_H3_COST_CNY_PER_SECOND")
        pricing = {"currency": "CNY", "source": "hardware_profile"}
        if cost:
            pricing["output_per_second"] = cost
        add(
            (
                "LOCAL_H3_ENABLED",
                "LOCAL_H3_DEPLOYMENT",
                "LOCAL_H3_BASE_URL",
                "LOCAL_H3_API_KEY",
                "LOCAL_H3_MODEL",
                "LOCAL_H3_MAX_CONCURRENCY",
                "LOCAL_H3_MIN_DURATION",
                "LOCAL_H3_MAX_DURATION",
                "LOCAL_H3_COST_CNY_PER_SECOND",
            ),
            name="Local MiniMax H3",
            slug=_env("LOCAL_H3_DEPLOYMENT", "h3-fl2va-int8-convrot"),
            driver="local_h3_gateway",
            modality="video",
            model=_env("LOCAL_H3_MODEL", "MiniMax-H3"),
            base_url=_env("LOCAL_H3_BASE_URL"),
            credential_ref="env:LOCAL_H3_API_KEY",
            enabled=_enabled("LOCAL_H3_ENABLED"),
            priority=10,
            config={
                "max_concurrency": int(_env("LOCAL_H3_MAX_CONCURRENCY", "1")),
                "min_duration": int(_env("LOCAL_H3_MIN_DURATION", "4")),
                "max_duration": int(_env("LOCAL_H3_MAX_DURATION", "6")),
                "timeout_seconds": float(_env("LOCAL_H3_TIMEOUT_SECONDS", "30")),
                "artifact_timeout_seconds": float(_env("LOCAL_H3_ARTIFACT_TIMEOUT_SECONDS", "900")),
                "max_artifact_bytes": int(_env("LOCAL_H3_MAX_ARTIFACT_BYTES", "536870912")),
                "max_inline_image_bytes": int(_env("LOCAL_H3_MAX_INLINE_IMAGE_BYTES", "8388608")),
                "artifact_allowed_hosts": [
                    value.strip()
                    for value in _env("LOCAL_H3_ARTIFACT_ALLOWED_HOSTS").split(",")
                    if value.strip()
                ],
            },
            capabilities={
                "modes": [
                    value.strip() for value in _env("LOCAL_H3_MODES", "text").split(",") if value.strip()
                ]
            },
            pricing=pricing,
        )
    if _env("DASHSCOPE_API_KEY"):
        add(
            ("DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL"),
            name="Qwen TTS",
            slug="qwen-tts",
            driver="qwen_tts",
            modality="speech",
            model="qwen-tts",
            base_url=_env("DASHSCOPE_BASE_URL"),
            credential_ref="env:DASHSCOPE_API_KEY",
            config={},
            capabilities={},
            pricing={},
        )
    if _env("TENCENT_AI3D_API_KEY"):
        add(
            ("TENCENT_AI3D_API_KEY", "TENCENT_AI3D_BASE_URL"),
            name="Tencent AI3D",
            slug="tencent-ai3d",
            driver="tencent_ai3d",
            modality="model_3d",
            model="hunyuan-3d",
            base_url=_env("TENCENT_AI3D_BASE_URL", "https://api.ai3d.cloud.tencent.com"),
            credential_ref="env:TENCENT_AI3D_API_KEY",
            config={},
            capabilities={},
            pricing={},
        )
    if mock_providers_enabled():
        logger.info("Mock Provider 保持兼容模式，不写入生产 Deployment Catalog")
    return specs


async def bootstrap_legacy_deployments(session: AsyncSession) -> int:
    """仅插入缺失 slug；永不覆盖管理员修改过的 Deployment。"""
    existing = set((await session.execute(select(ModelDeployment.slug))).scalars().all())
    inserted = 0
    for spec in legacy_deployment_specs():
        payload = spec.payload
        if payload.slug in existing:
            continue
        row = ModelDeployment(**payload.model_dump(), revision=1, config_hash=config_hash(payload))
        session.add(row)
        existing.add(payload.slug)
        inserted += 1
        logger.info(
            "bootstrap model deployment slug=%s driver=%s sources=%s",
            payload.slug,
            payload.driver,
            ",".join(spec.source_variables),
        )
    marker = await session.get(Setting, BOOTSTRAP_SETTING_KEY)
    if marker is None:
        session.add(Setting(key=BOOTSTRAP_SETTING_KEY, value=BOOTSTRAP_VERSION))
    else:
        marker.value = max(int(marker.value or 0), BOOTSTRAP_VERSION)
    await session.commit()
    return inserted


async def bootstrap_default_routes(session: AsyncSession) -> int:
    """Create first-run purpose bindings from imported deployments.

    Existing routes are intentionally left untouched so an administrator's
    choice survives later restarts and environment changes.
    """
    from app.db.models import ModelRoute

    deployments = list(
        (await session.execute(select(ModelDeployment).where(ModelDeployment.enabled.is_(True))))
        .scalars()
        .all()
    )
    existing = set(
        (await session.execute(select(ModelRoute.purpose).where(ModelRoute.tier == "final"))).scalars().all()
    )
    by_modality: dict[str, list[ModelDeployment]] = {}
    for deployment in deployments:
        by_modality.setdefault(deployment.modality, []).append(deployment)
    for values in by_modality.values():
        values.sort(key=lambda item: (item.priority, item.slug))

    def supports(deployment: ModelDeployment, capability: str) -> bool:
        capabilities = deployment.capabilities or {}
        return capabilities.get(capability) is True or capability in capabilities.get("operations", [])

    candidates: dict[str, list[ModelDeployment]] = {
        "chat": by_modality.get("chat", []),
        "planner": [item for item in by_modality.get("chat", []) if supports(item, "tools")],
        "director": by_modality.get("chat", []),
        "reviewer": [item for item in by_modality.get("chat", []) if supports(item, "vision")],
        "image_generation": [item for item in by_modality.get("image", []) if supports(item, "generate")],
        "image_edit": [item for item in by_modality.get("image", []) if supports(item, "edit")],
        "shot_video": by_modality.get("video", []),
        "speech": by_modality.get("speech", []),
        "model_3d": by_modality.get("model_3d", []),
    }
    inserted = 0
    for purpose, items in candidates.items():
        if purpose in existing or not items:
            continue
        primary = items[0]
        session.add(
            ModelRoute(
                purpose=purpose,
                tier="final",
                modality=primary.modality,
                primary_deployment=primary.slug,
                fallback_deployments=[item.slug for item in items[1:]],
                enabled=True,
                revision=1,
            )
        )
        inserted += 1
        logger.info("bootstrap model route purpose=%s primary=%s", purpose, primary.slug)
    await session.commit()
    return inserted
