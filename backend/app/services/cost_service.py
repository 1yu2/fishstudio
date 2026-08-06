"""费用报价与累计预算（review 0.6 / P1-4 / P1-5）。

- estimate_run_cost：不创建任何 Job，按 run 未完成的 creates_jobs steps × 当前 revision
  的 shots/tracks × pricing 单价快照，输出逐项明细。
- create_cost_quote：估算后把 quote 快照写入 run.input_snapshot.cost_quote，
  用户确认的是一个不可变 quote（quote_id + input_hash + expires_at +
  price_table_version）。
- validate_cost_quote：确认/推进前校验 quote——存在、quote_id 匹配、未过期、
  价格表版本一致、且**重新计算的 input_hash 与快照一致**（报价后分镜/参数变化即失效）。
- reserve_budget：原子预算预留（SELECT ... FOR UPDATE 行锁内检查并累加）。

并发说明（已承认）：reserve_budget 依赖 workflow_runs 行锁——单进程事务内行锁
已防并发穿透；多 uvicorn worker 共享同一 PG，FOR UPDATE 跨进程同样有效。
"""

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import StoryboardRevision, WorkflowRun, WorkflowStep
from app.providers.pricing import (
    PRICE_TABLE_VERSION,
    estimate_image_cost,
    estimate_video_cost,
    resolve_video_routing,
)
from app.services import job_planner

logger = logging.getLogger(__name__)

# TTS 单价占位（未入 pricing 价格表，待供应商刊例确认后接管）
TTS_PRICE_PER_SECOND = Decimal("0.05")
VIDEO_FALLBACK_PRICE_PER_SECOND = Decimal("0.50")  # H3 768P 兜底 ¥0.50/秒
_TTS_CHARS_PER_SECOND = 5
QUOTE_TTL_MINUTES = 30


def _resolve_image_provider_model() -> tuple[str, str]:
    """解析当前可用的图片 Provider 和 Model（报价/预算一致性）。

    实际运行环境只会有一个图片 provider；多 provider 时按最贵报价（见 review 0.5）。
    """
    from app.providers.image_base import available_image_providers
    from app.providers.volcano_image import VolcanoImageProvider

    providers = available_image_providers()
    if providers:
        p = providers[0]
        model = "seedream-4.5"
        if isinstance(p, VolcanoImageProvider):
            from app.tools import volcano_image_generation as vig
            model = vig.VOLCANO_IMAGE_MODEL
        return p.name, model
    return "volcano_image", "seedream-4.5"


def _image_unit_price() -> Decimal:
    """图片单价（CNY/张），按当前可用 provider 解析。无价格条目返回 0（调用方兜底）。"""
    prov, model = _resolve_image_provider_model()
    price = estimate_image_cost(prov, model, count=1)
    return price if price is not None else Decimal("0")


class ShotRoutePlan(BaseModel):
    """R8.3：单个 Shot 的不可变路由计划（写入 CostQuoteItem，Planner 消费）。"""
    shot_id: str
    provider: str
    model: str
    resolution: str
    protocol: str = ""
    requested_duration: int
    billable_duration: int
    estimated_total_cost: str  # Decimal 字符串
    price_snapshot: dict = Field(default_factory=dict)
    price_table_version: str = ""


class CostQuoteItem(BaseModel):
    """报价明细项（R8.3：含 shot 级 route_plans 供 Planner 消费）。"""
    step_node_key: str
    operation: str
    provider: str = ""
    model: str = ""
    resolution: str = ""
    candidate_count: int = 1
    unit_count: float
    unit_price: str  # Decimal 序列化为字符串，避免浮点误差
    subtotal: str
    route_plans: list[ShotRoutePlan] = Field(default_factory=list)


class CostQuote(BaseModel):
    quote_id: str
    run_id: str
    items: list[CostQuoteItem]
    total: str
    price_table_version: str
    created_at: str
    # 复审 0.6：quote 与估算输入绑定——报价后分镜/参数变化 → input_hash 不一致 → 失效
    input_hash: str = ""
    # 估算时各 episode 的当前 storyboard revision id（{episode_id: revision_id|None}）
    storyboard_revision_id: dict[str, Optional[str]] = Field(default_factory=dict)
    expires_at: str = ""  # ISO8601，创建后 QUOTE_TTL_MINUTES 分钟


