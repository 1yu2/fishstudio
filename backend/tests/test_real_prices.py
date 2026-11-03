"""真实刊例价（P0 0.3）：Seedance 1.0 Pro Fast Token 计价、Seedream 单价、qwen3-tts-flash 按字符计费。

所有厂商 HTTP 均 mock，不产生真实调用。
"""

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType, SimpleNamespace

import pytest

from app.providers import pricing, tts_base
from app.providers.ark_video import ArkVideoProvider
from app.providers.base import VideoGenerationRequest
from app.providers.pricing import (
    estimate_image_cost,
    estimate_video_cost,
    estimate_video_tokens,
)
from app.services import cost_service

FAST_MODEL = "doubao-seedance-1-0-pro-fast-251015"
PER_MILLION = Decimal("4.20")


def _run(coro):
    return asyncio.run(coro)


def _expected(resolution: str, seconds: int) -> Decimal:
    pixels = {"480p": 854 * 480, "720p": 1280 * 720, "1080p": 1920 * 1080}[resolution]
    # 与 estimate_video_tokens 一致：(fps*秒 + 1) 帧，再乘 1.05 安全余量
    tokens = estimate_video_tokens(resolution, seconds)
    assert tokens == int(pixels * (24 * seconds + 1) / 1024 * 1.05)
    return (PER_MILLION * tokens / Decimal(1_000_000)).quantize(Decimal("0.000001"))


def test_price_table_version():
    assert pricing.PRICE_TABLE_VERSION == "2026-11-03"


@pytest.mark.parametrize("resolution", ["480p", "720p", "1080p"])
def test_fast_model_token_cost_matches_formula(resolution):
    cost = estimate_video_cost("volcano", FAST_MODEL, resolution, output_seconds=5)
    assert cost == _expected(resolution, 5)
    # 大小写不敏感
    assert estimate_video_cost("volcano", FAST_MODEL, resolution.upper(), output_seconds=5) == cost


def test_fast_model_per_second_reference_values():
    per_second = {r: _expected(r, 1) for r in ("480p", "720p", "1080p")}
    assert round(per_second["480p"], 4) == Decimal("0.0441")
    assert round(per_second["720p"], 4) == Decimal("0.0992")
    assert round(per_second["1080p"], 4) == Decimal("0.2233")


def test_unknown_resolution_falls_back_to_720p_for_token_pricing():
    # 策略默认的 768P 不是方舟档位：静态估算路径不得返回 None
    assert estimate_video_cost("volcano", FAST_MODEL, "768P", output_seconds=5) == _expected("720p", 5)


def test_actual_cost_from_completion_tokens():
    cost, basis = pricing.actual_video_cost(
        {"output_per_million_tokens": "4.20"}, {"completion_tokens": 108000}
    )
    assert basis == "tokens"
    assert cost == (PER_MILLION * 108000 / Decimal(1_000_000)).quantize(Decimal("0.000001"))


def test_static_routing_plan_has_price_for_fast_model(monkeypatch):
    from app.tools import volcano_video_generation as vvg

    monkeypatch.setattr(vvg, "ACTIVE_VIDEO_MODEL", FAST_MODEL)
    plan = pricing.resolve_video_routing("balanced", "720p", duration=6)
    assert plan["model"] == FAST_MODEL
    assert plan["estimated_total_cost"] == _expected("720p", 6)
    assert plan["price_snapshot"]["unit_prices"]["output_per_million_tokens"] == "4.20"


def test_default_video_model_is_fast():
    from app.tools import volcano_video_generation as vvg

    assert vvg.VOLCANO_VIDEO_MODEL == FAST_MODEL


def _bootstrap_video_spec(monkeypatch):
    from app.services import model_deployment_bootstrap as bootstrap

    for name in ("VOLCANO_VIDEO_MODEL", "VOLCANO_USE_DMXAPI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOLCANO_API_KEY", "test-key")
    monkeypatch.setenv("VOLCANO_BASE_URL", "https://ark.test/api/v3")
    monkeypatch.setattr(bootstrap, "mock_providers_enabled", lambda: False)
    specs = bootstrap.legacy_deployment_specs()
    return next(item.payload for item in specs if item.payload.slug == "volcano-video")


