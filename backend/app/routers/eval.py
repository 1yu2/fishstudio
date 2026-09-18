"""质量、成本、运营反馈 Eval API。"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.db.database import get_session
from app.db.models import (
    BenchmarkDataset,
    EvalRun,
    GenerationJob,
    ModelInvocation,
    PriceTableEntry,
    Publication,
    SeriesProject,
    UsageEvent,
    User,
    WorkflowStep,
)
from app.services import eval_service, series_service

router = APIRouter(dependencies=[Depends(get_current_user)])


class EvalRunRequest(BaseModel):
    dataset_id: str
    provider: str
    deployment: str
    model: str
    project_id: Optional[str] = None
    samples: list[dict[str, Any]]


class PriceEntryRequest(BaseModel):
    version: str
    provider: str
    deployment: str = ""
    model: str
    unit_type: str
    unit_price: Decimal = Field(ge=0)
    currency: str = "CNY"
    effective_from: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    effective_to: Optional[datetime] = None
    metadata_snapshot: dict[str, Any] = Field(default_factory=dict)


class PublicationMetricsRequest(BaseModel):
    metrics: dict[str, Any]
    content_revisions: dict[str, Any] = Field(default_factory=dict)


def _run_payload(row: EvalRun) -> dict[str, Any]:
    return {
        "id": row.id, "dataset_id": row.dataset_id, "project_id": row.project_id,
        "provider": row.provider, "deployment": row.deployment, "model": row.model,
        "status": row.status, "input_hash": row.input_hash, "metrics": row.metrics,
        "failure_reasons": row.failure_reasons, "score": row.score,
        "explanation": row.explanation,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/eval/datasets")
async def list_datasets(session: AsyncSession = Depends(get_session)):
    rows = await eval_service.seed_benchmarks(session)
    return [{
        "id": row.id, "key": row.key, "version": row.version, "name": row.name,
        "shot_count": row.shot_count, "content_hash": row.content_hash,
        "input_payload": row.input_payload, "expected_constraints": row.expected_constraints,
    } for row in rows]


@router.post("/eval/runs")
async def create_eval_run(
    body: EvalRunRequest, session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    dataset = await session.get(BenchmarkDataset, body.dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="benchmark dataset 不存在")
    if body.project_id:
        try:
            project = await series_service.get_project(session, body.project_id, user.id, is_admin=user.is_admin)
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error))
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
    try:
        row = await eval_service.create_eval_run(
            session, dataset=dataset, provider=body.provider, deployment=body.deployment,
            model=body.model, samples=body.samples, project_id=body.project_id,
            created_by=user.id,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return _run_payload(row)


@router.get("/eval/runs")
async def list_eval_runs(
    dataset_id: Optional[str] = None, deployment: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    stmt = select(EvalRun).order_by(EvalRun.created_at.desc()).limit(200)
    if not user.is_admin:
        owned_projects = select(SeriesProject.id).where(SeriesProject.user_id == user.id)
        stmt = stmt.where(or_(EvalRun.project_id.is_(None), EvalRun.project_id.in_(owned_projects)))
    if dataset_id:
        stmt = stmt.where(EvalRun.dataset_id == dataset_id)
    if deployment:
        stmt = stmt.where(EvalRun.deployment == deployment)
    return [_run_payload(row) for row in (await session.execute(stmt)).scalars()]


@router.get("/eval/routing-stats")
async def get_routing_stats(session: AsyncSession = Depends(get_session)):
    return await eval_service.routing_stats(session)


@router.get("/eval/projects/{project_id}/feedback")
async def get_project_feedback(
    project_id: str, session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        project = await series_service.get_project(session, project_id, user.id, is_admin=user.is_admin)
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error))
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return await eval_service.project_feedback(session, project_id)


@router.get("/eval/usage")
async def drilldown_usage(
    project_id: Optional[str] = None, episode_id: Optional[str] = None,
    shot_id: Optional[str] = None, step_id: Optional[str] = None,
    purpose: Optional[str] = None, provider: Optional[str] = None,
    deployment: Optional[str] = None, session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if project_id:
        try:
            project = await series_service.get_project(session, project_id, user.id, is_admin=user.is_admin)
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error))
        if project is None:
            raise HTTPException(status_code=404, detail="项目不存在")
    stmt = select(UsageEvent, GenerationJob, ModelInvocation, WorkflowStep).outerjoin(
        GenerationJob, GenerationJob.id == UsageEvent.job_id
    ).outerjoin(ModelInvocation, ModelInvocation.id == UsageEvent.invocation_id).outerjoin(
        WorkflowStep, WorkflowStep.id == GenerationJob.workflow_step_id
    ).limit(1000)
    if not user.is_admin:
        owned_projects = select(SeriesProject.id).where(SeriesProject.user_id == user.id)
        stmt = stmt.where(UsageEvent.project_id.in_(owned_projects))
    for condition in (
        UsageEvent.project_id == project_id if project_id else None,
        GenerationJob.shot_id == shot_id if shot_id else None,
        GenerationJob.workflow_step_id == step_id if step_id else None,
        UsageEvent.provider == provider if provider else None,
        ModelInvocation.purpose == purpose if purpose else None,
        ModelInvocation.deployment_name == deployment if deployment else None,
    ):
        if condition is not None:
            stmt = stmt.where(condition)
    rows = (await session.execute(stmt)).all()
    # 新记录优先使用 step 实体；兼容旧记录 event_metadata 中的 episode_id。
    if episode_id:
        rows = [row for row in rows if (
            (row[3] and row[3].entity_type == "episode" and row[3].entity_id == episode_id)
            or (row[0].event_metadata or {}).get("episode_id") == episode_id
        )]
    return [{
        "usage_id": usage.id,
        "episode_id": step.entity_id if step and step.entity_type == "episode" else (usage.event_metadata or {}).get("episode_id"),
        "shot_id": job.shot_id if job else None, "step_id": job.workflow_step_id if job else None,
        "purpose": invocation.purpose if invocation else None,
        "provider": usage.provider, "deployment": invocation.deployment_name if invocation else None,
        "model": usage.model, "latency_ms": usage.latency_ms,
        "cost_cny": str(usage.actual_cost if usage.actual_cost is not None else usage.estimated_cost) if (usage.actual_cost is not None or usage.estimated_cost is not None) else "unavailable",
    } for usage, job, invocation, step in rows]


@router.get("/eval/prices")
async def list_prices(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(PriceTableEntry).order_by(PriceTableEntry.effective_from.desc()))).scalars()
    return [{
        "id": row.id, "version": row.version, "provider": row.provider,
        "deployment": row.deployment, "model": row.model, "unit_type": row.unit_type,
        "unit_price": str(row.unit_price), "currency": row.currency,
        "effective_from": row.effective_from.isoformat(),
        "effective_to": row.effective_to.isoformat() if row.effective_to else None,
    } for row in rows]


@router.post("/eval/prices")
async def create_price(
    body: PriceEntryRequest, session: AsyncSession = Depends(get_session),
    _admin: User = Depends(require_admin),
):
    row = PriceTableEntry(**body.model_dump())
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {"id": row.id, "version": row.version}


@router.post("/eval/publications/{publication_id}/metrics")
async def record_publication_metrics(
    publication_id: str, body: PublicationMetricsRequest,
    session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user),
):
    publication = await session.get(Publication, publication_id)
    if publication is None:
        raise HTTPException(status_code=404, detail="publication 不存在")
    try:
        project = await series_service.get_project(
            session, publication.project_id, user.id, is_admin=user.is_admin,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=str(error))
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    row = await eval_service.record_publication_metrics(
        session, publication, body.metrics, content_revisions=body.content_revisions,
        recorded_by=user.id,
    )
    return {"id": row.id, "artifact_id": row.artifact_id, "model_invocation_ids": row.model_invocation_ids}
