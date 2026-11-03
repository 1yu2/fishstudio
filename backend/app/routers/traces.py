"""请求到产物的 Trace 查询；按项目归属隔离。"""

from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import Artifact, GenerationJob, JobEvent, ModelInvocation, SeriesProject, UsageEvent, User
from app.services import asset_service

router = APIRouter()


def _visible_jobs(user: User):
    stmt = select(GenerationJob).join(SeriesProject, GenerationJob.project_id == SeriesProject.id)
    if not user.is_admin:
        stmt = stmt.where(SeriesProject.user_id == user.id)
    return stmt


def _fields(row, names: str) -> dict:
    return {key: getattr(row, key) for key in names.split()}


def _elapsed(start, end):
    return max(0, int((end - start).total_seconds() * 1000)) if start and end else None


async def _run_trace(session: AsyncSession, user: User, run_id: str) -> dict:
    trace_run = GenerationJob.params["trace"]["run_id"].astext
    jobs = list(
        await session.scalars(
            _visible_jobs(user)
            .where(or_(trace_run == run_id, (trace_run.is_(None)) & (GenerationJob.id == run_id)))
            .order_by(GenerationJob.created_at, GenerationJob.id)
            .limit(501)
        )
    )
    if not jobs:
        raise HTTPException(404, "Trace not found")
    truncated = len(jobs) > 500
    jobs = jobs[:500]
    ids = [job.id for job in jobs]
    related = {}
    for model, order in (
        (ModelInvocation, ModelInvocation.created_at),
        (UsageEvent, UsageEvent.created_at),
        (JobEvent, JobEvent.occurred_at),
        (Artifact, Artifact.created_at),
    ):
        grouped = defaultdict(list)
        for row in await session.scalars(
            select(model).where(model.job_id.in_(ids)).order_by(order, model.id)
        ):
            grouped[row.job_id].append(row)
        related[model] = grouped
    result = []
    total = Decimal(0)
    requests = set()
    for job in jobs:
        params = job.params or {}
        trace = params.get("trace") or {}
        requests.add(trace.get("request_id") or job.id)
        usage = related[UsageEvent][job.id]
        total += sum(
            (u.actual_cost if u.actual_cost is not None else u.estimated_cost or Decimal(0) for u in usage),
            Decimal(0),
        )
        result.append(
            {
                **_fields(
                    job,
                    "id operation status provider model created_at started_at finished_at attempt error_code error",
                ),
                "queue_wait_ms": _elapsed(job.created_at, job.started_at),
                "duration_ms": _elapsed(job.started_at, job.finished_at),
                "canvas_id": params.get("canvas_id") or trace.get("canvas_id"),
                "canvas_node_id": params.get("canvas_node_id"),
                "source": params.get("source"),
                "invocations": [
                    _fields(
                        row,
                        "id request_id purpose provider model deployment_name status attempt latency_ms error_code external_request_id",
                    )
                    for row in related[ModelInvocation][job.id]
                ],
                "usage": [
                    _fields(
                        row,
                        "estimated_cost actual_cost cost_source unit_type input_units output_units request_id run_id",
                    )
                    for row in usage
                ],
                "events": [
                    _fields(row, "event status progress occurred_at") for row in related[JobEvent][job.id]
                ],
                "artifacts": [
                    {
                        "id": row.id,
                        "type": row.type,
                        "url": asset_service.artifact_url(row),
                        "lineage": (row.artifact_metadata or {}).get("lineage"),
                        "l1_check": (row.artifact_metadata or {}).get("l1_check"),
                    }
                    for row in related[Artifact][job.id]
                ],
            }
        )
    return {
        "run_id": run_id,
        "truncated": truncated,
        "request_ids": sorted(requests),
        "started_at": min(job.created_at for job in jobs),
        "finished_at": max(job.finished_at for job in jobs) if all(job.finished_at for job in jobs) else None,
        "total_cost": total,
        "jobs": result,
    }


@router.get("/traces/{run_id}")
async def get_trace(
    run_id: str, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    return await _run_trace(session, user, run_id)


@router.get("/jobs/{job_id}/trace")
async def get_job_trace(
    job_id: str, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    job = await session.scalar(_visible_jobs(user).where(GenerationJob.id == job_id))
    if job is None:
        raise HTTPException(404, "Trace not found")
    run_id = ((job.params or {}).get("trace") or {}).get("run_id") or job.id
    return await _run_trace(session, user, run_id)
