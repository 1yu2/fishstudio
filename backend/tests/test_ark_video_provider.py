"""火山方舟 Seedance Adapter：请求构造、状态映射、产物下载与计价。"""

import asyncio
import base64
import json
from decimal import Decimal

import httpx
import pytest

from app.model_runtime.deployment_config import DeploymentInput
from app.model_runtime.registry import DEFAULT_DRIVER_REGISTRY
from app.providers import ark_video
from app.providers.ark_video import ArkVideoProvider
from app.providers.base import VideoGenerationRequest
from app.providers.pricing import estimate_video_cost_from_entry, estimate_video_tokens, pricing_entry

BASE = "https://ark.test/api/v3"
VIDEO_URL = "https://cdn.ark.test/out.mp4"
FRAME_URL = "https://cdn.ark.test/last.png"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(ark_video, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(ark_video, "VIDEOS_DIR", tmp_path / "videos")
    monkeypatch.setattr(ark_video, "IMAGES_DIR", tmp_path / "images")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "first.png").write_bytes(b"png-bytes")
    return tmp_path


def _client(monkeypatch, handler):
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr("app.providers.ark_video.httpx.AsyncClient", factory)


def _provider(**overrides):
    config = overrides.pop("config", {})
    capabilities = overrides.pop("capabilities", {})
    return ArkVideoProvider(
        api_key=overrides.pop("api_key", "ark-key"),
        base_url=BASE,
        model_name="doubao-seedance-1-5-pro-251215",
        deployment_name="seedance",
        config=config,
        capabilities=capabilities,
        **overrides,
    )


def _req(**values):
    values.setdefault("prompt", "女主在雨夜回头")
    values.setdefault("duration", 5)
    return VideoGenerationRequest(**values)


def _run(coro):
    return asyncio.run(coro)


def test_payload_inlines_storage_image_and_sets_defaults(storage):
    provider = _provider()
    payload = provider.build_payload(
        _req(mode="first_frame", first_frame="images/first.png"), provider.model_name
    )

    assert payload["model"] == "doubao-seedance-1-5-pro-251215"
    assert payload["content"][0] == {"type": "text", "text": "女主在雨夜回头"}
    image = payload["content"][1]
    assert image["role"] == "first_frame"
    assert image["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(b"png-bytes").decode()
    assert payload["ratio"] == "9:16"
    assert payload["duration"] == 5
    assert payload["resolution"] == "720p"
    assert payload["return_last_frame"] is True
    assert payload["generate_audio"] is False
    assert payload["watermark"] is False
    assert "callback_url" not in payload


def test_preview_quality_uses_low_resolution_and_inline_style(storage):
    provider = _provider(config={"parameter_style": "inline", "camera_fixed": True})
    payload = provider.build_payload(_req(quality="preview", aspect_ratio="16:9"), provider.model_name)

    assert "resolution" not in payload and "ratio" not in payload
    assert payload["content"][0]["text"].endswith("--rs 480p --rt 16:9 --dur 5 --wm false --cf true")


def test_storage_path_cannot_escape_storage_dir(storage):
    provider = _provider()
    with pytest.raises(ValueError, match="storage"):
        provider.build_payload(_req(mode="first_frame", first_frame="../secret.png"), provider.model_name)


def test_supports_checks_mode_duration_ratio_references_and_model():
    provider = _provider(capabilities={"modes": ["text", "first_frame"], "max_duration": 10})

    assert provider.supports(_req())
    assert provider.supports(_req(mode="first_frame", first_frame="x"))
    assert not provider.supports(_req(mode="first_last_frame", first_frame="x", last_frame="y"))
    assert not provider.supports(_req(duration=12))
    assert not provider.supports(_req(aspect_ratio="2:1"))
    assert not provider.supports(_req(reference_images=["a.png"]))
    assert not provider.supports(_req(), model="other-model")
    assert not _provider(api_key="").supports(_req())


def test_submit_returns_task_id(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "cgt-123"})

    _client(monkeypatch, handler)
    result = _run(_provider().submit(_req(), model="doubao-seedance-1-5-pro-251215"))

    assert result.success and result.external_request_id == "cgt-123"
    assert result.raw_metadata["protocol"] == "ark_v3"
    assert seen["url"] == f"{BASE}/contents/generations/tasks"
    assert seen["auth"] == "Bearer ark-key"
    assert seen["body"]["duration"] == 5


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, "auth_failed", False),
        (429, "rate_limited", True),
        (500, "server_error", True),
        (400, "invalid_request", False),
    ],
)
def test_submit_classifies_http_errors(monkeypatch, status, code, retryable):
    body = {"error": {"code": "InputTextSensitiveContentDetected", "message": "敏感内容"}}
    _client(monkeypatch, lambda request: httpx.Response(status, json=body))

    result = _run(_provider().submit(_req()))

    assert not result.success
    assert (result.error_code, result.retryable) == (code, retryable)
    assert "InputTextSensitiveContentDetected" in result.error_message


