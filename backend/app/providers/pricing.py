"""价格引擎（CNY，文档 S7）：按维度分别计价 + 价格快照。

各维度（输出秒/输入视频秒/输入图片张数/输入音频秒/再生成秒）分别累计加总；
无价格条目返回 None（调用方需兜底，不得按 0 计费入账）。
"""

import os
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional

PRICE_TABLE_VERSION = "2026-08-03"
VIDEO_ROUTING_VERSION = "2026-08-05.3"

_ZERO = Decimal("0")

# (provider, model, resolution) -> 计价规则。resolution="*" 为通配兜底。
# 字段（缺省 0/不适用）：
#   output_per_second / input_video_per_second / input_audio_per_second /
#   regeneration_per_second: 按秒单价
#   per_piece: {duration_s: 单条价}（按条计费模型，优先于 output_per_second）
#   image_free_count + image_over_price: 输入图片前 N 张免费，超出每张价
PRICE_TABLE: dict[tuple[str, str, str], dict] = {
    ("minimax", "MiniMax-H3", "768P"): {
        "output_per_second": Decimal("0.50"),
        "input_video_per_second": Decimal("0.50"),
        "image_free_count": 5,
        "image_over_price": Decimal("0.20"),
        "input_audio_per_second": Decimal("0"),  # 输入音频免费
        "regeneration_per_second": Decimal("0.30"),
    },
    ("minimax", "MiniMax-H3", "2K"): {
        "output_per_second": Decimal("0.80"),
        "input_video_per_second": Decimal("0.80"),
        "image_free_count": 5,
        "image_over_price": Decimal("0.20"),
        "input_audio_per_second": Decimal("0"),
        "regeneration_per_second": Decimal("0.30"),
    },
    ("minimax", "MiniMax-Hailuo-2.3-Fast", "768P"): {
        "output_per_second": Decimal("0.225"),
    },
    ("minimax", "MiniMax-Hailuo-2.3", "768P"): {
        "per_piece": {6: Decimal("2.00"), 10: Decimal("4.00")},
    },
    ("minimax", "MiniMax-Hailuo-02", "512P"): {
        "per_piece": {6: Decimal("0.60"), 10: Decimal("1.00")},
    },
    # 占位价（0.50/秒），待火山官方刊例核实后更新
    ("volcano", "doubao-seedance-1-5-pro", "*"): {
        "output_per_second": Decimal("0.50"),
    },
    ("volcano", "doubao-seedance-1-5-pro-responses", "*"): {
        "output_per_second": Decimal("0.50"),
    },
}

# 图片单价表（CNY/张）。(provider, model) -> 单张价。
# TODO(pricing): 火山 Seedream 占位单价（0.20/张），待官方刊例核实后更新；
# mock 图片不计费（不在表中，estimate_image_cost 返回 None，调用方按 0 处理）。
IMAGE_PRICE_TABLE: dict[tuple[str, str], Decimal] = {
    ("volcano_image", "seedream-4.5"): Decimal("0.20"),
}

# 音色创建价格（按次）。(provider) -> 每次价格。
VOICE_ENROLL_PRICE: dict[str, Decimal] = {
    "qwen_tts": Decimal("2.00"),  # 占位，待供应商刊例核实
    "mock_tts": Decimal("0"),
}


def estimate_image_cost(provider: str, model: str, *, count: int = 1) -> Optional[Decimal]:
    """图片成本估算（CNY）。无价格条目返回 None（调用方需兜底，不得按 0 计费入账）。"""
    unit = IMAGE_PRICE_TABLE.get((provider, model))
    if unit is None:
        return None
    return (unit * count).quantize(Decimal("0.000001"))


_TEXT_PRICING_KEYS = {"currency", "version", "unit", "source", "status", "per_piece"}