def test_bootstrap_video_deployment_uses_fast_model_and_real_price(monkeypatch):
    payload = _bootstrap_video_spec(monkeypatch)
    assert payload.driver == "ark_video"
    assert payload.model == FAST_MODEL
    assert payload.capabilities["modes"] == ["text", "first_frame"]
    assert (payload.capabilities["min_duration"], payload.capabilities["max_duration"]) == (2, 12)
    assert payload.pricing == {
        "currency": "CNY",
        "output_per_million_tokens": "4.20",
        "source": "volcengine-ark-2026-11",
    }
    # 带 source 文本字段的刊例仍能转成计价条目
    assert pricing.pricing_entry(payload.pricing)["output_per_million_tokens"] == PER_MILLION


def test_bootstrap_env_override_still_wins(monkeypatch):
    monkeypatch.setenv("VOLCANO_VIDEO_MODEL", "custom-model")
    monkeypatch.setenv("VOLCANO_API_KEY", "test-key")
    from app.services import model_deployment_bootstrap as bootstrap

    monkeypatch.setattr(bootstrap, "mock_providers_enabled", lambda: False)
    specs = bootstrap.legacy_deployment_specs()
    assert next(i.payload for i in specs if i.payload.slug == "volcano-video").model == "custom-model"


def _fast_provider(payload):
    return ArkVideoProvider(
        api_key="k",
        base_url="https://ark.test/api/v3",
        model_name=payload.model,
        deployment_name="volcano-video",
        capabilities=dict(payload.capabilities),
    )


def test_payload_omits_audio_and_sends_explicit_resolution(monkeypatch):
    provider = _fast_provider(_bootstrap_video_spec(monkeypatch))
    req = VideoGenerationRequest(prompt="雨夜", duration=5)
    payload = provider.build_payload(req, FAST_MODEL)
    assert "generate_audio" not in payload
    assert payload["resolution"] in {"480p", "720p", "1080p"}
    assert payload["duration"] == 5
    # 1.5 系列仍保持原行为
    assert provider.build_payload(req, "doubao-seedance-1-5-pro-251215")["generate_audio"] is False


def test_first_last_frame_not_supported_by_fast_deployment(monkeypatch):
    provider = _fast_provider(_bootstrap_video_spec(monkeypatch))
    text = VideoGenerationRequest(prompt="x", duration=5)
    first = VideoGenerationRequest(mode="first_frame", prompt="x", first_frame="f", duration=5)
    both = VideoGenerationRequest(
        mode="first_last_frame", prompt="x", first_frame="f", last_frame="l", duration=5
    )
    short = VideoGenerationRequest(prompt="x", duration=2)
    assert provider.supports(text, model=FAST_MODEL)
    assert provider.supports(first, model=FAST_MODEL)
    assert provider.supports(short, model=FAST_MODEL)
    assert not provider.supports(both, model=FAST_MODEL)


def test_route_plan_prices_fast_deployment_and_rejects_first_last_frame(monkeypatch):
    from app.model_runtime import video as video_module
    from app.model_runtime.catalog import CatalogDeployment, CatalogSnapshot
    from app.services import model_route_service

    payload = _bootstrap_video_spec(monkeypatch)
    provider = _fast_provider(payload)
    deployment = CatalogDeployment(
        id="fast",
        slug="fast",
        name="Seedance Fast",
        driver="ark_video",
        modality="video",
        model=FAST_MODEL,
        base_url="https://ark.test/api/v3",
        enabled=True,
        priority=100,
        capabilities=MappingProxyType(dict(payload.capabilities)),
        pricing=MappingProxyType(dict(payload.pricing)),
        revision=1,
        config_hash="h",
        instance=provider,
    )
    snapshot = CatalogSnapshot(MappingProxyType({"fast": deployment}), "rev", datetime.now(timezone.utc))
    monkeypatch.setattr(video_module, "get_catalog", lambda: SimpleNamespace(snapshot=snapshot))
    route = SimpleNamespace(
        purpose="shot_video",
        tier="final",
        modality="video",
        primary_deployment="fast",
        fallback_deployments=[],
        enabled=True,
        revision=1,
    )

    async def _effective(session, purpose, tier="final"):
        return route

    monkeypatch.setattr(model_route_service, "get_effective_route", _effective)

    for resolution in ("480p", "720P", "1080p"):
        plan = _run(video_module.resolve_video_plan(None, duration=5, resolution=resolution))
        assert plan["estimated_total_cost"] == _expected(resolution.lower(), 5)
        assert plan["price_snapshot"]["unit_prices"]["source"] == "volcengine-ark-2026-11"

    with pytest.raises(video_module.VideoRoutingError):
        _run(video_module.resolve_video_plan(None, duration=5, mode="first_last_frame"))