def test_submit_rejects_unsupported_request_without_calling_api(monkeypatch):
    def handler(request):  # pragma: no cover - 不应被调用
        raise AssertionError("不应调用方舟")

    _client(monkeypatch, handler)
    result = _run(_provider().submit(_req(duration=30)))

    assert result.error_code == "unsupported_request"


@pytest.mark.parametrize(
    ("task", "success", "code", "retryable"),
    [
        ({"status": "queued"}, False, "pending", True),
        ({"status": "running"}, False, "pending", True),
        ({"status": "succeeded"}, True, "", False),
        (
            {"status": "failed", "error": {"code": "OutputVideoSensitive", "message": "审核未通过"}},
            False,
            "OutputVideoSensitive",
            False,
        ),
        ({"status": "expired"}, False, "task_expired", True),
        ({"status": "cancelled"}, False, "task_cancelled", False),
    ],
)
def test_status_mapping(monkeypatch, task, success, code, retryable):
    _client(monkeypatch, lambda request: httpx.Response(200, json={"id": "cgt-1", **task}))

    result = _run(_provider().get_status("cgt-1"))

    assert result.success is success
    if not success:
        assert (result.error_code, result.retryable) == (code, retryable)


def _succeeded(**content):
    return {
        "id": "cgt-9",
        "model": "doubao-seedance-1-5-pro-251215",
        "status": "succeeded",
        "duration": 5,
        "resolution": "720p",
        "content": {"video_url": VIDEO_URL, "last_frame_url": FRAME_URL, **content},
        "usage": {"completion_tokens": 108000, "total_tokens": 108000},
    }


def test_fetch_result_downloads_video_and_last_frame(monkeypatch, storage):
    def handler(request):
        url = str(request.url)
        if url == VIDEO_URL:
            return httpx.Response(200, content=b"mp4-bytes", headers={"content-type": "video/mp4"})
        if url == FRAME_URL:
            return httpx.Response(200, content=b"frame", headers={"content-type": "image/png"})
        return httpx.Response(200, json=_succeeded())

    _client(monkeypatch, handler)
    result = _run(_provider().fetch_result("cgt-9"))

    assert result.success
    artifact = result.artifacts[0]
    assert (storage / artifact["storage_key"]).read_bytes() == b"mp4-bytes"
    assert (storage / artifact["last_frame_storage_key"]).read_bytes() == b"frame"
    assert artifact["duration_ms"] == 5000
    assert result.usage == {"output_video_seconds": 5.0, "completion_tokens": 108000, "total_tokens": 108000}


def test_last_frame_failure_does_not_fail_video(monkeypatch, storage):
    def handler(request):
        url = str(request.url)
        if url == VIDEO_URL:
            return httpx.Response(200, content=b"mp4-bytes", headers={"content-type": "video/mp4"})
        if url == FRAME_URL:
            return httpx.Response(404)
        return httpx.Response(200, json=_succeeded())

    _client(monkeypatch, handler)
    result = _run(_provider().fetch_result("cgt-9"))

    assert result.success
    assert "last_frame_storage_key" not in result.artifacts[0]


@pytest.mark.parametrize(
    ("video_url", "config"),
    [
        ("http://cdn.ark.test/out.mp4", {}),
        ("https://127.0.0.1/out.mp4", {}),
        ("https://localhost/out.mp4", {}),
        ("https://evil.example/out.mp4", {"artifact_allowed_host_suffixes": ["ark.test"]}),
    ],
)
def test_fetch_result_rejects_unsafe_artifact_urls(monkeypatch, storage, video_url, config):
    _client(monkeypatch, lambda request: httpx.Response(200, json=_succeeded(video_url=video_url)))

    result = _run(_provider(config=config).fetch_result("cgt-9"))

    assert not result.success and result.error_code == "artifact_invalid"