def check_budget(estimated_new: Decimal, run: WorkflowRun) -> bool:
    """累计预算判定（纯函数）：run.estimated_cost（已计划累计）+ 本次 > cost_limit → 拒绝。

    无 cost_limit 视为不限。仅用于纯函数场景/测试；写路径请用 reserve_budget。
    """
    if run.cost_limit is None:
        return True
    planned = run.estimated_cost or Decimal("0")
    return planned + estimated_new <= run.cost_limit


async def reserve_budget(session: AsyncSession, run_id: str, amount: Decimal) -> bool:
    """原子预算预留（P1-5）：SELECT ... FOR UPDATE 行锁内检查并累加 estimated_cost。

    cost_limit 为空 = 不限。通过则 estimated_cost += amount 并返回 True，否则 False
    （不累加）。并发语义见模块 docstring。
    """
    stmt = select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update()
    run = (await session.execute(stmt)).scalars().first()
    if run is None:
        raise ValueError(f"run {run_id} not found")
    planned = run.estimated_cost or Decimal("0")
    if run.cost_limit is not None and planned + amount > run.cost_limit:
        return False
    run.estimated_cost = planned + amount
    await session.flush()
    return True


def _video_unit_price(policy: dict[str, Any]) -> tuple[Decimal, str, str, str]:
    """按 run default_policy 解析视频路由和单价（R6.3：使用统一 VideoRoutePlan）。"""
    resolution = policy.get("resolution", "768P")
    provider_policy = policy.get("provider_policy", "balanced")
    routing = resolve_video_routing(
        provider_policy, resolution, duration=6,
        explicit_provider=policy.get("explicit_provider", ""),
    )
    price = routing["estimated_total_cost"]
    per_second = (price / routing["billable_duration"]) if (price and routing["billable_duration"]) else VIDEO_FALLBACK_PRICE_PER_SECOND
    return (
        per_second,
        routing["provider"],
        routing["model"],
        routing["resolution"],
    )


def _estimate_tts_seconds(text: str) -> int:
    return max(1, round(len(text.strip()) / _TTS_CHARS_PER_SECOND))


async def _run_pending_job_steps(session: AsyncSession, run: WorkflowRun) -> list[WorkflowStep]:
    """该 run 未完成的 creates_jobs steps（succeeded/skipped/canceled 不再计价）。"""
    stmt = select(WorkflowStep).where(
        WorkflowStep.run_id == run.id,
        WorkflowStep.status.notin_(["succeeded", "skipped", "canceled"]),
    )
    steps = list((await session.execute(stmt)).scalars().all())
    return [s for s in steps if s.node_type in _STEP_OPERATION]


async def _run_all_job_steps(session: AsyncSession, run: WorkflowRun) -> list[WorkflowStep]:
    """该 run 全部 creates_jobs steps（含已完成）。

    input_hash 必须基于全量 steps 计算：若只取未完成 steps，任一收费 step
    完成后 hash 即变化，费用 Gate 复核（validate_cost_quote）会把仍有效的
    quote 判为失效，后续收费节点永久卡 waiting_approval（E2E 实测复现）。
    """
    stmt = select(WorkflowStep).where(WorkflowStep.run_id == run.id)
    steps = list((await session.execute(stmt)).scalars().all())
    return [s for s in steps if s.node_type in _STEP_OPERATION]


_STEP_OPERATION = {
    "shot.video.generate": "generate_video",
    "shot.image.generate": "generate_image",
    "shot.audio.generate": "tts",
    "episode.render": "render_episode",
}


async def _current_revision_id(session: AsyncSession, episode_id: str) -> Optional[str]:
    stmt = (
        select(StoryboardRevision.id)
        .where(StoryboardRevision.episode_id == episode_id)
        .order_by(StoryboardRevision.revision.desc())
        .limit(1)
    )
    return await session.scalar(stmt)


def _shot_tts_seconds(shot: Any) -> int:
    seconds = 0
    for tracks in (shot.dialogue_tracks or [], shot.narration_tracks or []):
        for track in tracks or []:
            text = (track or {}).get("text") or ""
            if text.strip():
                seconds += _estimate_tts_seconds(text)
    return seconds