def test_estimate_job_retry_cost_for_token_priced_video():
    job = SimpleNamespace(
        operation="generate_video",
        provider="volcano",
        model=FAST_MODEL,
        params={"duration_ms": 5000, "resolution": "720p"},
    )
    assert cost_service.estimate_job_retry_cost(job) == _expected("720p", 5)


def test_seedream_price():
    # 旧 4.5 条目保留供历史 Job 重估
    assert estimate_image_cost("volcano_image", "seedream-4.5", count=1) == Decimal("0.250000")
    assert estimate_image_cost("volcano_image", "seedream-4.5", count=4) == Decimal("1.000000")


FLASH_IMAGE = "doubao-seedream-5-0-flash-260915"


def test_default_image_model_constant_and_price():
    assert pricing.DEFAULT_VOLCANO_IMAGE_MODEL == FLASH_IMAGE
    assert estimate_image_cost("volcano_image", FLASH_IMAGE, count=3) == Decimal("0.360000")


def test_image_default_model_used_everywhere(monkeypatch):
    from app.providers.volcano_image import VolcanoImageProvider
    from app.tools import volcano_image_generation as vig

    monkeypatch.delenv("VOLCANO_IMAGE_MODEL", raising=False)
    monkeypatch.setenv("VOLCANO_API_KEY", "test-key")
    assert vig.VOLCANO_IMAGE_MODEL == FLASH_IMAGE
    assert VolcanoImageProvider.from_legacy_env().model_name == FLASH_IMAGE
    monkeypatch.setenv("VOLCANO_IMAGE_MODEL", "ep-custom")
    assert VolcanoImageProvider.from_legacy_env().model_name == "ep-custom"


def test_unknown_image_model_unavailable_without_env_price(monkeypatch):
    monkeypatch.delenv("VOLCANO_IMAGE_PRICE_CNY", raising=False)
    assert estimate_image_cost("volcano_image", "ep-20260101-abc", count=1) is None
    monkeypatch.setenv("VOLCANO_IMAGE_PRICE_CNY", "not-a-number")
    assert estimate_image_cost("volcano_image", "ep-20260101-abc", count=1) is None
    monkeypatch.setenv("VOLCANO_IMAGE_PRICE_CNY", "0")
    assert estimate_image_cost("volcano_image", "ep-20260101-abc", count=1) is None


def test_env_image_price_is_fallback_only(monkeypatch):
    monkeypatch.setenv("VOLCANO_IMAGE_PRICE_CNY", "0.30")
    assert estimate_image_cost("volcano_image", "ep-20260101-abc", count=2) == Decimal("0.600000")
    # 价格表中的模型仍用官方价，不被环境变量覆盖
    assert estimate_image_cost("volcano_image", FLASH_IMAGE, count=1) == Decimal("0.120000")
    # 不影响其它 provider
    assert estimate_image_cost("mock_image", "x", count=1) is None


