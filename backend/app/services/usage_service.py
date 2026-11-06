"""UsageEvent 记录与聚合；已收费视频的幂等入账必须与终态同事务。"""

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
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


class VideoAccountingError(RuntimeError):
    """视频费用未能落账；停止自动取回，等待人工处理。"""


async def record_usage(session: AsyncSession, **fields) -> Optional[UsageEvent]:
    """写一条 UsageEvent；视频账目写入失败上抛，其余历史用量仍只记录日志。"""
    if (
        fields.get("operation") == "generate_video"
        and fields.get("provider_request_id")
        and (
            fields.get("status") == "success"
            or any(fields.get(key) is not None for key in ("estimated_cost", "actual_cost"))
        )
    ):
        # 使用现有主键作跨 job、跨事务的幂等键；ON CONFLICT 防止取消与 worker 并发重复记账。
        fields = _auto_fill(fields)
        fields["id"] = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"video-charge:{fields.get('provider', '')}:{fields['provider_request_id']}",
            )
        )
        for key in _MONEY_FIELDS:
            if fields.get(key) is not None:
                fields[key] = Decimal(str(fields[key]))
        statement = insert(UsageEvent).values(**fields)
        incoming = statement.excluded
        # 实际金额优先；成功取回可以完善估价账目，迟到的估价不得降级实际账目。
        improve = or_(
            incoming.actual_cost.is_not(None),
            (UsageEvent.actual_cost.is_(None)) & (incoming.status == "success"),
        )
        updates = {
            key: case((improve, getattr(incoming, key)), else_=getattr(UsageEvent, key))
            for key in (
                "input_units",
                "output_units",
                "input_tokens",
                "output_tokens",
                "gpu_seconds",
                "unit_type",
                "cost_source",
                "event_metadata",
                "error_code",
            )
            if key in fields
        }
        updates["actual_cost"] = func.coalesce(incoming.actual_cost, UsageEvent.actual_cost)
        updates["status"] = case(
            (incoming.status == "success", incoming.status),
            (UsageEvent.status == "success", UsageEvent.status),
            (improve, incoming.status),
            else_=UsageEvent.status,
        )
        try:
            result = await session.execute(
                statement.on_conflict_do_update(index_elements=[UsageEvent.id], set_=updates)
                .returning(UsageEvent)
                .execution_options(populate_existing=True)
            )
            return result.scalar_one_or_none()
        except Exception as error:
            raise VideoAccountingError("视频费用入账失败") from error

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
        func.sum(UsageEvent.input_units).label("input_units"),
        func.sum(UsageEvent.output_units).label("output_units"),
        func.count().filter(UsageEvent.actual_cost.is_(None)).label("unpriced_count"),
    ).group_by(col)
    if user_id is not None:
        stmt = stmt.where(UsageEvent.user_id == user_id)
    if project_id:
        stmt = stmt.where(UsageEvent.project_id == project_id)
    if since:
        stmt = stmt.where(UsageEvent.created_at >= since)

    result = await session.execute(stmt)
    rows = []
    for (
        key,
        count,
        estimated_cost,
        actual_cost,
        input_units,
        output_units,
        unpriced_count,
    ) in result.all():
        rows.append(
            {
                group_by: key.isoformat() if hasattr(key, "isoformat") else key,
                "count": count,
                "estimated_cost": float(estimated_cost) if estimated_cost is not None else None,
                "actual_cost": float(actual_cost) if actual_cost is not None else None,
                "input_units": float(input_units or 0),
                "output_units": float(output_units or 0),
                "unpriced_count": int(unpriced_count or 0),
            }
        )
    return rows
