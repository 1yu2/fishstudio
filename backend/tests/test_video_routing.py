"""视频用途 Route Resolver：按 (shot_video, tier) Route 与 Catalog 选择 Deployment。"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType, SimpleNamespace

import pytest

from app.model_runtime import video as video_module
from app.model_runtime.catalog import CatalogDeployment, CatalogSnapshot
from app.model_runtime.video import VideoRoutingError, resolve_video_plan
from app.services import model_route_service


class FakeVideoProvider:
    def __init__(self, name, *, available=True, modes=("text",), max_duration=12):
        self.name = name
        self.available = available
        self.modes = set(modes)
        self.max_duration = max_duration

    def is_available(self):
        return self.available

    def supports(self, req, *, model=None):
        return self.available and req.mode in self.modes and req.duration <= self.max_duration


def _deployment(slug, provider, *, pricing=None, enabled=True, model=None, revision=1):
    return CatalogDeployment(
        id=slug,
        slug=slug,
        name=slug,
        driver=f"{provider.name}_driver",
        modality="video",
        model=model or f"{slug}-model",
        base_url="https://example.invalid",
        enabled=enabled,
        priority=100,
        capabilities=MappingProxyType({}),
        pricing=MappingProxyType({"output_per_second": "0.5"} if pricing is None else pricing),
        revision=revision,
        config_hash=f"hash-{slug}",
        instance=provider,
    )


def _route(primary, fallbacks=(), *, tier="final", enabled=True, revision=3):
    return SimpleNamespace(
        purpose="shot_video",
        tier=tier,
        modality="video",
        primary_deployment=primary,
        fallback_deployments=list(fallbacks),
        enabled=enabled,
        revision=revision,
    )


@pytest.fixture
def configure(monkeypatch):
    """装配 Catalog 与 Route；routes 以 tier 为键，未配置的档位回退到 final。"""

    def _configure(deployments, routes):
        snapshot = CatalogSnapshot(
            MappingProxyType({item.slug: item for item in deployments}),
            "catalog-rev",
            datetime.now(timezone.utc),
        )
        monkeypatch.setattr(video_module, "get_catalog", lambda: SimpleNamespace(snapshot=snapshot))

        async def _effective(session, purpose, tier="final"):
            return routes.get(tier) or routes.get("final")

        monkeypatch.setattr(model_route_service, "get_effective_route", _effective)

    return _configure


def _resolve(**kwargs):
    kwargs.setdefault("duration", 5)
    return asyncio.run(resolve_video_plan(None, **kwargs))


def test_unconfigured_primary_is_skipped_for_configured_fallback(configure):
    """主 Deployment 缺密钥时不能被选中（原静态路由会直接选 Volcano 导致规划失败）。"""
    volcano = _deployment("volcano-seedance", FakeVideoProvider("volcano", available=False))
    minimax = _deployment("minimax-hailuo", FakeVideoProvider("minimax"))
    configure([volcano, minimax], {"final": _route("volcano-seedance", ["minimax-hailuo"])})

    plan = _resolve()

    assert plan["deployment_name"] == "minimax-hailuo"
    assert plan["provider"] == "minimax"
    assert plan["route"]["fallback_used"] is True
    assert plan["route"]["skipped"] == [{"deployment": "volcano-seedance", "reason": "unavailable"}]


def test_first_frame_shot_routes_to_image_to_video_capable_deployment(configure):
    text_only = _deployment("local-h3", FakeVideoProvider("local_h3", modes=("text",)))
    i2v = _deployment("seedance", FakeVideoProvider("volcano", modes=("text", "first_frame")))
    configure([text_only, i2v], {"final": _route("local-h3", ["seedance"])})

    assert _resolve(mode="text")["deployment_name"] == "local-h3"
    plan = _resolve(mode="first_frame")
    assert plan["deployment_name"] == "seedance"
    assert plan["route"]["skipped"] == [{"deployment": "local-h3", "reason": "unsupported_request"}]


def test_deployment_without_pricing_is_never_selected(configure):
    free = _deployment("no-price", FakeVideoProvider("volcano"), pricing={})
    configure([free], {"final": _route("no-price")})

    with pytest.raises(VideoRoutingError) as excinfo:
        _resolve()

    assert "no-price(missing_pricing)" in str(excinfo.value)
    assert excinfo.value.skipped == [{"deployment": "no-price", "reason": "missing_pricing"}]


def test_cost_comes_from_deployment_pricing(configure):
    per_second = _deployment(
        "per-second", FakeVideoProvider("volcano"), pricing={"output_per_second": "0.3", "version": "v9"}
    )
    configure([per_second], {"final": _route("per-second")})

    plan = _resolve(duration=5)

    assert plan["estimated_total_cost"] == Decimal("1.500000")
    assert plan["price_table_version"] == "v9"
    assert plan["price_snapshot"]["unit_prices"]["output_per_second"] == "0.3"
    assert plan["deployment_revision"] == 1
    assert plan["deployment_config_hash"] == "hash-per-second"
    assert plan["catalog_revision"] == "catalog-rev"


def test_per_piece_pricing_skips_unpriced_duration(configure):
    piece = _deployment(
        "hailuo", FakeVideoProvider("minimax"), pricing={"per_piece_6": "2", "per_piece_10": "4"}
    )
    configure([piece], {"final": _route("hailuo")})

    assert _resolve(duration=6)["estimated_total_cost"] == Decimal("2.000000")
    with pytest.raises(VideoRoutingError, match="unpriced_duration"):
        _resolve(duration=5)


def test_preview_tier_uses_its_own_route_and_falls_back_to_final(configure):
    local = _deployment("local-h3", FakeVideoProvider("local_h3"), pricing={"output_per_second": "0.01"})
    cloud = _deployment("seedance", FakeVideoProvider("volcano"))
    configure([local, cloud], {"final": _route("seedance"), "preview": _route("local-h3", tier="preview")})

    preview = _resolve(tier="preview")
    assert preview["deployment_name"] == "local-h3"
    assert preview["route"]["tier"] == "preview"
    assert _resolve(tier="final")["deployment_name"] == "seedance"

    configure([local, cloud], {"final": _route("seedance")})
    draft = _resolve(tier="draft")
    assert draft["deployment_name"] == "seedance"
    assert draft["route"] == {**draft["route"], "requested_tier": "draft", "tier": "final"}


def test_explicit_deployment_by_slug_and_by_legacy_provider_name(configure):
    seedance = _deployment("seedance", FakeVideoProvider("volcano"))
    hailuo = _deployment("hailuo", FakeVideoProvider("minimax"))
    configure([seedance, hailuo], {"final": _route("seedance", ["hailuo"])})

    assert _resolve(explicit_deployment="hailuo")["deployment_name"] == "hailuo"
    assert _resolve(explicit_deployment="minimax")["deployment_name"] == "hailuo"
    with pytest.raises(VideoRoutingError, match="missing"):
        _resolve(explicit_deployment="does-not-exist")


def test_disabled_route_is_rejected(configure):
    seedance = _deployment("seedance", FakeVideoProvider("volcano"))
    configure([seedance], {"final": _route("seedance", enabled=False)})

    with pytest.raises(VideoRoutingError, match="已停用"):
        _resolve()


def test_deployments_without_route_is_a_configuration_error(configure):
    configure([_deployment("seedance", FakeVideoProvider("volcano"))], {})

    with pytest.raises(VideoRoutingError, match="未配置用途 Route"):
        _resolve()


def test_empty_catalog_keeps_legacy_static_routing(configure, monkeypatch):
    configure([], {})
    calls = []

    def _legacy(policy, resolution, *, duration, explicit_provider=""):
        calls.append((policy, resolution, duration, explicit_provider))
        return {"provider": "legacy"}

    monkeypatch.setattr(video_module, "resolve_video_routing", _legacy)

    assert _resolve(provider_policy="cheapest")["provider"] == "legacy"
    assert _resolve(explicit_deployment="minimax")["provider"] == "legacy"
    assert calls == [("cheapest", "768P", 5, ""), ("explicit", "768P", 5, "minimax")]


def test_worker_prefers_locked_deployment_slug(monkeypatch):
    from app.worker import generation_worker as worker

    first = FakeVideoProvider("volcano")
    second = FakeVideoProvider("volcano")
    deployments = {
        "seedance-a": SimpleNamespace(provider=first),
        "seedance-b": SimpleNamespace(provider=second),
    }

    def _get(modality, name):
        if name in deployments:
            return deployments[name]
        # 按 Provider 名查找时只能拿到第一个同名实例。
        return next((item for item in deployments.values() if item.provider.name == name), None)

    monkeypatch.setattr(worker, "_media_runtime", SimpleNamespace(get=_get))

    assert worker._resolve_locked_video_provider("volcano", "seedance-b") == (second, False)
    assert worker._resolve_locked_video_provider("volcano") == (first, False)


def test_quote_route_plan_locks_selected_deployment(configure, monkeypatch):
    """报价里的逐镜计划记录选中的 Deployment，确认后 Worker 按 slug 执行。"""
    from app.services import cost_service, job_planner

    seedance = _deployment("seedance", FakeVideoProvider("volcano", modes=("text", "first_frame")))
    configure([seedance], {"final": _route("seedance")})
    shot = SimpleNamespace(id="sh1", duration_ms=5000, visual_prompt="p", aspect_ratio="9:16")

    async def _shots(session, episode_id):
        return [shot]

    async def _inputs(session, shot):
        return "images/first.png", None

    monkeypatch.setattr(job_planner, "_current_revision_shots", _shots)
    monkeypatch.setattr(job_planner, "load_shot_video_inputs", _inputs)
    step = SimpleNamespace(
        node_key="shot.video.generate:ep_e1", node_type="shot.video.generate", entity_id="e1"
    )

    item = asyncio.run(cost_service._estimate_step_item(None, SimpleNamespace(id="r1"), step, {}))

    plan = item.route_plans[0]
    assert plan.deployment_name == "seedance"
    assert plan.route["tier"] == "final"
    assert Decimal(item.subtotal) == Decimal("2.500000")