def _bootstrap_image_pricing(monkeypatch, **env):
    from app.services import model_deployment_bootstrap as bootstrap

    for name in ("VOLCANO_IMAGE_MODEL", "VOLCANO_IMAGE_PRICE_CNY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("VOLCANO_API_KEY", "test-key")
    monkeypatch.setattr(bootstrap, "mock_providers_enabled", lambda: False)
    return next(i.payload for i in bootstrap.legacy_deployment_specs() if i.payload.slug == "volcano-image")


def test_bootstrap_image_deployment_default_model_and_price(monkeypatch):
    payload = _bootstrap_image_pricing(monkeypatch)
    assert payload.model == FLASH_IMAGE
    assert payload.pricing["output_per_image"] == "0.12"


def test_bootstrap_image_deployment_endpoint_uses_env_price_or_empty(monkeypatch):
    payload = _bootstrap_image_pricing(monkeypatch, VOLCANO_IMAGE_MODEL="ep-1")
    assert payload.pricing == {}
    payload = _bootstrap_image_pricing(monkeypatch, VOLCANO_IMAGE_MODEL="ep-1", VOLCANO_IMAGE_PRICE_CNY="0.3")
    assert payload.pricing["output_per_image"] == "0.3"


# ── TTS ──────────────────────────────────────────────────────────────────


def test_tts_cost_per_character():
    assert cost_service.TTS_PRICE_PER_10K_CHARS == Decimal("0.80")
    assert cost_service.tts_cost(10_000) == Decimal("0.800000")
    assert cost_service.tts_cost(250) == (Decimal("0.80") * 250 / 10000).quantize(Decimal("0.000001"))
    assert cost_service.tts_char_count("  你好世界  ") == 4
    job = SimpleNamespace(
        operation="tts", provider="qwen_tts", model="", params={"text": " " + "字" * 100 + " "}
    )
    assert cost_service.estimate_job_retry_cost(job) == Decimal("0.008000")


def test_tts_quote_item_uses_characters(monkeypatch):
    shot = SimpleNamespace(
        id="s1",
        duration_ms=3000,
        dialogue_tracks=[{"text": " " + "好" * 50}],
        narration_tracks=[{"text": "旁白" * 10}, {"text": "   "}],
    )

    async def _shots(session, entity_id):
        return [shot]

    monkeypatch.setattr(cost_service.job_planner, "_current_revision_shots", _shots)
    step = SimpleNamespace(node_key="n", node_type="shot.audio.generate", entity_id="e1")
    item = _run(cost_service._estimate_step_item(None, SimpleNamespace(id="r"), step, {}))
    assert item.unit_count == 70
    assert item.unit_price == "0.80"
    assert Decimal(item.subtotal) == Decimal("0.005600")


def test_split_tts_text_respects_limit_and_sentences():
    sentence = "这是一个测试句子。"  # 9 字符
    text = sentence * 100  # 900 字符
    chunks = tts_base.split_tts_text(text, 600)
    assert len(chunks) == 2
    assert all(len(c) <= 600 for c in chunks)
    assert "".join(chunks) == text
    assert all(c.endswith("。") for c in chunks)
    # 单句超长硬切
    hard = tts_base.split_tts_text("字" * 1300, 600)
    assert [len(c) for c in hard] == [600, 600, 100]
    assert tts_base.split_tts_text("   ", 600) == []


class _FakeResponse:
    def __init__(self, *, json_data=None, content=b"RIFFfake", status=200):
        self.status_code = status
        self._json = json_data or {}
        self.content = content
        self.text = json.dumps(self._json)

    def json(self):
        return self._json

    def raise_for_status(self):
        return None


@pytest.fixture
def qwen_http(tmp_path, monkeypatch):
    """mock 百炼 HTTP，并把音频落盘重定向到临时目录。"""
    from app.tools import qwen_tts

    audios = tmp_path / "storage" / "audios"
    audios.mkdir(parents=True)
    monkeypatch.setattr(qwen_tts, "AUDIOS_DIR", audios)
    monkeypatch.setattr(qwen_tts, "DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(qwen_tts, "DASHSCOPE_BASE_URL", "https://dash.test")
    monkeypatch.setattr(tts_base, "BASE_DIR", tmp_path)
    calls: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json})
        return _FakeResponse(
            json_data={"output": {"audio": {"url": "https://cdn.test/a.wav"}}, "request_id": "req-1"}
        )

    monkeypatch.setattr(qwen_tts.requests, "post", fake_post)
    monkeypatch.setattr(qwen_tts.requests, "get", lambda url, timeout=None: _FakeResponse())
    return SimpleNamespace(calls=calls, audios=audios)


