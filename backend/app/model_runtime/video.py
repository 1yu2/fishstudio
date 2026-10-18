"""视频用途的 Route Resolver：报价与 Job 规划共用同一份路由计划。

按 (shot_video, tier) 的数据库 Route 依次检查候选 Deployment，跳过未启用、密钥缺失、
能力不满足或没有价格的候选，返回第一个可执行的计划。Worker 不重新路由，只执行计划
里锁定的 Deployment（远端拿到 task_id 后禁止切换，避免重复付费）。

Catalog 中还没有任何视频 Deployment 时（尚未完成配置迁移）回退到旧的静态路由。
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.model_runtime.catalog import CatalogDeployment, get_catalog
from app.model_runtime.routes import DEFAULT_TIER
from app.providers.base import VideoGenerationRequest
from app.providers.pricing import (
    PRICE_TABLE_VERSION,
    estimate_video_cost_from_entry,
    estimate_video_tokens,
    pricing_entry,
    resolve_video_routing,
    snapshot_entry,
    video_protocol,
)

VIDEO_PURPOSE = "shot_video"


class VideoRoutingError(ValueError):
    """没有可执行的视频 Deployment；message 列出每个候选被跳过的原因。"""

    def __init__(self, message: str, skipped: Optional[list[dict[str, str]]] = None) -> None:
        super().__init__(message)
        self.skipped = skipped or []


def _probe_request(
    *, duration: int, mode: str, aspect_ratio: str, resolution: str, tier: str, reference_count: int
) -> VideoGenerationRequest:
    """构造只用于能力判断的请求；不包含真实素材。"""
    return VideoGenerationRequest(
        mode=mode,
        prompt="routing-probe",
        first_frame="routing-probe" if mode in {"first_frame", "first_last_frame"} else None,
        last_frame="routing-probe" if mode == "first_last_frame" else None,
        reference_images=["routing-probe"] * reference_count,
        duration=duration,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        quality="preview" if tier in {"draft", "preview"} else "standard",
    )


def _candidate_slugs(route: Any, explicit: str, deployments: tuple[CatalogDeployment, ...]) -> list[str]:
    if not explicit:
        return [route.primary_deployment, *route.fallback_deployments]
    by_slug = {item.slug for item in deployments}
    if explicit in by_slug:
        return [explicit]
    # 兼容旧的 explicit_provider（minimax / volcano / local_h3）：按 Provider 名匹配，
    # Route 内的候选优先。
    ordered = [route.primary_deployment, *route.fallback_deployments] + sorted(by_slug)
    snapshot = {item.slug: item for item in deployments}
    for slug in dict.fromkeys(ordered):
        item = snapshot.get(slug)
        if item is not None and explicit in {item.driver, getattr(item.instance, "name", "")}:
            return [slug]
    return [explicit]


def _plan_for(
    item: CatalogDeployment,
    req: VideoGenerationRequest,
    *,
    resolution: str,
    duration: int,
    reference_count: int,
) -> tuple[Optional[dict[str, Any]], str]:
    """返回 (计划, 跳过原因)；计划为 None 时原因非空。"""
    if not item.enabled:
        return None, "disabled"
    instance = item.instance
    is_available = getattr(instance, "is_available", None)
    if is_available is not None and not is_available():
        return None, "unavailable"
    if not instance.supports(req, model=item.model):
        return None, "unsupported_request"
    entry = pricing_entry(item.pricing)
    if entry is None:
        return None, "missing_pricing"
    provider_name = getattr(instance, "name", "") or item.driver
    resolution_for = getattr(instance, "resolution_for", None)
    plan_resolution = resolution_for(req, item.model) if resolution_for else resolution
    input_images = reference_count + (1 if req.first_frame else 0) + (1 if req.last_frame else 0)
    cost = estimate_video_cost_from_entry(
        entry,
        output_seconds=duration,
        input_image_count=input_images,
        video_tokens=estimate_video_tokens(plan_resolution, duration),
    )
    if cost is None:
        return None, "unpriced_duration"
    version = str(item.pricing.get("version") or PRICE_TABLE_VERSION)
    return {
        "provider": provider_name,
        "model": item.model,
        "resolution": plan_resolution,
        "protocol": video_protocol(provider_name, item.model),
        "billable_duration": duration,
        "estimated_total_cost": cost,
        "price_snapshot": snapshot_entry(entry, version=version),
        "price_table_version": version,
        "deployment_name": item.slug,
        "deployment_revision": item.revision,
        "deployment_config_hash": item.config_hash,
    }, ""


async def resolve_video_plan(
    session: AsyncSession,
    *,
    duration: int,
    resolution: str = "768P",
    tier: str = DEFAULT_TIER,
    mode: str = "text",
    aspect_ratio: str = "9:16",
    reference_count: int = 0,
    explicit_deployment: str = "",
    provider_policy: str = "balanced",
) -> dict[str, Any]:
    """为一个镜头请求选出视频 Deployment 并计算报价，返回 routing_snapshot。"""
    from app.services import model_route_service

    snapshot = get_catalog().snapshot
    video_deployments = snapshot.list("video")
    route = await model_route_service.get_effective_route(session, VIDEO_PURPOSE, tier)

    if route is None:
        if video_deployments:
            raise VideoRoutingError(f"已有视频 Deployment，但未配置用途 Route: {VIDEO_PURPOSE}")
        # 兼容期：Catalog 为空（尚未迁移到模型服务配置）时沿用旧的静态路由。
        policy = "explicit" if explicit_deployment else provider_policy
        return resolve_video_routing(
            policy, resolution, duration=duration, explicit_provider=explicit_deployment
        )
    if not route.enabled:
        raise VideoRoutingError(f"视频用途 Route 已停用: {VIDEO_PURPOSE}/{route.tier}")

    req = _probe_request(
        duration=duration,
        mode=mode,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        tier=tier,
        reference_count=reference_count,
    )
    slugs = _candidate_slugs(route, explicit_deployment, video_deployments)
    skipped: list[dict[str, str]] = []
    for slug in slugs:
        item = snapshot.get(slug)
        if item is None or item.modality != "video":
            skipped.append({"deployment": slug, "reason": "missing"})
            continue
        plan, reason = _plan_for(
            item, req, resolution=resolution, duration=duration, reference_count=reference_count
        )
        if plan is None:
            skipped.append({"deployment": slug, "reason": reason})
            continue
        plan.update(
            {
                "catalog_revision": snapshot.catalog_revision,
                "route": {
                    "purpose": VIDEO_PURPOSE,
                    "requested_tier": tier,
                    "tier": route.tier,
                    "route_revision": route.revision,
                    "candidate_deployments": slugs,
                    "skipped": skipped,
                    "fallback_used": slug != slugs[0],
                },
            }
        )
        return plan
    detail = "，".join(f"{row['deployment']}({row['reason']})" for row in skipped) or "无候选"
    raise VideoRoutingError(
        f"{duration}s/{mode} 镜头没有可执行的视频 Deployment（{route.tier} 档）：{detail}",
        skipped,
    )
