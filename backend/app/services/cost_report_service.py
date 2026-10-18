"""节点级成本报告与调用树。

数据源只有 usage_events（每次模型调用或渲染一条），按项目/集过滤后从多个维度汇总；
调用树把 WorkflowRun → Step → Job → ModelInvocation → UsageEvent 串起来，回答
「这一批生成卡在哪、钱花在哪」。

金额口径：
- estimated：调用前的报价估算；
- actual：按厂商返回用量算出的实际成本（Token / GPU 秒 / 视频秒），算不出时为空；
- effective：有实际用实际，否则用估算，用于汇总「目前花了多少」。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import case, false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GenerationJob, ModelInvocation, UsageEvent, WorkflowRun, WorkflowStep

SELF_HOSTED_PROVIDERS = ("local_h3", "local")
GROUP_DIMENSIONS = ("purpose", "deployment", "tier", "shot", "hosting", "operation")
_TOP_SHOTS = 20


def _money(value: Any) -> Optional[float]:
    return float(value) if value is not None else None


def _effective_cost():
    return func.coalesce(UsageEvent.actual_cost, UsageEvent.estimated_cost)


def _hosting():
    return case((UsageEvent.provider.in_(SELF_HOSTED_PROVIDERS), "self_hosted"), else_="cloud")


def _group_column(dimension: str):
    columns = {
        "purpose": UsageEvent.purpose,
        "deployment": func.coalesce(func.nullif(UsageEvent.deployment_name, ""), UsageEvent.provider),
        "tier": UsageEvent.tier,
        "shot": UsageEvent.shot_id,
        "hosting": _hosting(),
        "operation": UsageEvent.operation,
    }
    if dimension not in columns:
        raise ValueError(f"不支持的维度: {dimension}（可选: {list(columns)}）")
    return columns[dimension]


def _metrics():
    priced_both = UsageEvent.actual_cost.isnot(None) & UsageEvent.estimated_cost.isnot(None)
    return (
        func.count().label("calls"),
        func.count().filter(UsageEvent.status != "success").label("failed"),
        func.sum(UsageEvent.estimated_cost).label("estimated"),
        func.sum(UsageEvent.actual_cost).label("actual"),
        func.sum(_effective_cost()).label("effective"),
        func.count()
        .filter(
            UsageEvent.status == "success",
            UsageEvent.actual_cost.is_(None),
            UsageEvent.estimated_cost.is_(None),
        )
        .label("unpriced"),
        func.sum(UsageEvent.input_tokens).label("input_tokens"),
        func.sum(UsageEvent.output_tokens).label("output_tokens"),
        func.sum(UsageEvent.cached_tokens).label("cached_tokens"),
        func.sum(UsageEvent.gpu_seconds).label("gpu_seconds"),
        func.sum(case((UsageEvent.purpose == "shot_video", UsageEvent.output_units), else_=0)).label(
            "video_seconds"
        ),
        func.sum(case((priced_both, UsageEvent.estimated_cost), else_=None)).label("compared_estimated"),
        func.sum(case((priced_both, UsageEvent.actual_cost), else_=None)).label("compared_actual"),
    )


def _row_metrics(row: Any) -> dict[str, Any]:
    compared_estimated = row.compared_estimated
    compared_actual = row.compared_actual
    error_pct = None
    if compared_estimated and compared_actual is not None:
        error_pct = round(
            float((Decimal(compared_actual) - Decimal(compared_estimated)) / Decimal(compared_estimated))
            * 100,
            2,
        )
    return {
        "calls": int(row.calls or 0),
        "failed": int(row.failed or 0),
        "estimated_cost": _money(row.estimated),
        "actual_cost": _money(row.actual),
        "effective_cost": _money(row.effective),
        "unpriced_calls": int(row.unpriced or 0),
        "input_tokens": int(row.input_tokens or 0),
        "output_tokens": int(row.output_tokens or 0),
        "cached_tokens": int(row.cached_tokens or 0),
        "gpu_seconds": float(row.gpu_seconds or 0),
        "video_seconds": float(row.video_seconds or 0),
        # 同时有估算与实际的调用上，实际相对估算的偏差（正数 = 实际更贵）
        "estimate_error_pct": error_pct,
    }


async def cost_report(
    session: AsyncSession,
    *,
    project_id: str,
    episode_id: Optional[str] = None,
    dimensions: tuple[str, ...] = GROUP_DIMENSIONS,
) -> dict[str, Any]:
    filters = [UsageEvent.project_id == project_id]
    if episode_id:
        filters.append(UsageEvent.episode_id == episode_id)

    total_row = (await session.execute(select(*_metrics()).where(*filters))).one()
    report: dict[str, Any] = {
        "project_id": project_id,
        "episode_id": episode_id,
        "currency": "CNY",
        "totals": _row_metrics(total_row),
        "breakdown": {},
    }
    for dimension in dimensions:
        column = _group_column(dimension)
        stmt = select(column.label("key"), *_metrics()).where(*filters).group_by(column)
        if dimension == "shot":
            stmt = (
                stmt.where(UsageEvent.shot_id.isnot(None))
                .order_by(_effective_cost_sum().desc())
                .limit(_TOP_SHOTS)
            )
        rows = (await session.execute(stmt)).all()
        items = [{"key": row.key or "", **_row_metrics(row)} for row in rows]
        if dimension != "shot":
            items.sort(key=lambda item: item["effective_cost"] or 0, reverse=True)
        report["breakdown"][dimension] = items
    return report


def _effective_cost_sum():
    return func.coalesce(func.sum(_effective_cost()), 0)


def _usage_dict(event: UsageEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "operation": event.operation,
        "purpose": event.purpose,
        "tier": event.tier,
        "deployment": event.deployment_name or event.provider,
        "provider": event.provider,
        "model": event.model,
        "status": event.status,
        "attempt": event.attempt,
        "estimated_cost": _money(event.estimated_cost),
        "actual_cost": _money(event.actual_cost),
        "cost_source": event.cost_source,
        "input_tokens": event.input_tokens,
        "output_tokens": event.output_tokens,
        "cached_tokens": event.cached_tokens,
        "gpu_seconds": event.gpu_seconds,
        "latency_ms": event.latency_ms,
        "cache_hit": event.cache_hit,
        "error_code": event.error_code,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _invocation_dict(row: ModelInvocation) -> dict[str, Any]:
    return {
        "id": row.id,
        "purpose": row.purpose,
        "deployment": row.deployment_name,
        "provider": row.provider,
        "model": row.model,
        "status": row.status,
        "attempt": row.attempt,
        "latency_ms": row.latency_ms,
        "error_code": row.error_code,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def _sum_costs(usage: list[dict[str, Any]]) -> dict[str, Optional[float]]:
    def total(key: str) -> Optional[float]:
        values = [item[key] for item in usage if item[key] is not None]
        return round(sum(values), 6) if values else None

    effective = [
        item["actual_cost"] if item["actual_cost"] is not None else item["estimated_cost"]
        for item in usage
        if item["actual_cost"] is not None or item["estimated_cost"] is not None
    ]
    return {
        "estimated_cost": total("estimated_cost"),
        "actual_cost": total("actual_cost"),
        "effective_cost": round(sum(effective), 6) if effective else None,
    }


async def run_trace(session: AsyncSession, run: WorkflowRun) -> dict[str, Any]:
    """一次 WorkflowRun 的调用树：步骤 → 任务 → 模型调用 → 用量；未挂在任务上的 LLM 用量挂到步骤上。"""
    steps = (
        (
            await session.execute(
                select(WorkflowStep).where(WorkflowStep.run_id == run.id).order_by(WorkflowStep.sort_order)
            )
        )
        .scalars()
        .all()
    )
    jobs = (
        (
            await session.execute(
                select(GenerationJob)
                .where(GenerationJob.workflow_run_id == run.id)
                .order_by(GenerationJob.created_at)
            )
        )
        .scalars()
        .all()
    )
    job_ids = [job.id for job in jobs]
    invocations = (
        (
            await session.execute(
                select(ModelInvocation).where(
                    (ModelInvocation.workflow_run_id == run.id)
                    | (ModelInvocation.job_id.in_(job_ids) if job_ids else false())
                )
            )
        )
        .scalars()
        .all()
    )
    usage_rows = (
        (
            await session.execute(
                select(UsageEvent)
                .where(
                    (UsageEvent.workflow_run_id == run.id)
                    | (UsageEvent.job_id.in_(job_ids) if job_ids else false())
                )
                .order_by(UsageEvent.created_at)
            )
        )
        .scalars()
        .all()
    )

    invocations_by_job: dict[str, list[dict[str, Any]]] = {}
    for row in invocations:
        if row.job_id:
            invocations_by_job.setdefault(row.job_id, []).append(_invocation_dict(row))
    usage_by_job: dict[str, list[dict[str, Any]]] = {}
    usage_by_step: dict[str, list[dict[str, Any]]] = {}
    for event in usage_rows:
        if event.job_id:
            usage_by_job.setdefault(event.job_id, []).append(_usage_dict(event))
        elif event.workflow_step_id:
            usage_by_step.setdefault(event.workflow_step_id, []).append(_usage_dict(event))

    jobs_by_step: dict[str, list[dict[str, Any]]] = {}
    for job in jobs:
        usage = usage_by_job.get(job.id, [])
        jobs_by_step.setdefault(job.workflow_step_id or "", []).append(
            {
                "id": job.id,
                "operation": job.operation,
                "status": job.status,
                "shot_id": job.shot_id,
                "provider": job.provider,
                "model": job.model,
                "attempt": job.attempt,
                "step_attempt": job.step_attempt,
                "error_code": job.error_code,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                "invocations": invocations_by_job.get(job.id, []),
                "usage": usage,
                **_sum_costs(usage),
            }
        )

    step_items = []
    for step in steps:
        step_jobs = jobs_by_step.get(step.id, [])
        direct_usage = usage_by_step.get(step.id, [])
        all_usage = direct_usage + [item for job in step_jobs for item in job["usage"]]
        step_items.append(
            {
                "id": step.id,
                "node_key": step.node_key,
                "node_type": step.node_type,
                "entity_id": step.entity_id,
                "status": step.status,
                "attempt": step.attempt,
                "error_code": step.error_code,
                "jobs": step_jobs,
                "usage": direct_usage,
                **_sum_costs(all_usage),
            }
        )
    all_usage = [_usage_dict(event) for event in usage_rows]
    return {
        "run": {
            "id": run.id,
            "project_id": run.project_id,
            "status": run.status,
            "quoted_cost": _money(run.estimated_cost),
            "cost_limit": _money(run.cost_limit),
            **_sum_costs(all_usage),
        },
        "steps": step_items,
        "unassigned_jobs": jobs_by_step.get("", []),
    }