async def _estimate_step_item(
    session: AsyncSession, run: WorkflowRun, step: WorkflowStep, policy: dict[str, Any]
) -> Optional[CostQuoteItem]:
    operation = _STEP_OPERATION[step.node_type]
    if step.node_type == "episode.render":
        return CostQuoteItem(
            step_node_key=step.node_key, operation=operation,
            unit_count=1, unit_price="0", subtotal="0",
        )

    shots = await job_planner._current_revision_shots(session, step.entity_id)
    if step.node_type == "shot.video.generate":
        # R8.3：逐 Shot 路由，保存完整 Route Plans
        total = Decimal("0")
        plans: list[ShotRoutePlan] = []
        for s in shots:
            dur = int(max(1, s.duration_ms) / 1000)
            plan = resolve_video_routing(
                policy.get("provider_policy", "balanced"),
                policy.get("resolution", "768P"),
                duration=dur,
                explicit_provider=policy.get("explicit_provider", ""),
            )
            cost = plan["estimated_total_cost"]
            total += cost if cost is not None else VIDEO_FALLBACK_PRICE_PER_SECOND * dur
            plans.append(ShotRoutePlan(
                shot_id=s.id,
                provider=plan["provider"],
                model=plan["model"],
                resolution=plan["resolution"],
                protocol=plan.get("protocol", ""),
                requested_duration=dur,
                billable_duration=plan["billable_duration"],
                estimated_total_cost=str(cost) if cost is not None else "0",
                price_snapshot=plan["price_snapshot"],
                price_table_version=plan["price_table_version"],
            ))
        first = plans[0] if plans else None
        return CostQuoteItem(
            step_node_key=step.node_key, operation=operation,
            provider=first.provider if first else "", model=first.model if first else "",
            resolution=first.resolution if first else "",
            unit_count=len(shots), unit_price=str(total / len(shots)) if shots else "0",
            subtotal=str(total),
            route_plans=plans,
        )
    if step.node_type == "shot.image.generate":
        candidate_count = int(policy.get("candidate_count", 1))
        count = len(shots) * candidate_count
        unit_price = _image_unit_price()
        prov, model = _resolve_image_provider_model()
        subtotal = unit_price * count
        return CostQuoteItem(
            step_node_key=step.node_key, operation=operation,
            provider=prov, model=model,
            candidate_count=candidate_count,
            unit_count=count, unit_price=str(unit_price),
            subtotal=str(subtotal),
        )
    if step.node_type == "shot.audio.generate":
        seconds = sum(_shot_tts_seconds(s) for s in shots)
        subtotal = (TTS_PRICE_PER_SECOND * seconds).quantize(Decimal("0.000001"))
        return CostQuoteItem(
            step_node_key=step.node_key, operation=operation,
            unit_count=seconds, unit_price=str(TTS_PRICE_PER_SECOND),
            subtotal=str(subtotal),
        )
    return None


async def _compute_input_payload(
    session: AsyncSession, run: WorkflowRun, steps: list[WorkflowStep]
) -> tuple[dict[str, Any], dict[str, Optional[str]]]:
    """quote 绑定的估算输入快照：当前 storyboard revision id、shot 数/时长明细、
    TTS 秒数、provider_policy、candidate_count、模型/分辨率（policy 内）、价格表版本。

    canonical JSON 的 sha256 即 input_hash；报价后任何一项变化 → hash 变化 → quote 失效。
    """
    policy = (run.input_snapshot or {}).get("default_policy") or {}
    # 解析实际视频路由信息（与报价 _video_unit_price 一致，用于 input_hash）
    _vp, _vprov, _vmodel, _vres = _video_unit_price(policy)
    payload_steps: list[dict[str, Any]] = []
    revision_ids: dict[str, Optional[str]] = {}
    for step in steps:
        revision_id: Optional[str] = None
        shots_payload: list[dict[str, Any]] = []
        if step.node_type != "episode.render":
            revision_id = await _current_revision_id(session, step.entity_id)
            revision_ids[step.entity_id] = revision_id
            shots = await job_planner._current_revision_shots(session, step.entity_id)
            for s in shots:
                shots_payload.append(
                    {
                        "id": s.id,
                        "duration_ms": s.duration_ms,
                        "tts_seconds": _shot_tts_seconds(s),
                    }
                )
        payload_steps.append(
            {
                "node_key": step.node_key,
                "node_type": step.node_type,
                "storyboard_revision_id": revision_id,
                "shots": shots_payload,
            }
        )
    payload = {
        "steps": payload_steps,
        "policy": {
            "resolution": policy.get("resolution", "768P"),
            "provider_policy": policy.get("provider_policy", "balanced"),
            "candidate_count": int(policy.get("candidate_count", 1)),
            "explicit_provider": policy.get("explicit_provider", ""),
        },
        "price_table_version": PRICE_TABLE_VERSION,
        # R8.3：路由版本包含 H3 开关和 Provider 模型配置，H3 变化时 hash 改变
        "routing_config": _routing_config_fingerprint(),
    }
    return payload, revision_ids


