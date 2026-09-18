"""providers 抽象层 / 路由 / 价格引擎 / mock provider / job_service 纯逻辑测试。

不连真 DB、不发真实 HTTP：httpx 用 monkeypatch 替身，job_service 用假 session。
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.providers.base import ProviderResult, VideoGenerationRequest
from app.providers.minimax_video import MiniMaxVideoProvider
from app.providers.mock_video import MockVideoProvider
from app.providers.pricing import (
    PRICE_TABLE_VERSION,
    estimate_video_cost,
    resolve_video_routing,
    snapshot_price,
)
from app.providers.video_router import NoProviderError, VideoProviderRouter
from app.providers.volcano_video import VolcanoVideoProvider


def _req(**overrides) -> VideoGenerationRequest:
    base = {"mode": "text", "prompt": "一只猫在花园里"}
    base.update(overrides)
    return VideoGenerationRequest(**base)


# ---------- pricing ----------


def test_pricing_h3_768p_output():
    # H3 768P 输出 0.50/秒 × 6 秒 = 3.00
    cost = estimate_video_cost("minimax", "MiniMax-H3", "768P", output_seconds=6)
    assert cost == Decimal("3.000000")


def test_pricing_h3_input_images_overage():
    # 前 5 张输入图免费，6 张超 1 张 ×0.20 → 3.00 + 0.20
    cost = estimate_video_cost(
        "minimax", "MiniMax-H3", "768P", output_seconds=6, input_image_count=6
    )
    assert cost == Decimal("3.200000")


def test_pricing_h3_regeneration():
    # 再生成输出 0.30/秒
    cost = estimate_video_cost(
        "minimax", "MiniMax-H3", "768P", output_seconds=0, regeneration_output_seconds=6
    )
    assert cost == Decimal("1.800000")


def test_pricing_per_piece_and_per_second():
    assert estimate_video_cost(
        "minimax", "MiniMax-Hailuo-2.3", "768P", output_seconds=10
    ) == Decimal("4.000000")
    assert estimate_video_cost(
        "minimax", "MiniMax-Hailuo-2.3-Fast", "768P", output_seconds=6
    ) == Decimal("1.350000")
    # 按条计费模型不支持其它时长
    assert estimate_video_cost("minimax", "MiniMax-Hailuo-2.3", "768P", output_seconds=8) is None


def test_pricing_unknown_returns_none():
    assert estimate_video_cost("minimax", "no-such-model", "768P", output_seconds=6) is None


def test_resolve_video_routing_uses_effective_volcano_model(monkeypatch):
    from app.tools import volcano_video_generation as vvg

    monkeypatch.delenv("MINIMAX_H3_EXPERIMENTAL", raising=False)
    monkeypatch.setattr(vvg, "ACTIVE_VIDEO_MODEL", "doubao-seedance-1-5-pro-responses")
    plan = resolve_video_routing("balanced", "768P", duration=5)
    assert plan["provider"] == "volcano"
    assert plan["model"] == "doubao-seedance-1-5-pro-responses"
    assert plan["estimated_total_cost"] == Decimal("2.500000")


@pytest.mark.parametrize("policy", ["balanced", "quality", "cheapest"])
def test_resolve_video_routing_rejects_unexecutable_long_duration(monkeypatch, policy):
    monkeypatch.setenv("MINIMAX_H3_EXPERIMENTAL", "true")
    with pytest.raises(ValueError, match="无可执行 Provider"):
        resolve_video_routing(policy, "768P", duration=16)
    assert estimate_video_cost("volcano", "doubao-seedance-1-5-pro", "720p", output_seconds=6) == Decimal("3.000000")


def test_snapshot_price():
    snap = snapshot_price("minimax", "MiniMax-H3", "768P")
    assert snap["price_table_version"] == PRICE_TABLE_VERSION
    assert snap["unit_prices"]["output_per_second"] == "0.50"
    assert snapshot_price("x", "y", "z")["unit_prices"] == {}


# ---------- ProviderResult ----------


def test_provider_result_defaults_and_roundtrip():
    r = ProviderResult(success=True)
    assert r.provider == "" and r.artifacts == [] and r.usage == {} and not r.retryable
    r2 = ProviderResult(**r.model_dump())
    assert r2 == r


# ---------- Router ----------


def _router(minimax_key="", volcano_key=None):
    if volcano_key is not None:
        import app.tools.volcano_video_generation as vvg

        vvg.VOLCANO_API_KEY = volcano_key
    return VideoProviderRouter(
        providers=[
            MiniMaxVideoProvider(api_key=minimax_key),
            VolcanoVideoProvider(),
            MockVideoProvider(),
        ]
    )


def test_router_explicit():
    d = _router(minimax_key="k").select(_req(provider_policy="explicit", explicit_provider="minimax"))
    assert d.provider.name == "minimax"
    # 默认（H3 实验关闭）：显式 minimax 走已核验的 Hailuo V1 标准模型
    assert d.model == "MiniMax-Hailuo-2.3"
    assert not d.mocked
    assert d.price_snapshot["price_table_version"] == PRICE_TABLE_VERSION


def test_router_explicit_unavailable_raises():
    with pytest.raises(ValueError):
        _router(minimax_key="").select(
            _req(provider_policy="explicit", explicit_provider="minimax")
        )
    with pytest.raises(ValueError):
        _router(minimax_key="k").select(
            _req(provider_policy="explicit", explicit_provider="no-such")
        )


def test_router_balanced_prefers_volcano_by_default(monkeypatch):
    """路由守卫（复审 0.9）：balanced 默认 volcano 优先（minimax H3 未核验，默认禁用）。"""
    monkeypatch.setattr("app.tools.volcano_video_generation.VOLCANO_API_KEY", "vk")
    d = _router(minimax_key="k").select(_req(provider_policy="balanced", duration=6))
    assert d.provider.name == "volcano" and not d.mocked
    assert "volcano 优先" in d.raw_reason


def test_router_balanced_minimax_v1_when_volcano_down(monkeypatch):
    """volcano 不可用时 balanced 回退 minimax（仅已核验的 Hailuo V1 系列模型）。"""
    monkeypatch.setattr("app.tools.volcano_video_generation.VOLCANO_API_KEY", "")
    d = _router(minimax_key="k").select(_req(provider_policy="balanced", duration=6))
    assert d.provider.name == "minimax" and d.model == "MiniMax-Hailuo-2.3"
    assert "Hailuo V1" in d.raw_reason


def test_router_balanced_h3_experimental_prefers_minimax(monkeypatch):
    """MINIMAX_H3_EXPERIMENTAL=true 时恢复 minimax(H3) balanced 优先。"""
    monkeypatch.setenv("MINIMAX_H3_EXPERIMENTAL", "true")
    monkeypatch.setattr("app.tools.volcano_video_generation.VOLCANO_API_KEY", "vk")
    d = _router(minimax_key="k").select(_req(provider_policy="balanced", duration=6))
    assert d.provider.name == "minimax" and d.model == "MiniMax-H3"
    assert "H3" in d.raw_reason


def test_router_preview_first_frame_picks_fast():
    d = _router(minimax_key="k").select(
        _req(mode="first_frame", first_frame="/storage/images/a.jpg", quality="preview")
    )
    assert d.provider.name == "minimax"
    assert d.model == "MiniMax-Hailuo-2.3-Fast"
    assert d.resolution == "768P"


def test_router_fallback_to_mock_when_no_keys(monkeypatch):
    import app.tools.volcano_video_generation as vvg

    monkeypatch.setattr(vvg, "VOLCANO_API_KEY", "")
    monkeypatch.setenv("ALLOW_MOCK_PROVIDERS", "true")
    d = _router(minimax_key="").select(_req())
    assert d.provider.name == "mock"
    assert d.mocked is True
    assert d.price_snapshot == {}


def test_router_raises_no_provider_when_mock_disabled(monkeypatch):
    # 默认（ALLOW_MOCK_PROVIDERS=false）无真实 provider 时抛 NoProviderError，不静默 mock
    import app.tools.volcano_video_generation as vvg

    monkeypatch.setattr(vvg, "VOLCANO_API_KEY", "")
    monkeypatch.setenv("ALLOW_MOCK_PROVIDERS", "false")
    with pytest.raises(NoProviderError) as exc_info:
        _router(minimax_key="").select(_req())
    assert exc_info.value.error_code == "no_provider"


def test_explicit_mock_provider_respects_switch_and_audit_flag(monkeypatch):
    router = VideoProviderRouter(providers=[MockVideoProvider()])
    request = _req(provider_policy="explicit", explicit_provider="mock")

    monkeypatch.setenv("ALLOW_MOCK_PROVIDERS", "false")
    with pytest.raises(NoProviderError):
        router.select(request)

    monkeypatch.setenv("ALLOW_MOCK_PROVIDERS", "true")
    decision = router.select(request)
    assert decision.provider.name == "mock"
    assert decision.mocked is True
    assert decision.price_snapshot == {}


def test_locked_video_provider_falls_back_to_mock_only_when_enabled(monkeypatch):
    from app.tools import volcano_video_generation as vvg
    from app.worker import generation_worker

    monkeypatch.setattr(vvg, "VOLCANO_API_KEY", "")
    monkeypatch.setenv("ALLOW_MOCK_PROVIDERS", "true")
    provider, mocked = generation_worker._resolve_locked_video_provider("volcano")
    assert provider is not None and provider.name == "mock" and mocked is True

    monkeypatch.setenv("ALLOW_MOCK_PROVIDERS", "false")
    provider, mocked = generation_worker._resolve_locked_video_provider("volcano")
    assert provider is None and mocked is False


def test_router_reference_mode_filters_volcano(monkeypatch):
    # reference 仅 H3 支持；minimax 无 key 时火山被 supports 过滤 → mock 兜底
    monkeypatch.setattr("app.tools.volcano_video_generation.VOLCANO_API_KEY", "k")
    d = _router(minimax_key="").select(
        _req(mode="reference", reference_images=["/storage/images/a.jpg"])
    )
    assert d.provider.name == "mock" and d.mocked


def test_router_first_last_frame_goes_volcano_when_minimax_down(monkeypatch):
    # first_last_frame 火山支持（start_end），minimax 无 key → 选火山
    monkeypatch.setattr("app.tools.volcano_video_generation.VOLCANO_API_KEY", "k")
    d = _router(minimax_key="").select(
        _req(mode="first_last_frame", first_frame="a", last_frame="b", duration=6)
    )
    assert d.provider.name == "volcano" and not d.mocked


# ---------- MiniMaxVideoProvider（monkeypatch httpx） ----------


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", content=b""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.content = content

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("err", request=None, response=None)


def _patch_httpx(monkeypatch, handler):
    """handler(client, method, url, **kwargs) -> _FakeResponse"""

    class FakeClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            return handler("POST", url, **kw)

        async def get(self, url, **kw):
            return handler("GET", url, **kw)

    monkeypatch.setattr("app.providers.minimax_video.httpx.AsyncClient", FakeClient)


async def test_minimax_submit_success(monkeypatch):
    """官方 V1 主路径：POST /v1/video_generation，body = model/prompt/duration/resolution。"""

    def handler(method, url, **kw):
        assert url == "https://api.minimaxi.com/v1/video_generation"
        body = kw["json"]
        assert body["model"] == "MiniMax-Hailuo-2.3"
        assert body["prompt"] == "一只猫在花园里"
        assert body["duration"] == 6
        assert body["resolution"] == "768P"
        assert "content" not in body and "first_frame_image" not in body
        return _FakeResponse(200, {"task_id": "task_123", "base_resp": {"status_code": 0}})

    _patch_httpx(monkeypatch, handler)
    p = MiniMaxVideoProvider(api_key="k")
    r = await p.submit(_req(duration=6))
    assert r.success and r.external_request_id == "task_123"
    assert r.usage["output_video_seconds"] == 6.0
    assert r.raw_metadata["protocol"] == "v1"


async def test_minimax_submit_v1_first_frame(monkeypatch):
    """官方 V1 图生视频：first_frame_image 为 data URL。"""

    def handler(method, url, **kw):
        body = kw["json"]
        assert url == "https://api.minimaxi.com/v1/video_generation"
        assert body["first_frame_image"] == "data:image/png;base64,AA=="
        return _FakeResponse(200, {"task_id": "t_i2v"})

    _patch_httpx(monkeypatch, handler)
    p = MiniMaxVideoProvider(api_key="k")
    r = await p.submit(
        _req(mode="first_frame", first_frame="data:image/png;base64,AA==", duration=6)
    )
    assert r.success and r.external_request_id == "t_i2v"


async def test_minimax_locked_hailuo_02_preserves_512p(monkeypatch):
    """锁定 Hailuo-02 时请求体和 metadata 必须保持 512P，不得重新 pick Fast。"""

    def handler(method, url, **kw):
        body = kw["json"]
        assert url == "https://api.minimaxi.com/v1/video_generation"
        assert body["model"] == "MiniMax-Hailuo-02"
        assert body["resolution"] == "512P"
        assert body["first_frame_image"] == "data:image/png;base64,AA=="
        return _FakeResponse(200, {"task_id": "t_512"})

    _patch_httpx(monkeypatch, handler)
    provider = MiniMaxVideoProvider(api_key="k")
    request = _req(
        mode="first_frame",
        first_frame="data:image/png;base64,AA==",
        duration=6,
        resolution="512P",
        quality="preview",
    )
    result = await provider.submit(request, model="MiniMax-Hailuo-02")

    assert result.success
    assert result.model == "MiniMax-Hailuo-02"
    assert result.raw_metadata["resolution"] == "512P"


def test_minimax_512p_only_allows_hailuo_02_first_frame():
    provider = MiniMaxVideoProvider(api_key="k")
    first_frame = _req(
        mode="first_frame",
        first_frame="data:image/png;base64,AA==",
        duration=6,
        resolution="512P",
    )
    text_only = _req(duration=6, resolution="512P")

    assert provider.supports(first_frame, model="MiniMax-Hailuo-02")
    assert not provider.supports(text_only, model="MiniMax-Hailuo-02")
    assert not provider.supports(first_frame, model="MiniMax-Hailuo-2.3")


async def test_minimax_submit_h3_content_structure(monkeypatch):
    """H3 实验分支（MINIMAX_H3_EXPERIMENTAL=true）：content[] 多模态结构不变。"""
    monkeypatch.setenv("MINIMAX_H3_EXPERIMENTAL", "true")
    captured = {}

    def handler(method, url, **kw):
        captured.update(kw["json"])
        assert url.endswith("/v2/video_generation")
        return _FakeResponse(200, {"task_id": "t"})

    _patch_httpx(monkeypatch, handler)
    p = MiniMaxVideoProvider(api_key="k")
    req = _req(
        mode="first_last_frame",
        first_frame="data:image/png;base64,AA==",
        last_frame="data:image/png;base64,BB==",
        reference_videos=["https://cdn/x.mp4"],
        reference_audios=["https://cdn/a.wav"],
        duration=8,
    )
    r = await p.submit(req)
    assert r.success
    content = captured["content"]
    assert content[0]["type"] == "text"
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,AA=="},
        "role": "first_frame",
    }
    assert content[2]["role"] == "last_frame"
    assert content[3] == {
        "type": "video_url",
        "video_url": {"url": "https://cdn/x.mp4"},
        "role": "reference_video",
    }
    assert content[4]["type"] == "audio_url" and content[4]["role"] == "reference_audio"
    # 带首帧时 ratio 省略（宽高比由图片决定）
    assert "ratio" not in captured


async def test_minimax_submit_regenerate_uses_v2_regeneration(monkeypatch):
    """H3 实验分支：regenerate 仍走 /v2/video_regeneration。"""
    monkeypatch.setenv("MINIMAX_H3_EXPERIMENTAL", "true")
    captured = {}

    def handler(method, url, **kw):
        captured["url"] = url
        captured.update(kw["json"])
        return _FakeResponse(200, {"task_id": "t9"})

    _patch_httpx(monkeypatch, handler)
    p = MiniMaxVideoProvider(api_key="k")
    r = await p.submit(_req(mode="regenerate", source_video="https://cdn/base.mp4", duration=6))
    assert r.success
    assert captured["url"] == "https://api.minimaxi.com/v2/video_regeneration"
    # 原 content 全量 + 仅一个 base_video 项，固定 2K
    assert captured["resolution"] == "2K"
    base_items = [c for c in captured["content"] if c.get("role") == "base_video"]
    assert len(base_items) == 1
    assert base_items[0]["video_url"] == {"url": "https://cdn/base.mp4"}


async def test_minimax_submit_fast_uses_official_v1(monkeypatch):
    """Fast（preview+first_frame）走官方 V1 协议（公开 i2v 文档模型枚举含 Fast）。"""
    captured = {}

    def handler(method, url, **kw):
        captured["url"] = url
        captured.update(kw["json"])
        return _FakeResponse(200, {"task_id": "tf"})

    _patch_httpx(monkeypatch, handler)
    p = MiniMaxVideoProvider(api_key="k")
    r = await p.submit(
        _req(mode="first_frame", first_frame="data:image/png;base64,AA==", quality="preview")
    )
    assert r.success
    assert captured["url"] == "https://api.minimaxi.com/v1/video_generation"
    assert captured["model"] == "MiniMax-Hailuo-2.3-Fast"
    assert captured["prompt"] == "一只猫在花园里"
    assert captured["first_frame_image"] == "data:image/png;base64,AA=="
    assert "content" not in captured


async def test_minimax_submit_usage_dimensions():
    p = MiniMaxVideoProvider(api_key="k")
    req = _req(
        mode="reference",
        first_frame="/storage/images/a.jpg",
        reference_images=["/storage/images/b.jpg", "/storage/images/c.jpg"],
    )
    usage = p._usage_for(req)
    assert usage["input_image_count"] == 3
    regen = p._usage_for(_req(mode="regenerate", source_video="/storage/videos/x.mp4", duration=10))
    assert regen["regeneration_output_seconds"] == 10.0
    assert regen["output_video_seconds"] == 0.0


async def test_minimax_submit_rate_limited_retryable(monkeypatch):
    _patch_httpx(monkeypatch, lambda m, u, **kw: _FakeResponse(429, text="too many"))
    p = MiniMaxVideoProvider(api_key="k")
    r = await p.submit(_req())
    assert not r.success and r.retryable and r.error_code == "rate_limited"


async def test_minimax_submit_400_not_retryable(monkeypatch):
    _patch_httpx(monkeypatch, lambda m, u, **kw: _FakeResponse(400, text="bad prompt"))
    p = MiniMaxVideoProvider(api_key="k")
    r = await p.submit(_req())
    assert not r.success and not r.retryable and r.error_code == "invalid_request"


async def test_minimax_submit_content_rejected(monkeypatch):
    _patch_httpx(monkeypatch, lambda m, u, **kw: _FakeResponse(400, text="内容审核拒绝 audit fail"))
    p = MiniMaxVideoProvider(api_key="k")
    r = await p.submit(_req())
    assert not r.success and not r.retryable and r.error_code == "content_rejected"


async def test_minimax_submit_biz_error_mapping(monkeypatch):
    """官方公开业务错误码（base_resp.status_code）映射。"""
    cases = [
        (1002, "rate_limited", True),   # 限流可重试
        (1026, "content_rejected", False),  # 审核拒绝不可重试
        (1008, "insufficient_balance", False),
        (1004, "auth_failed", False),
        (2013, "invalid_request", False),
        (1013, "server_error", True),
    ]
    for status_code, expected_code, expected_retryable in cases:
        _patch_httpx(
            monkeypatch,
            lambda m, u, _sc=status_code, **kw: _FakeResponse(
                200, {"base_resp": {"status_code": _sc, "status_msg": "err"}}
            ),
        )
        p = MiniMaxVideoProvider(api_key="k")
        r = await p.submit(_req())
        assert not r.success, f"status_code={status_code}"
        assert r.error_code == expected_code, f"status_code={status_code}"
        assert r.retryable is expected_retryable, f"status_code={status_code}"


async def test_minimax_get_status_shapes(monkeypatch):
    """官方 V1 查询：GET /v1/query/video_generation?task_id=，顶层平铺响应。"""
    states = iter(
        [
            {"task_id": "t", "status": "Processing", "base_resp": {"status_code": 0}},
            {
                "task_id": "t", "status": "Success", "file_id": "f123",
                "video_width": 1920, "video_height": 1080,
                "base_resp": {"status_code": 0},
            },
            {"task_id": "t", "status": "Fail", "base_resp": {"status_code": 0}},
        ]
    )

    def handler(method, url, **kw):
        assert url == "https://api.minimaxi.com/v1/query/video_generation"
        assert kw["params"] == {"task_id": "t"}
        return _FakeResponse(200, next(states))

    _patch_httpx(monkeypatch, handler)
    p = MiniMaxVideoProvider(api_key="k")

    r = await p.get_status("t")
    assert not r.success and r.error_code == "pending" and r.retryable
    r = await p.get_status("t")
    assert r.success and r.raw_metadata["file_id"] == "f123"
    assert r.raw_metadata["video_width"] == 1920
    r = await p.get_status("t")
    assert not r.success and r.error_code == "task_failed" and not r.retryable


async def test_minimax_get_status_h3_experimental(monkeypatch):
    """H3 实验分支：/v2/query/video_generation/{task_id}，成功直返 content.url。"""
    monkeypatch.setenv("MINIMAX_H3_EXPERIMENTAL", "true")

    def handler(method, url, **kw):
        assert url == "https://api.minimaxi.com/v2/query/video_generation/t"
        return _FakeResponse(
            200, {"task": {"status": "succeeded", "content": {"url": "http://cdn/x.mp4"}}}
        )

    _patch_httpx(monkeypatch, handler)
    p = MiniMaxVideoProvider(api_key="k")
    r = await p.get_status("t", protocol="h3")
    assert r.success and r.raw_metadata["download_url"] == "http://cdn/x.mp4"


async def test_minimax_fetch_result(monkeypatch, tmp_path):
    """官方 V1 下载链：query 拿 file_id → files/retrieve 拿 download_url → 下载落盘。"""
    monkeypatch.setattr("app.providers.minimax_video.VIDEOS_DIR", tmp_path)
    calls = []

    def handler(method, url, **kw):
        calls.append(url)
        if "query/video_generation" in url:
            assert kw["params"] == {"task_id": "task_9"}
            return _FakeResponse(
                200,
                {"task_id": "task_9", "status": "Success", "file_id": "f9",
                 "base_resp": {"status_code": 0}},
            )
        if "files/retrieve" in url:
            assert kw["params"] == {"file_id": "f9"}
            return _FakeResponse(
                200,
                {"file": {"file_id": 9, "download_url": "http://cdn/x.mp4"},
                 "base_resp": {"status_code": 0}},
            )
        return _FakeResponse(200, content=b"\x00\x01fake-mp4")

    _patch_httpx(monkeypatch, handler)
    p = MiniMaxVideoProvider(api_key="k")
    r = await p.fetch_result("task_9")
    assert r.success
    assert r.artifacts[0]["storage_key"] == "videos/minimax_task_9.mp4"
    assert (tmp_path / "minimax_task_9.mp4").read_bytes() == b"\x00\x01fake-mp4"
    assert any("files/retrieve" in u for u in calls)


async def test_minimax_fetch_result_regeneration_new_file(monkeypatch, tmp_path):
    """H3 实验分支：再生成产物用独立文件名，禁止覆盖原产物。"""
    monkeypatch.setenv("MINIMAX_H3_EXPERIMENTAL", "true")
    monkeypatch.setattr("app.providers.minimax_video.VIDEOS_DIR", tmp_path)

    def handler(method, url, **kw):
        if "query/video_generation" in url:
            return _FakeResponse(
                200,
                {
                    "task": {
                        "status": "succeeded",
                        "task_type": "regeneration",
                        "content": {"url": "http://cdn/y.mp4"},
                    }
                },
            )
        return _FakeResponse(200, content=b"regen")

    _patch_httpx(monkeypatch, handler)
    p = MiniMaxVideoProvider(api_key="k")
    r = await p.fetch_result("task_r1", protocol="h3")
    assert r.success
    assert r.artifacts[0]["storage_key"] == "videos/minimax_regen_task_r1.mp4"


def test_minimax_supports_and_availability_default_h3_disabled(monkeypatch):
    """默认（H3 实验关闭）：仅 V1 能力——text/first_frame + 6/10s；H3 请求不支持。"""
    monkeypatch.delenv("MINIMAX_H3_EXPERIMENTAL", raising=False)
    assert MiniMaxVideoProvider(api_key="").is_available() is False
    p = MiniMaxVideoProvider(api_key="k")
    assert p.supports(_req(mode="text", duration=6))
    assert p.supports(_req(mode="first_frame", first_frame="a", duration=10))
    # H3 才有的能力默认不可用
    assert not p.supports(_req(mode="first_last_frame", first_frame="a", last_frame="b", duration=10))
    assert not p.supports(_req(mode="regenerate", source_video="v", duration=6))
    assert not p.supports(_req(mode="reference", reference_images=["i"], duration=15))
    assert not p.supports(_req(mode="text", duration=16))
    assert not p.supports(_req(mode="text", duration=8))  # V1 仅 6/10 秒
    # pick_model 不选 H3
    assert p.pick_model(_req(mode="text", duration=6)) == "MiniMax-Hailuo-2.3"
    # Fast（preview+first_frame）仅 text/first_frame 且 6/10 秒
    fast_req = _req(mode="first_frame", first_frame="a", quality="preview", duration=6)
    assert p.supports(fast_req)
    assert p.pick_model(fast_req) == "MiniMax-Hailuo-2.3-Fast"
    assert not p.supports(_req(mode="first_frame", first_frame="a", quality="preview", duration=8))


def test_minimax_supports_h3_experimental(monkeypatch):
    """MINIMAX_H3_EXPERIMENTAL=true：恢复 H3 全 mode 4~15s 能力矩阵。"""
    monkeypatch.setenv("MINIMAX_H3_EXPERIMENTAL", "true")
    p = MiniMaxVideoProvider(api_key="k")
    assert p.pick_model(_req(mode="text", duration=6)) == "MiniMax-H3"
    assert p.supports(_req(mode="text", duration=6))
    assert p.supports(_req(mode="first_last_frame", first_frame="a", last_frame="b", duration=10))
    assert p.supports(_req(mode="regenerate", source_video="v", duration=6))
    assert p.supports(_req(mode="reference", reference_images=["i"], duration=15))
    assert not p.supports(_req(mode="text", duration=16))
    assert not p.supports(_req(mode="text", duration=3))
    assert not p.supports(_req(mode="text", duration=6), model="unknown-model")


async def test_volcano_submit_passes_locked_model(monkeypatch):
    from app.tools import volcano_video_generation as vvg

    captured = {}

    def fake_submit(**kwargs):
        captured.update(kwargs)
        return "task-1", False

    monkeypatch.setattr(vvg, "submit_video_generation_task", fake_submit)
    result = await VolcanoVideoProvider().submit(
        _req(duration=5), model="doubao-seedance-1-5-pro-responses"
    )
    assert result.success
    assert captured["model"] == "doubao-seedance-1-5-pro-responses"
    assert result.model == captured["model"]


# ---------- MockVideoProvider 全流程 ----------


async def test_mock_provider_full_flow(monkeypatch, tmp_path):
    monkeypatch.setenv("MOCK_PROVIDER_DELAY_POLLS", "1")
    monkeypatch.setattr("app.providers.mock_video.VIDEOS_DIR", tmp_path)

    provider = MockVideoProvider()
    req = _req(duration=6)

    sub = await provider.submit(req)
    assert sub.success and sub.external_request_id.startswith("mock_")
    task_id = sub.external_request_id

    s1 = await provider.get_status(task_id)
    assert not s1.success and s1.error_code == "pending" and s1.retryable

    s2 = await provider.get_status(task_id)
    assert s2.success
    assert s2.usage["output_video_seconds"] == 6.0
    assert s2.artifacts[0]["storage_key"] == f"videos/{task_id}.mp4"
    # ffmpeg 可用时真实落盘；失败也容忍（raw_metadata.generated=False）
    if s2.raw_metadata.get("generated"):
        assert (tmp_path / f"{task_id}.mp4").exists()

    f = await provider.fetch_result(task_id)
    assert f.success and f.artifacts[0]["storage_key"] == f"videos/{task_id}.mp4"


async def test_mock_provider_unknown_task():
    r = await MockVideoProvider().get_status("mock_nonexistent")
    assert not r.success and r.error_code == "not_found" and not r.retryable


# ---------- job_service 纯逻辑（假 session，不连 DB） ----------


class _FakeSession:
    async def flush(self):
        pass


def _make_job(attempt=1, max_attempts=3):
    from app.db.models import GenerationJob

    return GenerationJob(
        operation="generate_video",
        project_id="p1",
        idempotency_key="k1",
        attempt=attempt,
        max_attempts=max_attempts,
        status="running",
    )


async def test_mark_failed_retryable_backoff():
    from app.services import job_service

    job = _make_job(attempt=1)
    before = datetime.now(timezone.utc)
    await job_service.mark_failed(
        _FakeSession(), job, error_code="rate_limited", error="x", retryable=True
    )
    assert job.status == "pending"
    # 指数退避 2**attempt 分钟（attempt=1 → 2 分钟），lease_until 复用为 run_after
    assert job.lease_until >= before + timedelta(minutes=2)
    assert job.lease_until <= datetime.now(timezone.utc) + timedelta(minutes=2, seconds=5)


async def test_mark_failed_terminal_when_attempts_exhausted():
    from app.services import job_service

    job = _make_job(attempt=3, max_attempts=3)
    await job_service.mark_failed(
        _FakeSession(), job, error_code="server_error", error="x", retryable=True
    )
    assert job.status == "failed" and job.finished_at is not None


async def test_mark_failed_non_retryable_is_terminal():
    from app.services import job_service

    job = _make_job(attempt=1)
    await job_service.mark_failed(
        _FakeSession(), job, error_code="invalid_request", error="x", retryable=False
    )
    assert job.status == "failed"


async def test_requeue_for_poll_does_not_burn_attempt():
    from app.services import job_service

    job = _make_job(attempt=2)
    job.status = "running"
    await job_service.requeue_for_poll(_FakeSession(), job, delay_seconds=10)
    assert job.status == "pending"
    assert job.attempt == 1  # 轮询重入队把 lease 时加的 attempt 减回
    assert job.lease_until > datetime.now(timezone.utc) + timedelta(seconds=5)


# ---------- TTS provider（P1-12） ----------


def test_tts_estimate_duration():
    from app.providers.tts_base import estimate_duration_ms

    assert estimate_duration_ms("") == 1000  # 下限 1s
    assert estimate_duration_ms("一" * 8) == 2000  # 4 字/秒


async def test_mock_tts_provider_synthesize(monkeypatch, tmp_path):
    from app.providers.tts_base import AUDIOS_DIR, MockTTSProvider, TTSRequest

    monkeypatch.setattr("app.providers.tts_base.AUDIOS_DIR", tmp_path)
    r = await MockTTSProvider().synthesize(TTSRequest(text="你好，世界"))
    assert r.success and r.provider == "mock_tts"
    assert r.artifacts[0]["type"] == "audio"
    assert r.artifacts[0]["storage_key"].startswith("audios/")
    assert r.raw_metadata["mocked"] is True
    assert AUDIOS_DIR.name == "audios"  # 模块常量未被破坏


def test_qwen_tts_availability():
    from app.providers.tts_base import QwenTTSProvider

    assert QwenTTSProvider(api_key="").is_available() is False
    assert QwenTTSProvider(api_key="k").is_available() is True


async def test_mock_image_provider_generate(monkeypatch, tmp_path):
    from app.providers.image_base import (
        ImageGenerationRequest,
        MockImageProvider,
        available_image_providers,
    )

    monkeypatch.setattr("app.providers.image_base.IMAGES_DIR", tmp_path)
    r = await MockImageProvider().generate(ImageGenerationRequest(prompt="角色图"))
    assert r.success
    assert r.artifacts[0]["storage_key"].startswith("images/")
    assert r.raw_metadata["mocked"] is True
    # 无 VOLCANO_API_KEY 时真实 provider 列表为空（worker 据此走 mock/no_provider 分支）
    monkeypatch.setattr("app.tools.volcano_image_generation.VOLCANO_API_KEY", "")
    assert available_image_providers() == []


# ---------- VolcanoImageProvider（P1-1，monkeypatch 工具函数，不发真实 HTTP） ----------


class _FakeTool:
    """langchain @tool 的最小替身（provider 侧 getattr(tool, "func", None) 取函数）。"""

    def __init__(self, func):
        self.func = func


def _volcano_result_json(**over):
    import json

    base = {
        "image_url": "/storage/images/volcano_x.jpg",
        "original_url": "https://cdn/x.jpg",
        "local_path": "/storage/images/volcano_x.jpg",
        "prompt": "角色图",
        "provider": "volcano",
    }
    base.update(over)
    return json.dumps(base, ensure_ascii=False)


def test_volcano_image_availability(monkeypatch):
    from app.providers.volcano_image import VolcanoImageProvider

    monkeypatch.setattr("app.tools.volcano_image_generation.VOLCANO_API_KEY", "")
    assert VolcanoImageProvider().is_available() is False
    monkeypatch.setattr("app.tools.volcano_image_generation.VOLCANO_API_KEY", "k")
    assert VolcanoImageProvider().is_available() is True


def test_available_image_providers_registers_volcano(monkeypatch):
    from app.providers.image_base import available_image_providers

    monkeypatch.setattr("app.tools.volcano_image_generation.VOLCANO_API_KEY", "k")
    providers = available_image_providers()
    assert [p.name for p in providers] == ["volcano_image"]  # 选择顺序：volcano 优先


async def test_volcano_image_generate_text2image(monkeypatch):
    import app.tools.volcano_image_generation as vig
    from app.providers.image_base import ImageGenerationRequest
    from app.providers.volcano_image import VolcanoImageProvider

    monkeypatch.setattr(vig, "VOLCANO_API_KEY", "k")
    calls = {}

    def fake_generate(prompt, size="1:1"):
        calls.update({"prompt": prompt, "size": size})
        return _volcano_result_json()

    monkeypatch.setattr(vig, "generate_volcano_image_tool", _FakeTool(fake_generate))
    r = await VolcanoImageProvider().generate(
        ImageGenerationRequest(prompt="角色图", aspect_ratio="9:16")
    )
    assert r.success and r.provider == "volcano_image"
    assert r.artifacts[0]["storage_key"] == "images/volcano_x.jpg" and r.artifacts[0]["type"] == "image"
    assert r.usage["output_image_count"] == 1
    assert calls == {"prompt": "角色图", "size": "9:16"}


async def test_volcano_image_generate_edit_with_source(monkeypatch):
    """image+prompt→image（首帧/角色参考图编辑）：走 edit 工具。"""
    import app.tools.volcano_image_generation as vig
    from app.providers.image_base import ImageGenerationRequest
    from app.providers.volcano_image import VolcanoImageProvider

    monkeypatch.setattr(vig, "VOLCANO_API_KEY", "k")
    calls = {}

    def fake_edit(prompt, image_url, size="1:1"):
        calls.update({"prompt": prompt, "image_url": image_url, "size": size})
        return _volcano_result_json(source_image=image_url)

    monkeypatch.setattr(vig, "edit_volcano_image_tool", _FakeTool(fake_edit))
    r = await VolcanoImageProvider().generate(
        ImageGenerationRequest(
            prompt="换成夜景", source_image="/storage/images/ref.png", aspect_ratio="9:16"
        )
    )
    assert r.success and r.raw_metadata["edited"] is True
    assert calls["image_url"] == "/storage/images/ref.png"


async def test_volcano_image_generate_error(monkeypatch):
    import app.tools.volcano_image_generation as vig
    from app.providers.image_base import ImageGenerationRequest
    from app.providers.volcano_image import VolcanoImageProvider

    monkeypatch.setattr(vig, "VOLCANO_API_KEY", "k")
    monkeypatch.setattr(
        vig, "generate_volcano_image_tool",
        _FakeTool(lambda prompt, size="1:1": "Error generating image: boom"),
    )
    r = await VolcanoImageProvider().generate(ImageGenerationRequest(prompt="x"))
    assert not r.success and r.error_code == "generate_failed" and r.retryable


def test_image_pricing():
    from app.providers.pricing import estimate_image_cost

    # 火山图片占位单价 0.20/张（待官方刊例核实）
    assert estimate_image_cost("volcano_image", "seedream-4.5", count=2) == Decimal("0.400000")
    assert estimate_image_cost("mock_image", "mock-image") is None  # 无条目不按 0 计费