def test_default_tts_path_uses_flash_model_and_cherry(qwen_http):
    provider = tts_base.QwenTTSProvider(api_key="test-key")
    result = _run(provider.synthesize(tts_base.TTSRequest(text="你好，世界。")))
    assert result.success, result.error_message
    assert len(qwen_http.calls) == 1
    call = qwen_http.calls[0]
    assert call["url"].endswith("/api/v1/services/aigc/multimodal-generation/generation")
    assert call["json"]["model"] == "qwen3-tts-flash"
    assert call["json"]["input"]["voice"] == "Cherry"
    assert call["json"]["input"]["text"] == "你好，世界。"
    assert result.usage["characters"] == 6
    # 不再每次创建临时设计音色
    assert not any("customization" in c["url"] for c in qwen_http.calls)


def test_default_tts_path_env_override(qwen_http, monkeypatch):
    from app.tools import qwen_tts

    monkeypatch.setattr(qwen_tts, "QWEN_TTS_MODEL", "qwen3-tts-flash-x")
    monkeypatch.setattr(qwen_tts, "QWEN_TTS_DEFAULT_VOICE", "Ethan")
    result = _run(tts_base.QwenTTSProvider(api_key="k").synthesize(tts_base.TTSRequest(text="hi")))
    assert result.success
    assert qwen_http.calls[0]["json"]["model"] == "qwen3-tts-flash-x"
    assert qwen_http.calls[0]["json"]["input"]["voice"] == "Ethan"


def test_voice_id_path_keeps_bound_model(qwen_http):
    provider = tts_base.QwenTTSProvider(api_key="k")
    req = tts_base.TTSRequest(text="你好", voice_id="vc123", voice_model="qwen3-tts-vc-test")
    assert _run(provider.synthesize(req)).success
    call = qwen_http.calls[0]
    assert call["json"]["model"] == "qwen3-tts-vc-test"
    assert call["json"]["input"]["voice"] == "vc123"


def test_long_text_is_split_and_concatenated(qwen_http, monkeypatch):
    from app.tools import audio_mixing

    joined: dict = {}

    def fake_concat(files, crossfade, silence):
        joined["files"] = list(files)
        out = qwen_http.audios / "joined.wav"
        out.write_bytes(b"RIFFjoined")
        return json.dumps(
            {"audio_url": "/storage/audios/joined.wav", "local_path": "/storage/audios/joined.wav"}
        )

    monkeypatch.setattr(audio_mixing, "concatenate_audio_tool", SimpleNamespace(func=fake_concat))
    text = "这是一个测试句子。" * 100  # 900 字符
    result = _run(tts_base.QwenTTSProvider(api_key="k").synthesize(tts_base.TTSRequest(text=text)))
    assert result.success, result.error_message
    assert len(qwen_http.calls) == 2
    assert all(len(c["json"]["input"]["text"]) <= 600 for c in qwen_http.calls)
    assert "".join(c["json"]["input"]["text"] for c in qwen_http.calls) == text
    assert len(joined["files"]) == 2
    assert result.artifacts[0]["storage_key"] == "audios/joined.wav"
    assert result.usage["characters"] == 900


