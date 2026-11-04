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
from app.providers.pricing import DEFAULT_VOLCANO_IMAGE_MODEL, IMAGE_PRICE_TABLE, env_image_price

logger = logging.getLogger(__name__)

BOOTSTRAP_VERSION = 2
REFERENCE_VIDEO_MODEL_DEFAULT = "doubao-seedance-2-0-mini-260615"
# 旧版 bootstrap 写入、方舟账号已无法访问的默认模型（0.8 实测 InvalidEndpointOrModel.NotFound）
OBSOLETE_DEFAULT_MODELS = {"doubao-seedance-1-5-pro", "seedream-4.5"}
BOOTSTRAP_SETTING_KEY = "model_deployment_bootstrap_version"


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _image_pricing(model: str) -> dict[str, str]:
    """图片部署定价：价格表 > VOLCANO_IMAGE_PRICE_CNY > 空（unavailable，绝不按 0 计）。"""
    unit = IMAGE_PRICE_TABLE.get(("volcano_image", model)) or env_image_price()
    if unit is None:
        return {}
    return {"currency": "CNY", "output_per_image": str(unit), "source": "volcengine-ark-2026-11"}


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
        image_model = _env("VOLCANO_IMAGE_MODEL", DEFAULT_VOLCANO_IMAGE_MODEL)
        add(
            ("VOLCANO_API_KEY", "VOLCANO_BASE_URL", "VOLCANO_IMAGE_MODEL", "VOLCANO_IMAGE_PRICE_CNY"),
            name="Volcano Image",
            slug="volcano-image",
            driver="volcano_image",
            modality="image",
            model=image_model,
            base_url=base_url,
            credential_ref="env:VOLCANO_API_KEY",
            config={},
            capabilities={"operations": ["generate", "edit"]},
            pricing=_image_pricing(image_model),
        )
        via_dmxapi = "dmxapi.cn" in base_url.lower() or _enabled("VOLCANO_USE_DMXAPI")
        add(
            ("VOLCANO_API_KEY", "VOLCANO_BASE_URL", "VOLCANO_VIDEO_MODEL"),
            name="Seedance (Volcano Ark)" if not via_dmxapi else "Volcano Video",
            slug="volcano-video",
            # 直连方舟使用原生异步 Adapter；DMXAPI 代理协议不同，仍走旧 Driver。
            driver="volcano_video" if via_dmxapi else "ark_video",
            modality="video",
            model=_env("VOLCANO_VIDEO_MODEL", "doubao-seedance-1-0-pro-fast-251015"),
            base_url=base_url,
            credential_ref="env:VOLCANO_API_KEY",
            config={"protocol": "volcano"} if via_dmxapi else {},
            # 1.0 Pro Fast 仅支持文生视频与首帧图生视频，时长 2-12 秒。
            capabilities={"modes": ["text", "first_frame"], "min_duration": 2, "max_duration": 12},
            # 方舟官方刊例：在线推理 4.20 元 / 百万 Token。
            pricing={
                "currency": "CNY",
                "output_per_million_tokens": "4.20",
                "source": "volcengine-ark-2026-11",
            },
            priority=120,
        )
        # Seedance 2.0 mini：支持参考图与首尾帧，1.0 pro-fast 不支持的请求落到它（直连方舟才有）。
        # VOLCANO_REFERENCE_VIDEO_MODEL 显式设为空串即关闭该部署。
        reference_model = os.getenv("VOLCANO_REFERENCE_VIDEO_MODEL", REFERENCE_VIDEO_MODEL_DEFAULT).strip()
        if reference_model and not via_dmxapi:
            add(
                ("VOLCANO_API_KEY", "VOLCANO_BASE_URL", "VOLCANO_REFERENCE_VIDEO_MODEL"),
                name="Seedance 2.0 Mini (Volcano Ark)",
                slug="volcano-video-reference",
                driver="ark_video",
                modality="video",
                model=reference_model,
                base_url=base_url,
                credential_ref="env:VOLCANO_API_KEY",
                config={},
                # 实测：时长 4-15 秒，仅 480p/720p；参考图与首尾帧不能在同一请求混用。
                capabilities={
                    "modes": ["text", "first_frame", "first_last_frame", "reference"],
                    "min_duration": 4,
                    "max_duration": 15,
                    "resolutions": ["480p", "720p"],
                    "max_reference_images": 9,
                },
                # 方舟官方刊例：23 元 / 百万 Token（无输入视频）。
                pricing={
                    "currency": "CNY",
                    "output_per_million_tokens": "23",
                    "source": "volcengine-ark-2026-11",
                },
                priority=125,
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
            model=_env("VOLCANO_VIDEO_MODEL", "doubao-seedance-1-0-pro-fast-251015"),
            base_url=_env("VOLCANO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            credential_ref="env:VOLCANO_API_KEY",
            config={"protocol": "volcano"},
            capabilities={"modes": ["text", "first_frame"], "min_duration": 2, "max_duration": 12},
            pricing={
                "currency": "CNY",
                "output_per_million_tokens": "4.20",
                "source": "volcengine-ark-2026-11",
            },
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
            ("DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL", "QWEN_TTS_MODEL"),
            name="Qwen TTS",
            slug="qwen-tts",
            driver="qwen_tts",
            modality="speech",
            model=_env("QWEN_TTS_MODEL", "qwen3-tts-flash"),
            base_url=_env("DASHSCOPE_BASE_URL"),
            credential_ref="env:DASHSCOPE_API_KEY",
            config={},
            capabilities={},
            # 百炼官方刊例：0.8 元 / 1 万输入字符，输出免费。
            pricing={"currency": "CNY", "per_10k_characters": "0.80", "source": "aliyun-bailian-2026-11"},
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


async def _append_to_final_video_route(session: AsyncSession, slug: str) -> None:
    """已有 shot_video final Route 不会被 bootstrap_default_routes 改动；新插入的视频部署只追加到 fallback 末尾。"""
    from app.db.models import ModelRoute

    route = (
        await session.execute(
            select(ModelRoute).where(ModelRoute.purpose == "shot_video", ModelRoute.tier == "final")
        )
    ).scalar_one_or_none()
    if route is None:
        return
    current = list(route.fallback_deployments or [])
    if slug == route.primary_deployment or slug in current:
        return
    # 重新赋值新列表，确保 JSONB 变更被检测到；只追加，不删除/重排。
    route.fallback_deployments = [*current, slug]
    route.revision = (route.revision or 1) + 1
    logger.info("bootstrap appended deployment=%s to shot_video fallback", slug)


def _is_stale_bootstrap_row(row: ModelDeployment, payload: DeploymentInput) -> bool:
    """管理员从未改过（revision == 1）的 bootstrap 行，且仍是未配置占位或已失效的旧默认模型。"""
    if row.revision != 1:
        return False
    placeholder_now_configured = not row.enabled and payload.enabled
    return placeholder_now_configured or row.model in OBSOLETE_DEFAULT_MODELS


def _refresh_bootstrap_row(row: ModelDeployment, payload: DeploymentInput) -> None:
    previous = row.model
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    row.revision += 1
    row.config_hash = config_hash(payload)
    logger.info(
        "bootstrap refreshed untouched deployment slug=%s model %s -> %s", row.slug, previous, row.model
    )


async def bootstrap_legacy_deployments(session: AsyncSession) -> int:
    """仅插入缺失 slug；永不覆盖管理员修改过的 Deployment。"""
    rows = {row.slug: row for row in (await session.execute(select(ModelDeployment))).scalars().all()}
    existing = set(rows)
    inserted = 0
    for spec in legacy_deployment_specs():
        payload = spec.payload
        if payload.slug in existing:
            row = rows.get(payload.slug)
            if row is not None and _is_stale_bootstrap_row(row, payload):
                _refresh_bootstrap_row(row, payload)
                if payload.modality == "video" and payload.enabled:
                    await _append_to_final_video_route(session, payload.slug)
            continue
        row = ModelDeployment(**payload.model_dump(), revision=1, config_hash=config_hash(payload))
        session.add(row)
        existing.add(payload.slug)
        inserted += 1
        if payload.modality == "video" and payload.enabled:
            await _append_to_final_video_route(session, payload.slug)
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