def pricing_entry(pricing: Mapping[str, Any]) -> Optional[dict]:
    """把 Deployment.pricing（JSON，数字可能是字符串）转成计价条目；空或非法返回 None。

    bootstrap 写入的按条价格是扁平字段 per_piece_<秒数>，这里还原为 per_piece 字典。
    """
    raw = dict(pricing)
    try:
        if not raw.get("output_per_second") and not raw.get("per_piece"):
            pieces = {
                int(key.removeprefix("per_piece_")): Decimal(str(value))
                for key, value in raw.items()
                if key.startswith("per_piece_")
            }
            if pieces:
                raw["per_piece"] = pieces
        converted = {
            key: Decimal(str(value)) if key not in _TEXT_PRICING_KEYS else value
            for key, value in raw.items()
            if not key.startswith("per_piece_")
        }
        if isinstance(converted.get("per_piece"), dict):
            converted["per_piece"] = {
                int(seconds): Decimal(str(price)) for seconds, price in converted["per_piece"].items()
            }
    except (InvalidOperation, ValueError):
        return None
    return converted or None


def _lookup(provider: str, model: str, resolution: str) -> Optional[dict]:
    # 新配置优先：只要 Catalog 中存在对应 Deployment，价格为空就明确表示
    # unavailable，不再误退回静态价格或零成本。
    from app.model_runtime.catalog import get_catalog

    matches = [
        item
        for item in get_catalog().snapshot.list()
        if item.model == model and (item.driver == provider or getattr(item.instance, "name", "") == provider)
    ]
    if matches:
        return pricing_entry(matches[0].pricing)
    if provider == "local_h3":
        raw = os.getenv("LOCAL_H3_COST_CNY_PER_SECOND", "").strip()
        if not raw:
            return None
        return {"output_per_second": Decimal(raw), "cost_source": "hardware_profile"}
    entry = PRICE_TABLE.get((provider, model, resolution))
    if entry is None:
        entry = PRICE_TABLE.get((provider, model, "*"))
    return entry


def estimate_video_cost(
    provider: str,
    model: str,
    resolution: str,
    *,
    output_seconds: float,
    input_video_seconds: float = 0,
    input_image_count: int = 0,
    input_audio_seconds: float = 0,
    regeneration_output_seconds: float = 0,
) -> Optional[Decimal]:
    """按维度分别估算成本（CNY）并加总。无价格条目返回 None。"""
    entry = _lookup(provider, model, resolution)
    if entry is None:
        return None
    return estimate_video_cost_from_entry(
        entry,
        output_seconds=output_seconds,
        input_video_seconds=input_video_seconds,
        input_image_count=input_image_count,
        input_audio_seconds=input_audio_seconds,
        regeneration_output_seconds=regeneration_output_seconds,
    )


# 按 Token 计费的视频模型（如方舟 Seedance）：tokens ≈ 宽 × 高 × 帧率 × 秒数 / 1024。
# 各画幅下像素数接近，按分辨率档位取 16:9 的像素数估算；实际用量以厂商返回的 usage 为准。
_PIXELS_BY_RESOLUTION = {"480p": 854 * 480, "720p": 1280 * 720, "1080p": 1920 * 1080}


def estimate_video_tokens(resolution: str, duration: float, *, fps: int = 24) -> Optional[int]:
    pixels = _PIXELS_BY_RESOLUTION.get((resolution or "").lower())
    if pixels is None:
        return None
    return int(pixels * fps * float(duration) / 1024)