def _routing_config_fingerprint() -> dict:
    """R9.3：路由配置指纹含 H3、Volcano DMXAPI、模型名和路由版本。"""
    import os

    from app.providers.minimax_video import MiniMaxVideoProvider, h3_experimental_enabled
    from app.providers.pricing import PRICE_TABLE_VERSION, VIDEO_ROUTING_VERSION
    from app.tools import volcano_video_generation as vvg

    minimax = MiniMaxVideoProvider()
    return {
        "routing_version": VIDEO_ROUTING_VERSION,
        "h3_experimental": os.getenv("MINIMAX_H3_EXPERIMENTAL", "false").strip().lower(),
        "h3_experimental_effective": h3_experimental_enabled(),
        "volcano_use_dmxapi_env": os.getenv("VOLCANO_USE_DMXAPI", "false").strip().lower(),
        "volcano_use_dmxapi_effective": vvg.VOLCANO_USE_DMXAPI,
        "volcano_base_url": vvg.VOLCANO_BASE_URL,
        "volcano_active_base_url": vvg.ACTIVE_BASE_URL,
        "volcano_direct_model": vvg.VOLCANO_VIDEO_MODEL,
        "volcano_dmxapi_model": vvg.DMXAPI_VIDEO_MODEL,
        "volcano_active_model": vvg.ACTIVE_VIDEO_MODEL,
        "minimax_base_url": minimax.base_url,
        "minimax_h3_model": minimax.h3_model,
        "minimax_v1_model": minimax.v1_model,
        "minimax_fast_model": minimax.fast_model,
        "price_table_version": PRICE_TABLE_VERSION,
    }


def _hash_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def compute_input_hash(session: AsyncSession, run: WorkflowRun) -> str:
    """重新计算当前估算输入的 input_hash（validate_cost_quote 用）。

    基于全量 creates_jobs steps（见 _run_all_job_steps），保证收费 step 完成
    不使 hash 变化；报价后分镜/时长/策略变化仍会使 hash 变化而失效。
    """
    steps = await _run_all_job_steps(session, run)
    payload, _ = await _compute_input_payload(session, run, steps)
    return _hash_payload(payload)


async def estimate_run_cost(session: AsyncSession, run: WorkflowRun) -> CostQuote:
    """不创建任何 Job，遍历 run 未完成 creates_jobs steps 输出逐项费用明细。"""
    policy = (run.input_snapshot or {}).get("default_policy") or {}
    steps = await _run_pending_job_steps(session, run)
    items: list[CostQuoteItem] = []
    total = Decimal("0")
    for step in steps:
        item = await _estimate_step_item(session, run, step, policy)
        if item is None:
            continue
        items.append(item)
        total += Decimal(item.subtotal)
    # 明细只计未完成 steps；input_hash 与 validate 一致基于全量 steps，
    # 保证 quote 在部分 step 完成后仍可通过复核（仅分镜/参数变化才失效）。
    payload, revision_ids = await _compute_input_payload(
        session, run, await _run_all_job_steps(session, run)
    )
    now = datetime.now(timezone.utc)
    return CostQuote(
        quote_id=str(uuid.uuid4()),
        run_id=run.id,
        items=items,
        total=str(total.quantize(Decimal("0.000001"))),
        price_table_version=PRICE_TABLE_VERSION,
        created_at=now.isoformat(),
        input_hash=_hash_payload(payload),
        storyboard_revision_id=revision_ids,
        expires_at=(now + timedelta(minutes=QUOTE_TTL_MINUTES)).isoformat(),
    )