def test_fetch_result_enforces_size_limit(monkeypatch, storage):
    def handler(request):
        if str(request.url) == VIDEO_URL:
            return httpx.Response(200, content=b"x" * 2048, headers={"content-type": "video/mp4"})
        return httpx.Response(200, json=_succeeded(last_frame_url=""))

    _client(monkeypatch, handler)
    result = _run(_provider(config={"max_artifact_bytes": 1024}).fetch_result("cgt-9"))

    assert result.error_code == "artifact_invalid"


def test_fetch_result_without_video_url_is_reported(monkeypatch, storage):
    _client(monkeypatch, lambda request: httpx.Response(200, json=_succeeded(video_url="")))

    result = _run(_provider().fetch_result("cgt-9"))

    assert result.error_code == "artifact_missing"


def test_registry_builds_ark_deployment(monkeypatch):
    monkeypatch.setenv("ARK_TEST_KEY", "from-env")
    payload = DeploymentInput(
        name="Seedance",
        slug="seedance-15",
        driver="ark_video",
        modality="video",
        model="doubao-seedance-1-5-pro-251215",
        base_url=BASE,
        credential_ref="env:ARK_TEST_KEY",
        config={"preview_resolution": "480p", "max_concurrency": 3},
        capabilities={"max_duration": 10},
        pricing={"output_per_million_tokens": "15"},
    )

    provider = DEFAULT_DRIVER_REGISTRY.build(payload)

    assert isinstance(provider, ArkVideoProvider)
    assert provider.api_key == "from-env" and provider.max_concurrency == 3 and provider.max_duration == 10


@pytest.mark.parametrize(
    ("config", "capabilities", "message"),
    [
        ({"api_key": "leak"}, {}, "密钥"),
        ({"unknown": 1}, {}, "extra"),
        ({}, {"modes": ["reference"]}, "max_reference_images"),
        ({}, {"aspect_ratios": ["2:1"]}, "画幅"),
        ({"callback_url": "ftp://x"}, {}, "http"),
    ],
)
def test_ark_config_validation(config, capabilities, message):
    with pytest.raises(ValueError, match=message):
        DeploymentInput(
            name="Seedance",
            slug="seedance-15",
            driver="ark_video",
            modality="video",
            model="m",
            base_url=BASE,
            credential_ref="env:ARK_TEST_KEY",
            config=config,
            capabilities=capabilities,
        )


def test_token_pricing():
    assert estimate_video_tokens("720p", 5) == 108000
    assert estimate_video_tokens("480p", 5) == 48037
    assert estimate_video_tokens("768P", 5) is None

    entry = pricing_entry({"output_per_million_tokens": "15"})
    assert estimate_video_cost_from_entry(entry, output_seconds=5, video_tokens=108000) == Decimal("1.620000")
    assert estimate_video_cost_from_entry(entry, output_seconds=5) is None


def test_route_plan_prices_seedance_by_tokens_per_tier(monkeypatch):
    from datetime import datetime, timezone
    from types import MappingProxyType, SimpleNamespace

    from app.model_runtime import video as video_module
    from app.model_runtime.catalog import CatalogDeployment, CatalogSnapshot
    from app.services import model_route_service

    provider = _provider()
    deployment = CatalogDeployment(
        id="seedance",
        slug="seedance",
        name="Seedance",
        driver="ark_video",
        modality="video",
        model=provider.model_name,
        base_url=BASE,
        enabled=True,
        priority=100,
        capabilities=MappingProxyType({}),
        pricing=MappingProxyType({"output_per_million_tokens": "15"}),
        revision=1,
        config_hash="h",
        instance=provider,
    )
    snapshot = CatalogSnapshot(MappingProxyType({"seedance": deployment}), "rev", datetime.now(timezone.utc))
    monkeypatch.setattr(video_module, "get_catalog", lambda: SimpleNamespace(snapshot=snapshot))
    route = SimpleNamespace(
        purpose="shot_video",
        tier="final",
        modality="video",
        primary_deployment="seedance",
        fallback_deployments=[],
        enabled=True,
        revision=1,
    )

    async def _effective(session, purpose, tier="final"):
        return route

    monkeypatch.setattr(model_route_service, "get_effective_route", _effective)

    final = _run(video_module.resolve_video_plan(None, duration=5, tier="final"))
    preview = _run(video_module.resolve_video_plan(None, duration=5, tier="preview"))

    assert (final["resolution"], final["protocol"]) == ("720p", "ark_v3")
    assert final["estimated_total_cost"] == Decimal("1.620000")
    assert preview["resolution"] == "480p"
    assert preview["estimated_total_cost"] == Decimal("0.720555")