def estimate_video_cost_from_entry(
    entry: Mapping[str, Any],
    *,
    output_seconds: float,
    input_video_seconds: float = 0,
    input_image_count: int = 0,
    input_audio_seconds: float = 0,
    regeneration_output_seconds: float = 0,
    video_tokens: Optional[int] = None,
) -> Optional[Decimal]:
    """给定计价条目估算成本；按条计费模型不支持该时长、或按 Token 计费却无法估算 Token 时返回 None。"""
    total = _ZERO
    per_piece = entry.get("per_piece")
    per_million_tokens = entry.get("output_per_million_tokens")
    if per_million_tokens is not None:
        if video_tokens is None:
            return None
        total += per_million_tokens * Decimal(video_tokens) / Decimal(1_000_000)
    elif per_piece is not None:
        piece = per_piece.get(int(output_seconds))
        if piece is None:
            return None  # 按条计费模型不支持该时长
        total += piece
    else:
        total += entry.get("output_per_second", _ZERO) * Decimal(str(output_seconds))

    total += entry.get("input_video_per_second", _ZERO) * Decimal(str(input_video_seconds))

    free_images = int(entry.get("image_free_count", 0))
    billable_images = max(0, int(input_image_count) - free_images)
    total += entry.get("image_over_price", _ZERO) * billable_images

    total += entry.get("input_audio_per_second", _ZERO) * Decimal(str(input_audio_seconds))
    total += entry.get("regeneration_per_second", _ZERO) * Decimal(str(regeneration_output_seconds))
    return total.quantize(Decimal("0.000001"))


def snapshot_price(provider: str, model: str, resolution: str) -> dict:
    """价格快照（写入 job.params / usage event_metadata，便于审计回溯）。"""
    return snapshot_entry(_lookup(provider, model, resolution))


def snapshot_entry(entry: Optional[Mapping[str, Any]], *, version: str = PRICE_TABLE_VERSION) -> dict:
    unit_prices: dict = {}
    if entry is not None:
        for key, value in entry.items():
            if key == "per_piece":
                unit_prices[key] = {str(k): str(v) for k, v in value.items()}
            elif isinstance(value, Decimal):
                unit_prices[key] = str(value)
            else:
                unit_prices[key] = value
    return {"price_table_version": version, "unit_prices": unit_prices}


def resolve_video_routing(
    provider_policy: str,
    resolution: str,
    *,
    duration: int = 6,
    explicit_provider: str = "",
) -> dict:
    """R7：按完整 Shot 请求计算可执行路由快照（VideoRoutePlan）。

    返回 routing_snapshot 含 provider/model/resolution/protocol/billable_duration/
    estimated_total_cost/price_snapshot/price_table_version。
    explicit 不匹配时直接抛 ValueError；未知 MiniMax 模型和 H3 关闭时 H3 路径均本地拒绝。
    """
    from app.providers.minimax_video import MiniMaxVideoProvider, h3_experimental_enabled
    from app.tools import volcano_video_generation as vvg

    minimax = MiniMaxVideoProvider()
    from app.providers.local_h3 import LocalH3Provider

    local_h3 = LocalH3Provider()
    v1_ok = duration in (6, 10)
    h3_on = h3_experimental_enabled()
    volcano_ok = 4 <= duration <= 12
    h3_ok = 4 <= duration <= 15
    volcano_model = vvg.ACTIVE_VIDEO_MODEL
    h3_model = minimax.h3_model

    if provider_policy == "explicit":
        if not explicit_provider:
            raise ValueError("explicit policy requires explicit_provider")
        prov = explicit_provider.lower()
        if prov in ("local_h3", "local-h3", local_h3.deployment_name.lower()):
            if not local_h3.is_available():
                raise ValueError("Local H3 Deployment 未启用或配置不完整")
            if not local_h3.min_duration <= duration <= local_h3.max_duration:
                raise ValueError(f"Local H3 仅支持 {local_h3.min_duration}-{local_h3.max_duration} 秒")
            return _make_plan(
                "local_h3",
                local_h3.model_name,
                "preview" if resolution.lower() == "preview" else "standard",
                duration,
                "local_h3_v1",
            )
        if prov in ("volcano", "volcano_image"):
            if not volcano_ok:
                raise ValueError(f"Volcano 仅支持 4-12 秒，当前 {duration}s 超限")
            return _make_plan("volcano", volcano_model, resolution, duration, "volcano")
        if prov == "minimax":
            if v1_ok:
                return _make_plan("minimax", "MiniMax-Hailuo-02", "512P", duration, "v1")
            if h3_on and h3_ok:
                return _make_plan("minimax", h3_model, resolution, duration, "h3")
            raise ValueError(f"explicit minimax 不支持 {duration}s（V1 仅 6/10s，H3 仅 4-15s）")
        raise ValueError(f"explicit provider {explicit_provider} 未注册")
    elif provider_policy in ("balanced", "quality"):
        if local_h3.is_available() and local_h3.min_duration <= duration <= local_h3.max_duration:
            return _make_plan(
                "local_h3",
                local_h3.model_name,
                "preview" if resolution.lower() == "preview" else "standard",
                duration,
                "local_h3_v1",
            )
        if volcano_ok:
            return _make_plan("volcano", volcano_model, resolution, duration, "volcano")
        if h3_on and h3_ok:
            return _make_plan("minimax", h3_model, resolution, duration, "h3")
        raise ValueError(f"balanced/quality {duration}s 无可执行 Provider（最大支持 15s）")
    elif provider_policy == "cheapest":
        if v1_ok:
            return _make_plan("minimax", "MiniMax-Hailuo-02", "512P", duration, "v1")
        if h3_on and h3_ok:
            return _make_plan("minimax", h3_model, resolution, duration, "h3")
        if volcano_ok:
            return _make_plan("volcano", volcano_model, resolution, duration, "volcano")
        raise ValueError(f"cheapest {duration}s 无可执行 Provider（最大支持 15s）")
    else:
        raise ValueError(f"未知 provider_policy: {provider_policy}")