def test_qwen_tts_bootstrap_deployment(monkeypatch):
    from app.services import model_deployment_bootstrap as bootstrap

    monkeypatch.delenv("QWEN_TTS_MODEL", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(bootstrap, "mock_providers_enabled", lambda: False)
    payload = next(i.payload for i in bootstrap.legacy_deployment_specs() if i.payload.slug == "qwen-tts")
    assert payload.model == "qwen3-tts-flash"
    assert payload.pricing == {
        "currency": "CNY",
        "per_10k_characters": "0.80",
        "source": "aliyun-bailian-2026-11",
    }
    entry = pricing.pricing_entry(payload.pricing)
    assert entry["per_10k_characters"] == Decimal("0.80")
    assert entry["source"] == "aliyun-bailian-2026-11"


def test_seedance_estimate_tokens_has_frame_and_safety_margin():
    # (fps*秒 + 1) 帧 × 像素 / 1024 × 1.05
    assert estimate_video_tokens("720P", 5) == int(1280 * 720 * 121 / 1024 * 1.05)
    # 不低于 2026-11 方舟实测用量（480p 9:16）
    assert estimate_video_tokens("480p", 2) >= 19_845
    assert estimate_video_tokens("480p", 4) >= 40_594


# ── Seedance 2.0 mini（参考图）部署 ───────────────────────────────────────

MINI_MODEL = "doubao-seedance-2-0-mini-260615"


def _bootstrap_specs(monkeypatch, **env):
    from app.services import model_deployment_bootstrap as bootstrap

    for name in ("VOLCANO_VIDEO_MODEL", "VOLCANO_USE_DMXAPI", "VOLCANO_REFERENCE_VIDEO_MODEL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("VOLCANO_API_KEY", "test-key")
    monkeypatch.setenv("VOLCANO_BASE_URL", env.get("VOLCANO_BASE_URL", "https://ark.test/api/v3"))
    monkeypatch.setattr(bootstrap, "mock_providers_enabled", lambda: False)
    return {item.payload.slug: item.payload for item in bootstrap.legacy_deployment_specs()}


def test_bootstrap_reference_deployment(monkeypatch):
    specs = _bootstrap_specs(monkeypatch)
    payload = specs["volcano-video-reference"]
    assert (payload.driver, payload.model, payload.priority) == ("ark_video", MINI_MODEL, 125)
    assert specs["volcano-video"].priority == 120
    # 校验后会补齐 aspect_ratios 默认值，这里只断言显式声明的能力
    assert {
        key: payload.capabilities[key]
        for key in ("modes", "min_duration", "max_duration", "resolutions", "max_reference_images")
    } == {
        "modes": ["text", "first_frame", "first_last_frame", "reference"],
        "min_duration": 4,
        "max_duration": 15,
        "resolutions": ["480p", "720p"],
        "max_reference_images": 9,
    }
    assert pricing.pricing_entry(payload.pricing)["output_per_million_tokens"] == Decimal("23")
    assert payload.pricing["source"] == "volcengine-ark-2026-11"


def test_bootstrap_reference_deployment_env_override_and_opt_out(monkeypatch):
    specs = _bootstrap_specs(monkeypatch, VOLCANO_REFERENCE_VIDEO_MODEL="custom-mini")
    assert specs["volcano-video-reference"].model == "custom-mini"
    specs = _bootstrap_specs(monkeypatch, VOLCANO_REFERENCE_VIDEO_MODEL="")
    assert "volcano-video-reference" not in specs
    assert "volcano-video" in specs


def test_bootstrap_reference_deployment_skipped_for_dmxapi_and_without_key(monkeypatch):
    from app.services import model_deployment_bootstrap as bootstrap

    specs = _bootstrap_specs(monkeypatch, VOLCANO_USE_DMXAPI="true")
    assert "volcano-video-reference" not in specs
    specs = _bootstrap_specs(monkeypatch, VOLCANO_BASE_URL="https://www.dmxapi.cn/v1")
    assert "volcano-video-reference" not in specs
    monkeypatch.delenv("VOLCANO_API_KEY")
    assert "volcano-video-reference" not in {i.payload.slug for i in bootstrap.legacy_deployment_specs()}


def test_mini_price_table_entry():
    assert pricing.PRICE_TABLE[("volcano", MINI_MODEL, "*")] == {"output_per_million_tokens": Decimal("23")}
    # 实测 4s/480p 40,594 Token -> 0.933662 元
    cost = estimate_video_cost("volcano", MINI_MODEL, "480p", output_seconds=4)
    assert cost == (Decimal(23) * estimate_video_tokens("480p", 4) / Decimal(1_000_000)).quantize(
        Decimal("0.000001")
    )


def _mini_and_fast_providers(monkeypatch):
    specs = _bootstrap_specs(monkeypatch)
    providers = {}
    for slug, payload in specs.items():
        if slug in {"volcano-video", "volcano-video-reference"}:
            providers[slug] = ArkVideoProvider(
                api_key="k",
                base_url="https://ark.test/api/v3",
                model_name=payload.model,
                deployment_name=slug,
                capabilities=dict(payload.capabilities),
            )
    return specs, providers


def test_reference_deployment_validates_against_ark_schemas(monkeypatch):
    from app.model_runtime.deployment_config import ArkVideoCapabilities, ArkVideoConfig

    payload = _bootstrap_specs(monkeypatch)["volcano-video-reference"]
    ArkVideoCapabilities.model_validate(payload.capabilities)
    ArkVideoConfig.model_validate(payload.config)


def test_routing_between_fast_and_mini(monkeypatch):
    from app.model_runtime import video as video_module
    from app.model_runtime.catalog import CatalogDeployment, CatalogSnapshot
    from app.services import model_route_service

    specs, providers = _mini_and_fast_providers(monkeypatch)
    items = {}
    for slug, provider in providers.items():
        payload = specs[slug]
        items[slug] = CatalogDeployment(
            id=slug,
            slug=slug,
            name=payload.name,
            driver="ark_video",
            modality="video",
            model=payload.model,
            base_url="https://ark.test/api/v3",
            enabled=True,
            priority=payload.priority,
            capabilities=MappingProxyType(dict(payload.capabilities)),
            pricing=MappingProxyType(dict(payload.pricing)),
            revision=1,
            config_hash="h",
            instance=provider,
        )
    snapshot = CatalogSnapshot(MappingProxyType(items), "rev", datetime.now(timezone.utc))
    monkeypatch.setattr(video_module, "get_catalog", lambda: SimpleNamespace(snapshot=snapshot))
    route = SimpleNamespace(
        purpose="shot_video",
        tier="final",
        modality="video",
        primary_deployment="volcano-video",
        fallback_deployments=["volcano-video-reference"],
        enabled=True,
        revision=1,
    )

    async def _effective(session, purpose, tier="final"):
        return route

    monkeypatch.setattr(model_route_service, "get_effective_route", _effective)

    def pick(**kwargs):
        return _run(video_module.resolve_video_plan(None, **kwargs))["deployment_name"]

    assert pick(duration=5, mode="text") == "volcano-video"
    assert pick(duration=5, mode="first_frame") == "volcano-video"
    assert pick(duration=5, mode="reference", reference_count=2) == "volcano-video-reference"
    assert pick(duration=5, mode="first_last_frame") == "volcano-video-reference"
    # 参考图 + 首帧混用：两个部署都拒绝，路由阶段报错，不会走到 provider
    with pytest.raises(video_module.VideoRoutingError):
        pick(duration=5, mode="first_frame", reference_count=2)


def test_ark_supports_rejects_reference_mixed_with_frames(monkeypatch):
    _, providers = _mini_and_fast_providers(monkeypatch)
    mini = providers["volcano-video-reference"]
    ref = VideoGenerationRequest(mode="reference", prompt="x", reference_images=["a", "b"], duration=5)
    mixed = VideoGenerationRequest(
        mode="reference", prompt="x", reference_images=["a"], first_frame="f", duration=5
    )
    assert mini.supports(ref, model=MINI_MODEL)
    assert not mini.supports(mixed, model=MINI_MODEL)


class _FakeResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def all(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class _RouteSession:
    def __init__(self, route, slugs=()):
        self.route = route
        self.slugs = list(slugs)
        self.added = []

    async def execute(self, statement):
        if "model_routes" in str(statement):
            return _FakeResult(self.route)
        # 已存在的 Deployment 视为管理员改过（revision 2），不触发旧行刷新
        return _FakeResult(
            [SimpleNamespace(slug=slug, revision=2, enabled=True, model="custom") for slug in self.slugs]
        )

    async def get(self, _model, _key):
        return None

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        pass


def _route(fallbacks):
    return SimpleNamespace(
        purpose="shot_video",
        tier="final",
        primary_deployment="volcano-video",
        fallback_deployments=list(fallbacks),
        revision=3,
    )


def test_new_video_deployment_is_appended_to_existing_route(monkeypatch):
    from app.services import model_deployment_bootstrap as bootstrap

    _bootstrap_specs(monkeypatch)
    route = _route(["minimax-video"])
    session = _RouteSession(route, slugs=["volcano-video", "volcano-chat", "volcano-image"])
    inserted = _run(bootstrap.bootstrap_legacy_deployments(session))
    assert inserted == 1
    # 只追加，不删除/重排；revision 递增
    assert route.fallback_deployments == ["minimax-video", "volcano-video-reference"]
    assert route.revision == 4


def test_existing_route_untouched_when_present_or_no_new_video(monkeypatch):
    from app.services import model_deployment_bootstrap as bootstrap

    _bootstrap_specs(monkeypatch)
    existing = ["volcano-video", "volcano-chat", "volcano-image", "volcano-video-reference"]
    route = _route(["volcano-video-reference"])
    assert _run(bootstrap.bootstrap_legacy_deployments(_RouteSession(route, slugs=existing))) == 0
    assert route.fallback_deployments == ["volcano-video-reference"]
    assert route.revision == 3
    # 部署缺失但 slug 已在 fallback 中：不重复追加
    route = _route(["volcano-video-reference"])
    session = _RouteSession(route, slugs=["volcano-video", "volcano-chat", "volcano-image"])
    _run(bootstrap.bootstrap_legacy_deployments(session))
    assert route.fallback_deployments == ["volcano-video-reference"]
    assert route.revision == 3
    # 没有现成 Route（新装）：不创建，交给 bootstrap_default_routes
    session = _RouteSession(None, slugs=["volcano-video", "volcano-chat", "volcano-image"])
    assert _run(bootstrap.bootstrap_legacy_deployments(session)) == 1


async def test_execute_tts_records_actual_cost_by_characters(monkeypatch):
    from types import SimpleNamespace

    from app.providers.base import ProviderResult
    from app.worker import generation_worker as gw

    class _Session:
        async def get(self, model, key):
            return None

        async def flush(self):
            pass

    class _Provider:
        name = "qwen_tts"

        async def synthesize(self, req):
            return ProviderResult(
                success=True,
                provider="qwen_tts",
                model="qwen3-tts-flash",
                external_request_id="req-1",
                artifacts=[{"storage_key": "audios/a.wav", "type": "audio", "duration_ms": 2000}],
                usage={"output_audio_seconds": 2.0, "characters": 2500},
            )

    async def no_abort(session, job):
        return False

    async def noop(*args, **kwargs):
        return None

    async def fake_create_artifact(session, **kw):
        return SimpleNamespace(id="art1", **kw)

    captured = {}

    async def fake_finalize(session, job, **kw):
        captured.update(kw)

    monkeypatch.setattr(gw, "_should_abort_before_submit", no_abort)
    monkeypatch.setattr(gw._media_runtime, "resolve_speech", lambda: (_Provider(), False))
    monkeypatch.setattr(gw, "_ensure_media_invocation", noop)
    monkeypatch.setattr(gw.asset_service, "create_artifact", fake_create_artifact)
    monkeypatch.setattr(gw, "finalize_job", fake_finalize)

    job = SimpleNamespace(
        id="job1",
        project_id="p1",
        provider=None,
        params={"text": "你好", "shot_id": "s1", "track_type": "dialogue"},
    )
    await gw.execute_tts(_Session(), job)

    usage = captured["usage_fields"]
    assert usage["actual_cost"] == Decimal("0.200000")  # 2500 字符 × 0.8 / 10000
    assert usage["event_metadata"]["characters"] == 2500
