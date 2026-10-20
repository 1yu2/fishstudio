"""每日云端花费上限。

只统计云端模型的花费（自托管的本地 H3 等不计入）：
今日已花 = 今天（按 BUDGET_TIMEZONE 的自然日）该用户 usage_events 的实际/预估费用之和；
进行中 = 该用户项目里尚未结束、提交时预估为云端费用的任务之和。
提交新任务前要求 已花 + 进行中 + 本次预估 ≤ DAILY_BUDGET_CNY。
"""

from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GenerationJob, SeriesProject, UsageEvent
from app.services.cost_report_service import SELF_HOSTED_PROVIDERS

DEFAULT_DAILY_BUDGET = "20"
ACTIVE_JOB_STATUSES = ("pending", "leased", "running")
# 写入 job.params，提交时的云端预估费用；本地自托管任务为 0
ESTIMATE_PARAM = "budget_estimate"


class BudgetExceeded(Exception):
    pass


def daily_cap() -> Optional[Decimal]:
    """DAILY_BUDGET_CNY；留空或 0 表示不限。"""
    raw = os.getenv("DAILY_BUDGET_CNY", DEFAULT_DAILY_BUDGET).strip()
    try:
        value = Decimal(raw) if raw else Decimal(0)
    except InvalidOperation:
        value = Decimal(DEFAULT_DAILY_BUDGET)
    return value if value > 0 else None


def day_start(now: Optional[datetime] = None) -> datetime:
    zone = ZoneInfo(os.getenv("BUDGET_TIMEZONE", "Asia/Shanghai"))
    current = (now or datetime.now(zone)).astimezone(zone)
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def is_cloud(provider: str) -> bool:
    return provider not in SELF_HOSTED_PROVIDERS


async def spent_today(session: AsyncSession, user_id: int) -> Decimal:
    cost = func.coalesce(UsageEvent.actual_cost, UsageEvent.estimated_cost)
    total = await session.scalar(
        select(func.coalesce(func.sum(cost), 0)).where(
            UsageEvent.user_id == user_id,
            UsageEvent.created_at >= day_start(),
            UsageEvent.provider.not_in(SELF_HOSTED_PROVIDERS),
        )
    )
    return Decimal(str(total or 0))


async def in_flight(session: AsyncSession, user_id: int) -> Decimal:
    rows = (
        await session.execute(
            select(GenerationJob.params)
            .join(SeriesProject, SeriesProject.id == GenerationJob.project_id)
            .where(
                SeriesProject.user_id == user_id,
                GenerationJob.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
    ).scalars()
    total = Decimal(0)
    for params in rows:
        try:
            total += Decimal(str((params or {}).get(ESTIMATE_PARAM) or 0))
        except InvalidOperation:
            continue
    return total


async def status(session: AsyncSession, user_id: int) -> dict[str, Any]:
    cap = daily_cap()
    spent = await spent_today(session, user_id)
    pending = await in_flight(session, user_id)
    return {
        "cap": float(cap) if cap is not None else None,
        "spent": float(spent),
        "in_flight": float(pending),
        "remaining": float(max(Decimal(0), cap - spent - pending)) if cap is not None else None,
    }


async def reserve(session: AsyncSession, user_id: int, estimate: Decimal) -> None:
    """检查本次云端预估是否超出今日上限；超出抛 BudgetExceeded。

    用户级事务锁，避免同一用户并发提交同时通过检查；锁随事务提交释放。
    调用方需在同一事务里把 estimate 写入新任务的 params[ESTIMATE_PARAM]。
    """
    cap = daily_cap()
    if cap is None or estimate <= 0:
        return
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 7_310_000 + user_id})
    spent = await spent_today(session, user_id)
    pending = await in_flight(session, user_id)
    if spent + pending + estimate > cap:
        raise BudgetExceeded(
            f"超出今日云端花费上限 ¥{cap}：已花 ¥{spent:.2f}，进行中 ¥{pending:.2f}，"
            f"本次预计 ¥{estimate:.2f}。可改用本地预览档，或在 backend/.env 调整 DAILY_BUDGET_CNY"
        )