_V1_MODELS = {"MiniMax-Hailuo-02", "MiniMax-Hailuo-2.3", "MiniMax-Hailuo-2.3-Fast"}


def video_protocol(provider_name: str, model: str) -> str:
    """Worker 查询/取结果时使用的协议标识；按 Provider 实例名与模型推断。"""
    if provider_name == "local_h3":
        return "local_h3_v1"
    if provider_name == "ark":
        return "ark_v3"
    if model == "MiniMax-H3":
        return "h3"
    if model in _V1_MODELS:
        return "v1"
    return "volcano"


def _make_plan(provider: str, model: str, resolution: str, duration: int, protocol: str = "") -> dict:
    """给定参数计算完整 routing_snapshot。"""
    if not protocol:
        protocol = video_protocol(provider, model)

    price = estimate_video_cost(provider, model, resolution, output_seconds=duration)
    if price is None and model in _V1_MODELS and duration not in (6, 10):
        # V1 per-piece only supports 6/10, undetermined
        price = None
    elif price is None:
        price = estimate_video_cost(provider, model, resolution, output_seconds=1)
        if price is not None:
            price = price * duration
    if price is None:
        raise ValueError(f"缺少视频定价配置: {provider}/{model}/{resolution}")

    snapshot = snapshot_price(provider, model, resolution)
    plan = {
        "provider": provider,
        "model": model,
        "resolution": resolution,
        "protocol": protocol,
        "billable_duration": duration,
        "estimated_total_cost": price,
        "price_snapshot": snapshot,
        "price_table_version": PRICE_TABLE_VERSION,
    }
    try:
        from app.model_runtime.catalog import get_catalog

        catalog = get_catalog().snapshot
        deployment = next(
            (
                item
                for item in catalog.list("video")
                if item.model == model
                and (item.driver == provider or getattr(item.instance, "name", "") == provider)
            ),
            None,
        )
        if deployment is not None:
            plan.update(
                {
                    "deployment_name": deployment.slug,
                    "deployment_revision": deployment.revision,
                    "deployment_config_hash": deployment.config_hash,
                    "catalog_revision": catalog.catalog_revision,
                }
            )
    except Exception:
        # 兼容期 Catalog 尚未加载时继续生成旧格式快照。
        pass
    return plan
