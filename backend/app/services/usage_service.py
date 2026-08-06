"""UsageEvent 记录与聚合。用量失败不影响主流程（只 log 不抛）。"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UsageEvent
from app.services.execution_context import get_execution_context

logger = logging.getLogger(__name__)

_MONEY_FIELDS = ("estimated_cost", "actual_cost", "unit_price_snapshot")


def _auto_fill(fields: dict) -> dict:
    """从 ExecutionContext 自动补 request_id/run_id/user_id（有的话，且调用方未显式给）。"""
    ctx = get_execution_context()
    if not fields.get("request_id") and ctx.request_id:
        fields["request_id"] = ctx.request_id
    if not fields.get("run_id") and ctx.run_id:
        fields["run_id"] = ctx.run_id
    if fields.get("user_id") is None and ctx.user_id:
        try:
            fields["user_id"] = int(ctx.user_id)
        except (TypeError, ValueError):
            pass
    return fields


async def record_usage(session: AsyncSession, **fields) -> Optional[UsageEvent]:
    """写一条 UsageEvent（金额字段转 Decimal）。任何失败只 log 不抛。"""
    try:
        fields = _auto_fill(fields)
        for key in _MONEY_FIELDS:
            if fields.get(key) is not None and not isinstance(fields[key], Decimal):
                fields[key] = Decimal(str(fields[key]))
        event = UsageEvent(**fields)
        session.add(event)
        await session.flush()
        return event
    except Exception as e:
        logger.warning(f"记录 UsageEvent 失败（已忽略）: {e}")
        return None


async def aggregate_usage(
    session: AsyncSession,
    group_by: str = "provider",
    user_id: Optional[int] = None,
    project_id: Optional[str] = None,
    since: Optional[datetime] = None,
) -> list[dict]:
    """按 provider/model/operation/day 聚合用量与费用。"""
    group_columns = {
        "provider": UsageEvent.provider,
        "model": UsageEvent.model,
        "operation": UsageEvent.operation,
        "day": func.date_trunc("day", UsageEvent.created_at),
    }
    if group_by not in group_columns:
        raise ValueError(f"不支持的 group_by: {group_by}（可选: {list(group_columns)}）")

    col = group_columns[group_by]
    stmt = select(
        col.label("key"),
        func.count().label("count"),
        func.sum(UsageEvent.estimated_cost).label("estimated_cost"),
        func.sum(UsageEvent.actual_cost).label("actual_cost"),
        func.sum(UsageEvent.output_units).label("output_units"),
    ).group_by(col)
    if user_id is not None:
        stmt = stmt.where(UsageEvent.user_id == user_id)
    if project_id:
        stmt = stmt.where(UsageEvent.project_id == project_id)
    if since:
        stmt = stmt.where(UsageEvent.created_at >= since)

    result = await session.execute(stmt)
    rows = []
    for key, count, estimated_cost, actual_cost, output_units in result.all():
        rows.append(
            {
                group_by: key.isoformat() if hasattr(key, "isoformat") else key,
                "count": count,
                "estimated_cost": float(estimated_cost or 0),
                "actual_cost": float(actual_cost or 0),
                "output_units": float(output_units or 0),
            }
        )
    return rows