async def create_cost_quote(session: AsyncSession, run_id: str) -> CostQuote:
    """估算并把 quote 快照写入 run.input_snapshot.cost_quote（每次调用刷新 quote_id）。"""
    run = await session.get(WorkflowRun, run_id)
    if run is None:
        raise ValueError(f"run {run_id} not found")
    quote = await estimate_run_cost(session, run)
    snapshot = dict(run.input_snapshot or {})
    snapshot["cost_quote"] = quote.model_dump()
    run.input_snapshot = snapshot
    await session.commit()
    return quote


async def validate_cost_quote(
    session: AsyncSession, run: WorkflowRun, quote_id: str
) -> tuple[bool, str]:
    """确认费用/展开 job 前校验 quote（复审 0.6）：

    - quote 存在；
    - 调用方给的 quote_id 与当前快照一致（确认的必须是不可变报价）；
    - 未过期（expires_at，30 分钟）；
    - price_table_version 与当前价格表一致；
    - 重新计算当前 input_hash 与快照一致（报价后分镜/参数变化 → 失效）。

    返回 (ok, 具体原因)。
    """
    quote = (run.input_snapshot or {}).get("cost_quote")
    if not quote:
        return False, "尚无费用报价，请先生成 cost-quote"
    if not quote_id:
        return False, "缺少 quote_id：确认费用必须绑定具体报价（先获取 cost-quote）"
    if quote.get("quote_id") != quote_id:
        return False, (
            f"quote_id 不匹配（当前最新报价 {quote.get('quote_id')}），"
            "报价已被刷新，请使用最新 quote_id 确认"
        )
    expires_at = quote.get("expires_at")
    if not expires_at:
        return False, "报价缺少有效期（旧格式报价），请重新生成 cost-quote"
    try:
        expires = datetime.fromisoformat(expires_at)
    except (TypeError, ValueError):
        return False, "报价有效期格式非法，请重新生成 cost-quote"
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires:
        return False, f"报价已过期（{QUOTE_TTL_MINUTES} 分钟有效），请重新生成 cost-quote"
    if quote.get("price_table_version") != PRICE_TABLE_VERSION:
        return False, (
            f"报价已过期（报价价格表 {quote.get('price_table_version')} ≠ 当前 "
            f"{PRICE_TABLE_VERSION}），请重新生成 cost-quote"
        )
    current_hash = await compute_input_hash(session, run)
    if quote.get("input_hash") != current_hash:
        return False, (
            "报价后分镜/时长/策略参数已变化，报价失效，请重新生成 cost-quote"
        )
    return True, ""


def estimate_job_retry_cost(job: Any) -> Decimal:
    """job 级重试的粗估费用（简单可靠方案）：

    - generate_video：按 job.provider/model/resolution/duration_ms 走价格表重估
      （无价格条目用 H3 768P 兜底价；params.price_snapshot 只作审计留痕，不直接当金额）；
    - generate_image：IMAGE_PRICE_PER_PIECE × candidate_count；
    - tts：文本估算秒数 × TTS_PRICE_PER_SECOND；
    - render_episode 等本地/未知操作：0（不预留）。
    """
    params = job.params or {}
    if job.operation == "generate_video":
        duration_s = max(1, int(params.get("duration_ms", 0) or 0) // 1000)
        cost = estimate_video_cost(
            job.provider or "minimax",
            job.model or "MiniMax-H3",
            params.get("resolution", "768P"),
            output_seconds=duration_s,
        )
        return cost if cost is not None else VIDEO_FALLBACK_PRICE_PER_SECOND * duration_s
    if job.operation == "generate_image":
        return estimate_image_cost(
            job.provider or "volcano_image",
            job.model or "seedream-4.5",
            count=int(params.get("candidate_count", 1)),
        ) or Decimal("0")
    if job.operation == "tts":
        return TTS_PRICE_PER_SECOND * _estimate_tts_seconds(params.get("text") or "")
    return Decimal("0")
