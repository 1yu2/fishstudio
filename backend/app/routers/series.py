"""系列剧（series）路由：项目 / 分镜 / 资产 / 审批 / 生成 Gate。

错误映射约定：PermissionError → 403；目标不存在 → 404；参数/版本错误 → 400。
"""

import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.db.database import get_session
from app.db.models import (
    Artifact,
    ArtifactCandidate,
    AssetCard,
    CloneVoiceQuoteRecord,
    DirectorPlanRevision,
    Episode,
    GenerationJob,
    InvalidationEvent,
    Publication,
    PublishPackage,
    QCReport,
    ReferenceAnalysis,
    Scene,
    ScriptRevision,
    SeriesProject,
    Shot,
    StoryboardRevision,
    TimelineRevision,
    UsageEvent,
    User,
    WorkflowRun,
    WorkflowStep,
)
from app.schemas.series import (
    ApprovalOut,
    AssetCardCreate,
    AssetCardOut,
    BibleRevisionCreate,
    BibleRevisionOut,
    DirectorPlanCreate,
    DirectorPlanOut,
    ObserverReviewCreate,
    ObserverReviewOut,
    ObserverRunRequest,
    ProjectCreate,
    ProjectOut,
    ScriptRevisionCreate,
    ScriptRevisionOut,
    StoryboardRevisionOut,
)
from app.services import (
    approval_service,
    asset_service,
    bible_service,
    candidate_service,
    dependency_invalidation_service,
    director_service,
    feedback_service,
    observer_service,
    planner_service,
    publish_service,
    reference_analysis_service,
    script_service,
    series_service,
    storyboard_service,
    timeline_service,
    workflow_service,
)

router = APIRouter(dependencies=[Depends(get_current_user)])


def _403(e: PermissionError) -> HTTPException:
    return HTTPException(status_code=403, detail=str(e) or "无权访问")


async def _owned_project(session: AsyncSession, project_id: str, user: User):
    try:
        project = await series_service.get_project(session, project_id, user.id, is_admin=user.is_admin)
    except PermissionError as e:
        raise _403(e)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


async def _owned_episode_project(session: AsyncSession, episode_id: str, user: User):
    episode = await session.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="集不存在")
    return episode, await _owned_project(session, episode.project_id, user)


async def _complete_episode_creative_steps(
    session: AsyncSession,
    project_id: str,
    episode_id: str,
    node_type: str,
    output_snapshot: dict[str, Any],
) -> None:
    """Studio 与 Agent 共用的创作节点推进语义。"""
    steps = (
        (
            await session.execute(
                select(WorkflowStep)
                .join(WorkflowRun, WorkflowStep.run_id == WorkflowRun.id)
                .where(
                    WorkflowRun.project_id == project_id,
                    WorkflowRun.status.notin_({"succeeded", "failed", "canceled"}),
                    WorkflowStep.entity_id == episode_id,
                    WorkflowStep.node_type == node_type,
                    WorkflowStep.status.in_({"draft", "ready", "waiting_approval"}),
                )
            )
        )
        .scalars()
        .all()
    )
    for step in steps:
        await workflow_service.complete_step(session, step.id, output_snapshot)


# ─────────────────────────────────────────
# 项目
# ─────────────────────────────────────────


@router.post("/series/projects", response_model=ProjectOut)
async def create_project(
    body: ProjectCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        return await series_service.create_project(
            session,
            user.id,
            canvas_id=body.canvas_id,
            project_type=body.project_type,
            title=body.title,
            workflow_template_id=body.workflow_template_id,
            active_skill_ids=body.active_skill_ids,
            settings=body.settings,
        )
    except PermissionError as e:
        raise _403(e)
    except ValueError as e:
        # canvas 不存在按 404，其余（如已绑定其他项目）按 400
        status_code = 404 if "不存在" in str(e) else 400
        raise HTTPException(status_code=status_code, detail=str(e))


@router.get("/series/projects", response_model=list[ProjectOut])
async def list_projects(
    project_type: Optional[str] = None,
    status: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    return await series_service.list_projects(session, user.id, project_type=project_type, status=status)


@router.get("/series/projects/{project_id}")
async def get_project(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    project = await _owned_project(session, project_id, user)
    episodes = await storyboard_service.list_episodes_with_storyboard(session, project.id)
    return {**ProjectOut.model_validate(project).model_dump(mode="json"), "episodes": episodes}


class ClaimRequest(BaseModel):
    user_id: int


class StudioStartRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    mode: str = "quick"
    story: str = ""
    style: str = ""
    target_duration_ms: int = Field(default=90000, ge=1000, le=3600000)
    episode_count: int = Field(default=1, ge=1, le=100)
    budget_cny: Optional[float] = Field(default=None, ge=0)
    aspect_ratio: str = "9:16"


class WorkspaceModeRequest(BaseModel):
    mode: str


@router.post("/series/studio/start")
async def start_studio_project(
    body: StudioStartRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """快速/专业入口的统一写路径：相同 Project、Episode 与 Workflow。"""
    if body.mode not in {"quick", "professional"}:
        raise HTTPException(status_code=400, detail="mode 必须是 quick 或 professional")
    settings = {
        "workspace_mode": body.mode,
        "story": body.story,
        "style": body.style,
        "goal": body.story,
        "episode_count": body.episode_count,
        "target_duration_ms": body.target_duration_ms,
        "budget_cny": body.budget_cny,
        "aspect_ratio": body.aspect_ratio,
    }
    project = await series_service.create_project(
        session,
        user.id,
        project_type="short_drama",
        title=body.title.strip(),
        settings=settings,
        episode_count=body.episode_count,
        target_duration_ms=body.target_duration_ms,
    )
    try:
        await workflow_service.seed_builtin_templates(session)
        plan = planner_service.plan_from_template(body.story or body.title, settings)
        params = {
            **plan.get("params", {}),
            "goal": body.story,
            "episodes": body.episode_count,
            "aspect_ratio": body.aspect_ratio,
        }
        if body.budget_cny is not None:
            params["cost_limit"] = body.budget_cny
        run = await workflow_service.create_run(
            session,
            project=project,
            trigger_type="api",
            input_snapshot=params,
            created_by=user.id,
            planner_model=plan.get("planner_model", "rule-based"),
        )
        await workflow_service.advance_run(session, run.id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {
        "project": ProjectOut.model_validate(project).model_dump(mode="json"),
        "run": {"id": run.id, "status": run.status},
        "studio_locator": {"project_id": project.id, "entity_type": "project", "entity_id": project.id},
    }


@router.patch("/series/projects/{project_id}/workspace-mode", response_model=ProjectOut)
async def set_workspace_mode(
    project_id: str,
    body: WorkspaceModeRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        return await series_service.set_workspace_mode(
            session, project_id, body.mode, user.id, is_admin=user.is_admin
        )
    except PermissionError as error:
        raise _403(error)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.get("/series/projects/{project_id}/workspace-summary")
async def get_workspace_summary(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    project = await _owned_project(session, project_id, user)
    latest_run = await session.scalar(
        select(WorkflowRun)
        .where(WorkflowRun.project_id == project.id)
        .order_by(WorkflowRun.created_at.desc())
        .limit(1)
    )
    steps = (
        []
        if latest_run is None
        else list(
            (
                await session.execute(select(WorkflowStep).where(WorkflowStep.run_id == latest_run.id))
            ).scalars()
        )
    )
    jobs = list(
        (await session.execute(select(GenerationJob).where(GenerationJob.project_id == project.id))).scalars()
    )
    qc_rows = list(
        (await session.execute(select(QCReport).where(QCReport.project_id == project.id))).scalars()
    )
    invalidations = list(
        (
            await session.execute(select(InvalidationEvent).where(InvalidationEvent.project_id == project.id))
        ).scalars()
    )
    usage_rows = list(
        (await session.execute(select(UsageEvent).where(UsageEvent.project_id == project.id))).scalars()
    )
    spent = sum(
        float(row.actual_cost if row.actual_cost is not None else row.estimated_cost or 0)
        for row in usage_rows
    )
    stale_count = sum(len(row.affected_entities or []) for row in invalidations)
    completed = sum(step.status in {"succeeded", "skipped"} for step in steps)
    stage = next(
        (
            step.node_type
            for step in steps
            if step.status in {"ready", "queued", "running", "waiting_approval", "failed"}
        ),
        project.status,
    )
    return {
        "project_id": project.id,
        "workspace_mode": (project.settings or {}).get("workspace_mode", "professional"),
        "stage": stage,
        "progress": {"completed": completed, "total": len(steps)},
        "budget": {"limit_cny": (project.settings or {}).get("budget_cny"), "spent_cny": round(spent, 4)},
        "stale_impact_count": stale_count,
        "qa_failed_count": sum(row.status == "failed" for row in qc_rows),
        "pending_approval_count": sum(step.status == "waiting_approval" for step in steps),
        "job_error_count": sum(job.status == "failed" for job in jobs),
        "run_id": latest_run.id if latest_run else None,
    }


@router.post("/series/projects/{project_id}/claim")
async def claim_project(
    project_id: str,
    body: ClaimRequest,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """认领历史无主项目（review 0.4）：仅 admin；仅 user_id 为 NULL 的项目可认领。"""
    project = await session.get(SeriesProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if project.user_id is not None:
        raise HTTPException(status_code=409, detail="项目已有归属，无需认领")
    target = await session.get(User, body.user_id)
    if target is None:
        raise HTTPException(status_code=400, detail=f"user {body.user_id} 不存在")
    project.user_id = body.user_id
    await session.commit()
    return {"id": project.id, "user_id": project.user_id, "claimed_by": admin.id}


# ─────────────────────────────────────────
# Series / Style Bible 版本
# ─────────────────────────────────────────


@router.post(
    "/series/projects/{project_id}/bibles/{kind}",
    response_model=BibleRevisionOut,
    status_code=201,
)
async def create_bible_revision(
    project_id: str,
    kind: str,
    body: BibleRevisionCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if kind not in {"series", "style"}:
        raise HTTPException(status_code=404, detail="Bible 类型必须是 series 或 style")
    project = await _owned_project(session, project_id, user)
    try:
        row = await bible_service.create_revision(
            session,
            project.id,
            kind,
            body.structured_payload,
            user_id=user.id,
            is_admin=user.is_admin,
            created_by=str(user.id),
            source_type=body.source_type,
            based_on_revision=body.based_on_revision,
            schema_version=body.schema_version,
        )
        await _complete_episode_creative_steps(
            session,
            project.id,
            project.id,
            f"{kind}.bible.generate",
            {"revision": row.revision},
        )
        return row
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.get(
    "/series/projects/{project_id}/bibles/{kind}",
    response_model=list[BibleRevisionOut],
)
async def list_bible_revisions(
    project_id: str,
    kind: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if kind not in {"series", "style"}:
        raise HTTPException(status_code=404, detail="Bible 类型必须是 series 或 style")
    try:
        return await bible_service.list_revisions(
            session, project_id, kind, user_id=user.id, is_admin=user.is_admin
        )
    except PermissionError as error:
        raise _403(error)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error))


@router.get(
    "/series/projects/{project_id}/bibles/{kind}/current",
    response_model=BibleRevisionOut,
)
async def get_current_bible_revision(
    project_id: str,
    kind: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if kind not in {"series", "style"}:
        raise HTTPException(status_code=404, detail="Bible 类型必须是 series 或 style")
    try:
        row = await bible_service.get_current_revision(
            session, project_id, kind, user_id=user.id, is_admin=user.is_admin
        )
    except PermissionError as error:
        raise _403(error)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error))
    if row is None:
        raise HTTPException(status_code=404, detail="项目还没有该 Bible revision")
    return row


# ─────────────────────────────────────────
# 局部依赖失效预览 / 审计
# ─────────────────────────────────────────


class InvalidationPreviewRequest(BaseModel):
    source_type: str
    source_id: str
    changed_fields: list[str] = Field(default_factory=list)
    reason: str = "content_changed"


@router.post("/series/projects/{project_id}/invalidation/preview")
async def preview_invalidation(
    project_id: str,
    body: InvalidationPreviewRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await _owned_project(session, project_id, user)
    try:
        return await dependency_invalidation_service.preview_invalidation(
            session,
            project_id=project_id,
            source_type=body.source_type,
            source_id=body.source_id,
            changed_fields=body.changed_fields,
            user_id=user.id,
            is_admin=user.is_admin,
            reason=body.reason,
        )
    except PermissionError as error:
        raise _403(error)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.get("/series/projects/{project_id}/invalidation-events")
async def list_invalidation_events(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await _owned_project(session, project_id, user)
    rows = (
        (
            await session.execute(
                select(InvalidationEvent)
                .where(InvalidationEvent.project_id == project_id)
                .order_by(InvalidationEvent.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": row.id,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "source_revision": row.source_revision,
            "changed_fields": row.changed_fields,
            "reason": row.reason,
            "affected": row.affected_entities,
            "created_at": row.created_at,
        }
        for row in rows
    ]


# ─────────────────────────────────────────
# 单集剧本版本
# ─────────────────────────────────────────


@router.post(
    "/series/episodes/{episode_id}/scripts",
    response_model=ScriptRevisionOut,
    status_code=201,
)
async def create_script_revision(
    episode_id: str,
    body: ScriptRevisionCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        _, project = await _owned_episode_project(session, episode_id, user)
        revision = await script_service.create_revision(
            session,
            episode_id,
            body.structured_payload,
            user_id=user.id,
            is_admin=user.is_admin,
            created_by=str(user.id),
            source_type=body.source_type,
            based_on_revision=body.based_on_revision,
            schema_version=body.schema_version,
        )
        await _complete_episode_creative_steps(
            session,
            project.id,
            episode_id,
            "episode.script.generate",
            {"revision": revision.revision},
        )
        return revision
    except PermissionError as e:
        raise _403(e)
    except ValueError as e:
        status_code = 404 if "not found" in str(e) else 400
        raise HTTPException(status_code=status_code, detail=str(e))


@router.get(
    "/series/episodes/{episode_id}/scripts",
    response_model=list[ScriptRevisionOut],
)
async def list_script_revisions(
    episode_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        return await script_service.list_revisions(
            session, episode_id, user_id=user.id, is_admin=user.is_admin
        )
    except PermissionError as e:
        raise _403(e)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/series/episodes/{episode_id}/scripts/current",
    response_model=ScriptRevisionOut,
)
async def get_current_script_revision(
    episode_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        row = await script_service.get_current_revision(
            session, episode_id, user_id=user.id, is_admin=user.is_admin
        )
    except PermissionError as e:
        raise _403(e)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if row is None:
        raise HTTPException(status_code=404, detail="该集还没有剧本 revision")
    return row


@router.post("/series/episodes/{episode_id}/director-plans", response_model=DirectorPlanOut)
async def create_director_plan(
    episode_id: str,
    body: DirectorPlanCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    episode, _ = await _owned_episode_project(session, episode_id, user)
    try:
        row = await director_service.create_plan(
            session,
            episode.id,
            based_on_script_revision_id=body.based_on_script_revision_id,
            payload=body.payload,
            asset_version_snapshot=body.asset_version_snapshot,
            platform_constraints=body.platform_constraints,
            source_type=body.source_type,
            created_by=str(user.id),
        )
        await _complete_episode_creative_steps(
            session,
            episode.project_id,
            episode.id,
            "director.plan.generate",
            {"director_plan_revision_id": row.id, "revision": row.revision},
        )
        await session.commit()
        await session.refresh(row)
        return row
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.get("/series/episodes/{episode_id}/director-plans", response_model=list[DirectorPlanOut])
async def list_director_plans(
    episode_id: str, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    await _owned_episode_project(session, episode_id, user)
    return await director_service.list_plans(session, episode_id)


@router.post("/series/episodes/{episode_id}/observer-reviews", response_model=ObserverReviewOut)
async def create_observer_review(
    episode_id: str,
    body: ObserverReviewCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    episode, _ = await _owned_episode_project(session, episode_id, user)
    deterministic = [item.model_dump(mode="json") for item in body.deterministic_findings]
    if body.target_type == "script":
        target = await session.get(ScriptRevision, body.target_id)
        if target is not None:
            deterministic += director_service.deterministic_script_findings(target.structured_payload)
    elif body.target_type == "storyboard":
        target = await session.get(StoryboardRevision, body.target_id)
        plan = (
            (
                await session.execute(
                    select(DirectorPlanRevision)
                    .where(DirectorPlanRevision.episode_id == episode_id)
                    .order_by(DirectorPlanRevision.revision.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        intent_keys = {str(item.get("key")) for item in ((plan.payload if plan else {}).get("intents") or [])}
        if target is not None:
            deterministic += director_service.deterministic_continuity_findings(target.payload, intent_keys)
    try:
        row = await director_service.create_review(
            session,
            episode,
            review_type=body.review_type,
            target_type=body.target_type,
            target_id=body.target_id,
            target_revision=body.target_revision,
            score=body.score,
            deterministic_findings=deterministic,
            specialist_findings=[item.model_dump(mode="json") for item in body.specialist_findings],
            llm_findings=[item.model_dump(mode="json") for item in body.llm_findings],
            summary=body.summary,
            model_invocation_id=body.model_invocation_id,
            review_scope=body.review_scope,
            created_by=str(user.id),
        )
        if not row.blocking:
            node_type = (
                "episode.script.review" if body.target_type == "script" else "storyboard.continuity.check"
            )
            await _complete_episode_creative_steps(
                session,
                episode.project_id,
                episode.id,
                node_type,
                {"observer_review_id": row.id, "score": row.score},
            )
        await session.commit()
        await session.refresh(row)
        return row
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.get("/series/episodes/{episode_id}/observer-reviews", response_model=list[ObserverReviewOut])
async def list_observer_reviews(
    episode_id: str,
    review_type: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await _owned_episode_project(session, episode_id, user)
    rows = await director_service.list_reviews(session, episode_id, review_type)
    invocation_ids = [row.model_invocation_id for row in rows if row.model_invocation_id]
    costs = {}
    if invocation_ids:
        costs = dict(
            (
                await session.execute(
                    select(UsageEvent.invocation_id, func.sum(UsageEvent.actual_cost))
                    .where(UsageEvent.invocation_id.in_(invocation_ids))
                    .group_by(UsageEvent.invocation_id)
                )
            ).all()
        )
    return [
        ObserverReviewOut.model_validate(row).model_copy(
            update={
                "model_cost_cny": float(costs[row.model_invocation_id])
                if row.model_invocation_id in costs and costs[row.model_invocation_id] is not None
                else None
            }
        )
        for row in rows
    ]


@router.post("/series/episodes/{episode_id}/observer-reviews/run", response_model=ObserverReviewOut)
async def run_observer_review(
    episode_id: str,
    body: ObserverRunRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    episode, _ = await _owned_episode_project(session, episode_id, user)
    if body.target_type == "script":
        target = await session.get(ScriptRevision, body.target_id)
        target_payload = target.structured_payload if target else None
        deterministic = director_service.deterministic_script_findings(target_payload or {})
    elif body.target_type == "storyboard":
        target = await session.get(StoryboardRevision, body.target_id)
        target_payload = target.payload if target else None
        plan = (
            (
                await session.execute(
                    select(DirectorPlanRevision)
                    .where(DirectorPlanRevision.episode_id == episode_id)
                    .order_by(DirectorPlanRevision.revision.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        intent_keys = {str(item.get("key")) for item in ((plan.payload if plan else {}).get("intents") or [])}
        deterministic = director_service.deterministic_continuity_findings(target_payload or {}, intent_keys)
    else:
        raise HTTPException(status_code=400, detail="target_type 仅支持 script 或 storyboard")
    if target is None or target.episode_id != episode_id or target.revision != body.target_revision:
        raise HTTPException(status_code=400, detail="reviewed revision 不存在或不匹配")
    node_type = "episode.script.review" if body.target_type == "script" else "storyboard.continuity.check"
    step = (
        (
            await session.execute(
                select(WorkflowStep)
                .join(WorkflowRun, WorkflowStep.run_id == WorkflowRun.id)
                .where(
                    WorkflowRun.project_id == episode.project_id,
                    WorkflowStep.entity_id == episode_id,
                    WorkflowStep.node_type == node_type,
                )
                .order_by(WorkflowStep.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    try:
        score, summary, llm_findings, invocation_id = await observer_service.run_review(
            project_id=episode.project_id,
            episode_id=episode_id,
            workflow_step_id=step.id if step else None,
            review_type=body.review_type,
            target_type=body.target_type,
            target_revision=body.target_revision,
            target_payload=target_payload,
            issue_codes=body.issue_codes,
            categories=body.categories,
            prompt_version=body.prompt_version,
        )
        row = await director_service.create_review(
            session,
            episode,
            review_type=body.review_type,
            target_type=body.target_type,
            target_id=body.target_id,
            target_revision=body.target_revision,
            score=score,
            deterministic_findings=deterministic,
            specialist_findings=[],
            llm_findings=llm_findings,
            summary=summary,
            model_invocation_id=invocation_id,
            review_scope={"issue_codes": body.issue_codes, "categories": body.categories},
            created_by="observer:model",
        )
        if not row.blocking:
            await _complete_episode_creative_steps(
                session,
                episode.project_id,
                episode.id,
                node_type,
                {"observer_review_id": row.id, "score": row.score, "model_invocation_id": invocation_id},
            )
        await session.commit()
        await session.refresh(row)
        return row
    except (ValueError, RuntimeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=502, detail=f"Observer 模型输出无效: {error}")


# ─────────────────────────────────────────
# 分镜
# ─────────────────────────────────────────


@router.get("/series/projects/{project_id}/storyboard")
async def get_storyboard(
    project_id: str,
    episode_id: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    project = await _owned_project(session, project_id, user)
    return await storyboard_service.list_episodes_with_storyboard(session, project.id, episode_id=episode_id)


class StoryboardSaveRequest(BaseModel):
    payload: dict[str, Any]
    based_on_script_revision_id: Optional[str] = None


@router.put("/series/episodes/{episode_id}/storyboard", response_model=StoryboardRevisionOut)
async def save_storyboard(
    episode_id: str,
    body: StoryboardSaveRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _, project = await _owned_episode_project(session, episode_id, user)
    try:
        kwargs: dict[str, Any] = {"created_by": str(user.id)}
        if body.based_on_script_revision_id is not None:
            kwargs["based_on_script_revision_id"] = body.based_on_script_revision_id
        revision = await storyboard_service.save_storyboard(
            session, project, episode_id, body.payload, **kwargs
        )
        await _complete_episode_creative_steps(
            session,
            project.id,
            episode_id,
            "storyboard.generate",
            {"revision": revision.revision},
        )
        return revision
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class ImportTextRequest(BaseModel):
    text: str


@router.post(
    "/series/episodes/{episode_id}/storyboard/import-text",
    response_model=StoryboardRevisionOut,
)
async def import_storyboard_text(
    episode_id: str,
    body: ImportTextRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _, project = await _owned_episode_project(session, episode_id, user)
    try:
        return await storyboard_service.import_legacy_text(
            session, project, episode_id, body.text, created_by=str(user.id)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class ShotUpdateRequest(BaseModel):
    duration_ms: Optional[int] = Field(default=None, gt=0, le=60000)
    shot_type: Optional[str] = None
    narrative_purpose: Optional[str] = None
    composition: Optional[str] = None
    action_start: Optional[str] = None
    action_end: Optional[str] = None
    emotion: Optional[str] = None
    first_frame_prompt: Optional[str] = None
    last_frame_prompt: Optional[str] = None
    generation_mode: Optional[str] = None
    continuity_constraints: Optional[dict[str, Any]] = None
    risk_tags: Optional[list[str]] = None
    locked_asset_versions: Optional[dict[str, int]] = None
    qa_status: Optional[str] = None
    visual_prompt: Optional[str] = None
    camera: Optional[dict[str, Any]] = None
    transition: Optional[dict[str, Any]] = None
    aspect_ratio: Optional[str] = None
    dialogue_tracks: Optional[list[Any]] = None
    narration_tracks: Optional[list[Any]] = None
    subtitle_tracks: Optional[list[Any]] = None
    status: Optional[str] = None


class CandidateEvaluateRequest(BaseModel):
    findings: list[dict[str, Any]] = Field(default_factory=list)
    score: Optional[float] = Field(default=None, ge=0, le=1)
    evaluated_by: str = "deterministic"


class CandidateRejectRequest(BaseModel):
    reason: str


class CandidateRegenerateRequest(BaseModel):
    reason: str = ""
    instruction: str = ""
    preserve_fields: list[str] = Field(default_factory=list)
    replace_fields: list[str] = Field(default_factory=list)
    confirmed_quote_id: str = ""


class TimelineRevisionCreateRequest(BaseModel):
    spec: dict[str, Any]
    parent_revision_id: Optional[str] = None
    storyboard_revision_id: Optional[str] = None


class QCRunRequest(BaseModel):
    render_artifact_id: Optional[str] = None
    phase: str = "final_qc"


class PublishPackageRequest(BaseModel):
    platform: str = "xiaohongshu"
    title: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)


def _timeline_out(row: TimelineRevision) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "episode_id": row.episode_id,
        "revision": row.revision,
        "parent_revision_id": row.parent_revision_id,
        "storyboard_revision_id": row.storyboard_revision_id,
        "status": row.status,
        "schema_version": row.schema_version,
        "spec": row.spec,
        "content_hash": row.content_hash,
        "created_by": row.created_by,
        "approved_by": row.approved_by,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _qc_out(row: QCReport) -> dict[str, Any]:
    return {
        "id": row.id,
        "episode_id": row.episode_id,
        "timeline_revision_id": row.timeline_revision_id,
        "render_artifact_id": row.render_artifact_id,
        "phase": row.phase,
        "status": row.status,
        "score": row.score,
        "technical_checks": row.technical_checks,
        "semantic_checks": row.semantic_checks,
        "issues": row.issues,
        "repair_targets": row.repair_targets,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _publication_out(row: Publication, package: Optional[PublishPackage] = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "episode_id": row.episode_id,
        "timeline_revision_id": row.timeline_revision_id,
        "render_artifact_id": row.render_artifact_id,
        "cover_artifact_id": row.cover_artifact_id,
        "qc_report_id": row.qc_report_id,
        "platform": row.platform,
        "title": row.title,
        "description": row.description,
        "tags": row.tags,
        "specifications": row.specifications,
        "status": row.status,
        "external_post_id": row.external_post_id,
        "package_id": package.id if package else None,
        "manifest_artifact_id": package.manifest_artifact_id if package else None,
    }


def _candidate_out(candidate: ArtifactCandidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "project_id": candidate.project_id,
        "shot_id": candidate.shot_id,
        "artifact_id": candidate.artifact_id,
        "job_id": candidate.job_id,
        "candidate_index": candidate.candidate_index,
        "status": candidate.status,
        "score": candidate.score,
        "qa_summary": candidate.qa_summary,
        "qa_findings": candidate.qa_findings,
        "rejection_reason": candidate.rejection_reason,
        "evaluated_by": candidate.evaluated_by,
        "evaluated_at": candidate.evaluated_at.isoformat() if candidate.evaluated_at else None,
        "selected_by": candidate.selected_by,
        "selected_at": candidate.selected_at.isoformat() if candidate.selected_at else None,
    }


async def _owned_candidate(session: AsyncSession, shot_id: str, candidate_id: str, user: User):
    shot = await session.get(Shot, shot_id)
    candidate = await session.get(ArtifactCandidate, candidate_id)
    if shot is None or candidate is None or candidate.shot_id != shot_id:
        raise HTTPException(status_code=404, detail="候选不存在")
    scene = await session.get(Scene, shot.scene_id)
    if scene is None:
        raise HTTPException(status_code=404, detail="镜头不存在")
    await _owned_project(session, scene.project_id, user)
    return candidate


@router.get("/series/shots/{shot_id}/candidates")
async def list_shot_candidates(
    shot_id: str, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    shot = await session.get(Shot, shot_id)
    if shot is None:
        raise HTTPException(status_code=404, detail="镜头不存在")
    scene = await session.get(Scene, shot.scene_id)
    if scene is None:
        raise HTTPException(status_code=404, detail="镜头不存在")
    await _owned_project(session, scene.project_id, user)
    return [_candidate_out(c) for c in await candidate_service.list_candidates(session, shot_id)]


@router.post("/series/shots/{shot_id}/candidates/{candidate_id}/evaluate")
async def evaluate_shot_candidate(
    shot_id: str,
    candidate_id: str,
    body: CandidateEvaluateRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    candidate = await _owned_candidate(session, shot_id, candidate_id, user)
    try:
        candidate = await candidate_service.evaluate_candidate(
            session, candidate, findings=body.findings, score=body.score, evaluated_by=body.evaluated_by
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _candidate_out(candidate)


@router.post("/series/shots/{shot_id}/candidates/{candidate_id}/promote")
async def promote_shot_candidate(
    shot_id: str,
    candidate_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    candidate = await _owned_candidate(session, shot_id, candidate_id, user)
    try:
        candidate = await candidate_service.promote_candidate(
            session, candidate, user_id=user.id, is_admin=user.is_admin
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _candidate_out(candidate)


@router.post("/series/shots/{shot_id}/candidates/{candidate_id}/reject")
async def reject_shot_candidate(
    shot_id: str,
    candidate_id: str,
    body: CandidateRejectRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    candidate = await _owned_candidate(session, shot_id, candidate_id, user)
    try:
        candidate = await candidate_service.reject_candidate(
            session, candidate, reason=body.reason, user_id=user.id
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _candidate_out(candidate)


@router.post("/series/shots/{shot_id}/candidates/{candidate_id}/regenerate")
async def regenerate_shot_candidate(
    shot_id: str,
    candidate_id: str,
    body: CandidateRegenerateRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    candidate = await _owned_candidate(session, shot_id, candidate_id, user)
    try:
        job = await candidate_service.create_regeneration_job(
            session,
            candidate=candidate,
            reason=body.reason,
            instruction=body.instruction,
            preserve_fields=body.preserve_fields,
            replace_fields=body.replace_fields,
            requested_by=user.id,
            confirmed_quote_id=body.confirmed_quote_id,
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"job_id": job.id, "candidate_id": candidate.id, "status": job.status, "operation": job.operation}


@router.post("/series/shots/{shot_id}/candidates/{candidate_id}/regeneration-quote")
async def quote_shot_candidate_regeneration(
    shot_id: str,
    candidate_id: str,
    body: CandidateRegenerateRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    candidate = await _owned_candidate(session, shot_id, candidate_id, user)
    try:
        quote = await candidate_service.quote_regeneration(
            session,
            candidate=candidate,
            reason=body.reason,
            instruction=body.instruction,
            preserve_fields=body.preserve_fields,
            replace_fields=body.replace_fields,
            requested_by=user.id,
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "quote_id": quote.quote_id,
        "price": str(quote.quoted_cost or 0),
        "currency": quote.currency,
        "price_table_version": quote.price_table_version,
        "expires_at": quote.quote_expires_at.isoformat() if quote.quote_expires_at else None,
    }


@router.get("/series/episodes/{episode_id}/timeline-revisions")
async def list_timeline_revisions(
    episode_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await _owned_episode_project(session, episode_id, user)
    return [_timeline_out(row) for row in await timeline_service.list_revisions(session, episode_id)]


@router.post("/series/episodes/{episode_id}/timeline-revisions/plan")
async def plan_timeline_revision(
    episode_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _, project = await _owned_episode_project(session, episode_id, user)
    try:
        row = await timeline_service.plan_from_storyboard(
            session, project_id=project.id, episode_id=episode_id, created_by=user.id
        )
        await session.commit()
        await session.refresh(row)
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _timeline_out(row)


@router.post("/series/episodes/{episode_id}/timeline-revisions")
async def create_timeline_revision(
    episode_id: str,
    body: TimelineRevisionCreateRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _, project = await _owned_episode_project(session, episode_id, user)
    try:
        row = await timeline_service.create_revision(
            session,
            project_id=project.id,
            episode_id=episode_id,
            spec=body.spec,
            created_by=user.id,
            parent_revision_id=body.parent_revision_id,
            storyboard_revision_id=body.storyboard_revision_id,
        )
        await session.commit()
        await session.refresh(row)
    except PermissionError as e:
        raise _403(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _timeline_out(row)


@router.post("/series/episodes/{episode_id}/timeline-revisions/{revision_id}/approve")
async def approve_timeline_revision(
    episode_id: str,
    revision_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await _owned_episode_project(session, episode_id, user)
    row = await session.get(TimelineRevision, revision_id)
    if row is None or row.episode_id != episode_id:
        raise HTTPException(status_code=404, detail="Timeline revision 不存在")
    row = await timeline_service.approve_revision(session, row, user_id=user.id)
    await dependency_invalidation_service.apply_invalidation(
        session,
        project_id=row.project_id,
        source_type="timeline",
        source_id=row.id,
        changed_fields=["spec"],
        user_id=user.id,
        is_admin=user.is_admin,
        reason="timeline_approved",
        change_token=row.content_hash,
    )
    await _complete_episode_creative_steps(
        session, row.project_id, episode_id, "timeline.plan", {"timeline_revision_id": row.id}
    )
    await session.commit()
    return _timeline_out(row)


@router.post("/series/episodes/{episode_id}/qc")
async def run_episode_qc(
    episode_id: str,
    body: QCRunRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _, project = await _owned_episode_project(session, episode_id, user)
    if body.phase not in {"director_review", "final_qc"}:
        raise HTTPException(status_code=400, detail="phase must be director_review or final_qc")
    try:
        report = await publish_service.run_qc(
            session,
            project_id=project.id,
            episode_id=episode_id,
            render_artifact_id=body.render_artifact_id,
            phase=body.phase,
            created_by=user.id,
        )
        if report.status == "passed":
            node_type = "episode.director.review" if body.phase == "director_review" else "episode.final.qc"
            await _complete_episode_creative_steps(
                session, project.id, episode_id, node_type, {"qc_report_id": report.id}
            )
        await session.commit()
        await session.refresh(report)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _qc_out(report)


@router.get("/series/episodes/{episode_id}/qc")
async def list_episode_qc(
    episode_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await _owned_episode_project(session, episode_id, user)
    reports = (
        (
            await session.execute(
                select(QCReport).where(QCReport.episode_id == episode_id).order_by(QCReport.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_qc_out(row) for row in reports]


@router.post("/series/episodes/{episode_id}/publish-package")
async def create_episode_publish_package(
    episode_id: str,
    body: PublishPackageRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _, project = await _owned_episode_project(session, episode_id, user)
    try:
        publication, package = await publish_service.create_publish_package(
            session,
            project_id=project.id,
            episode_id=episode_id,
            platform=body.platform,
            title=body.title,
            description=body.description,
            tags=body.tags,
            created_by=user.id,
        )
        await _complete_episode_creative_steps(
            session,
            project.id,
            episode_id,
            "publish.package.generate",
            {"publication_id": publication.id, "publish_package_id": package.id},
        )
        await session.commit()
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _publication_out(publication, package)


@router.get("/series/projects/{project_id}/publications")
async def list_project_publications(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await _owned_project(session, project_id, user)
    return [_publication_out(row) for row in await publish_service.list_publications(session, project_id)]


@router.patch("/series/shots/{shot_id}")
async def update_shot(
    shot_id: str,
    body: ShotUpdateRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="没有可更新的字段")
    try:
        shot = await storyboard_service.update_shot(session, shot_id, fields, user.id)
    except PermissionError as e:
        raise _403(e)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "id": shot.id,
        "scene_id": shot.scene_id,
        "shot_no": shot.shot_no,
        "duration_ms": shot.duration_ms,
        "visual_prompt": shot.visual_prompt,
        "status": shot.status,
    }


# ─────────────────────────────────────────
# 资产
# ─────────────────────────────────────────


@router.post("/series/projects/{project_id}/assets", response_model=AssetCardOut)
async def create_asset(
    project_id: str,
    body: AssetCardCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    project = await _owned_project(session, project_id, user)
    return await asset_service.create_asset_card(
        session,
        project.id,
        user.id,
        type=body.type,
        name=body.name,
        description=body.description,
        provider_meta=body.provider_meta,
        tags=body.tags,
        scope=body.scope,
        source=body.source,
        profile_schema_version=body.profile_schema_version,
        profile=body.profile,
    )


@router.get("/series/projects/{project_id}/assets", response_model=list[AssetCardOut])
async def list_assets(
    project_id: str,
    type: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
    include_personal: bool = False,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """项目资产列表；include_personal=true 时合并返回当前用户的个人资产
    （scope=personal，供工作台"项目资产/个人资产"切换）。"""
    project = await _owned_project(session, project_id, user)
    cards = await asset_service.list_assets(session, project_id=project.id, type=type, tag=tag, q=q)
    if include_personal:
        cards += await asset_service.list_assets(
            session, user_id=user.id, scope="personal", type=type, tag=tag, q=q
        )
    return cards


class NewVersionRequest(BaseModel):
    updates: dict[str, Any] = Field(default_factory=dict)


@router.post("/series/assets/{asset_id}/versions", response_model=AssetCardOut)
async def new_asset_version(
    asset_id: str,
    body: NewVersionRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    asset = await asset_service.get_asset_for_user(session, asset_id, user.id)
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权访问")
    try:
        return await asset_service.new_asset_version(session, asset_id, user.id, body.updates)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class BindShotRequest(BaseModel):
    shot_id: str
    role: str = "character"
    reference_type: Optional[str] = None
    artifact_id: Optional[str] = None
    subject: str = ""
    weight: float = 1.0
    sort_order: int = 0
    usage: str = "identity"


@router.post("/series/assets/{asset_id}/bind-shot")
async def bind_asset_to_shot(
    asset_id: str,
    body: BindShotRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    asset = await asset_service.get_asset_for_user(session, asset_id, user.id)
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权访问")
    try:
        if (
            body.reference_type is None
            and body.artifact_id is None
            and body.subject == ""
            and body.weight == 1.0
            and body.sort_order == 0
            and body.usage == "identity"
        ):
            ref = await asset_service.bind_asset_to_shot(session, body.shot_id, asset_id, role=body.role)
        else:
            ref = await asset_service.bind_asset_to_shot(
                session,
                body.shot_id,
                asset_id,
                role=body.role,
                reference_type=body.reference_type,
                artifact_id=body.artifact_id,
                subject=body.subject,
                weight=body.weight,
                sort_order=body.sort_order,
                usage=body.usage,
            )
    except PermissionError as e:
        raise _403(e)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "id": ref.id,
        "shot_id": ref.shot_id,
        "asset_id": ref.asset_id,
        "asset_version": getattr(ref, "asset_version", getattr(asset, "version", 1)),
        "role": ref.role,
        "reference_type": getattr(ref, "reference_type", ref.role),
        "artifact_id": getattr(ref, "artifact_id", None),
        "subject": getattr(ref, "subject", ""),
        "weight": getattr(ref, "weight", 1.0),
        "sort_order": getattr(ref, "sort_order", 0),
        "usage": getattr(ref, "usage", "identity"),
    }


@router.get("/series/shots/{shot_id}/references")
async def list_shot_references(
    shot_id: str, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    from app.db.models import ShotAssetRef

    project_id = await asset_service._shot_project_id(session, shot_id)
    if project_id is None:
        raise HTTPException(status_code=404, detail="镜头不存在")
    await _owned_project(session, project_id, user)
    rows = (
        (
            await session.execute(
                select(ShotAssetRef)
                .where(ShotAssetRef.shot_id == shot_id)
                .order_by(ShotAssetRef.sort_order, ShotAssetRef.id)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": row.id,
            "shot_id": row.shot_id,
            "asset_id": row.asset_id,
            "asset_version": row.asset_version,
            "role": row.role,
            "reference_type": row.reference_type,
            "artifact_id": row.artifact_id,
            "subject": row.subject,
            "weight": row.weight,
            "sort_order": row.sort_order,
            "usage": row.usage,
        }
        for row in rows
    ]


# ─────────────────────────────────────────
# 审批 / 生成 Gate
# ─────────────────────────────────────────


class ApprovalRequest(BaseModel):
    target_type: str  # series_bible | style_bible | script | director_plan | storyboard | asset
    target_id: str
    target_revision: int
    action: str  # approve | reject
    comment: str = ""
    project_id: str


@router.post("/series/approvals", response_model=ApprovalOut)
async def submit_approval(
    body: ApprovalRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    project = await _owned_project(session, body.project_id, user)
    fn = {"approve": approval_service.approve, "reject": approval_service.reject}.get(body.action)
    if fn is None:
        raise HTTPException(status_code=400, detail="action 必须是 approve 或 reject")
    try:
        result = await fn(
            session,
            project_id=project.id,
            target_type=body.target_type,
            target_id=body.target_id,
            target_revision=body.target_revision,
            approved_by=user.id,
            comment=body.comment,
        )
    except PermissionError as e:
        raise _403(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 审批可能放行 waiting_approval 的 Gate 节点：对项目内未终态 run 触发重估
    active_runs = (
        (
            await session.execute(
                select(WorkflowRun).where(
                    WorkflowRun.project_id == project.id,
                    WorkflowRun.status.notin_({"succeeded", "failed", "canceled", "paused"}),
                )
            )
        )
        .scalars()
        .all()
    )
    for run in active_runs:
        await workflow_service.advance_run(session, run.id)
    return result


@router.get("/series/projects/{project_id}/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    project = await _owned_project(session, project_id, user)
    return await approval_service.list_approvals(session, project.id)


@router.get("/series/episodes/{episode_id}/generation-gate")
async def generation_gate(
    episode_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _, project = await _owned_episode_project(session, episode_id, user)
    ok, reasons = await approval_service.check_generation_gate(session, project.id, episode_id)
    return {"ok": ok, "reasons": reasons}


# ─────────────────────────────────────────
# Artifact 候选与反馈（P2-2）
# ─────────────────────────────────────────


async def _owned_shot_project(session: AsyncSession, shot_id: str, user: User):
    """shot → project 归属校验（不存在或无权 → 404）。"""
    project_id = await asset_service._shot_project_id(session, shot_id)
    if project_id is None:
        raise HTTPException(status_code=404, detail="镜头不存在")
    return await _owned_project(session, project_id, user)


@router.get("/series/shots/{shot_id}/artifacts")
async def list_shot_artifacts(
    shot_id: str,
    operation: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """该 shot 的生成产物候选列表（经 job.shot_id 反查，含 provider/model/url）。"""
    await _owned_shot_project(session, shot_id, user)
    stmt = (
        select(Artifact, GenerationJob)
        .join(GenerationJob, Artifact.job_id == GenerationJob.id)
        .where(GenerationJob.shot_id == shot_id)
        .order_by(Artifact.created_at.desc())
    )
    if operation:
        stmt = stmt.where(GenerationJob.operation == operation)
    rows = (await session.execute(stmt)).all()
    return [
        {
            "artifact_id": a.id,
            "type": a.type,
            "url": asset_service.artifact_url(a),
            "job_id": j.id,
            "operation": j.operation,
            "provider": j.provider,
            "model": j.model,
            "job_status": j.status,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a, j in rows
    ]


class FeedbackRequest(BaseModel):
    action: str  # selected | rejected | rated
    score: Optional[int] = None
    reason: str = ""
    step_id: Optional[str] = None


@router.post("/series/artifacts/{artifact_id}/feedback")
async def submit_feedback(
    artifact_id: str,
    body: FeedbackRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """写反馈；action=selected 且给 step_id 时同步更新 step.selected_artifact_id
    （简单语义：直接覆盖旧选中，历史经 feedback 记录追溯）。"""
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None or artifact.project_id is None:
        raise HTTPException(status_code=404, detail="产物不存在")
    await _owned_project(session, artifact.project_id, user)
    try:
        feedback = await feedback_service.record_feedback(
            session,
            artifact=artifact,
            user_id=user.id,
            action=body.action,
            score=body.score,
            reason=body.reason,
            step_id=body.step_id,
        )
    except PermissionError as e:
        raise _403(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "id": feedback.id,
        "artifact_id": feedback.artifact_id,
        "action": feedback.action,
        "score": feedback.score,
        "step_id": feedback.step_id,
    }


@router.get("/series/projects/{project_id}/feedback")
async def list_project_feedback(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    project = await _owned_project(session, project_id, user)
    rows = await feedback_service.list_feedback(session, project.id)
    return [
        {
            "id": f.id,
            "artifact_id": f.artifact_id,
            "step_id": f.step_id,
            "action": f.action,
            "score": f.score,
            "reason": f.reason,
            "user_id": f.user_id,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in rows
    ]


# ─────────────────────────────────────────
# 个人资产库（P2-3）
# ─────────────────────────────────────────


class CopyToProjectRequest(BaseModel):
    project_id: str


@router.post("/series/assets/{asset_id}/copy-to-project", response_model=AssetCardOut)
async def copy_asset_to_project(
    asset_id: str,
    body: CopyToProjectRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """把个人资产（scope=personal）复制进项目：新行 version=1、source=原资产 id，
    项目内版本演进独立于个人库（锁定版本语义见 service 注释）。"""
    await _owned_project(session, body.project_id, user)
    try:
        return await asset_service.copy_personal_asset_to_project(session, asset_id, body.project_id, user.id)
    except PermissionError as e:
        raise _403(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ─────────────────────────────────────────
# 音色复刻（R6.4：Cost Gate 安全版）
# ─────────────────────────────────────────


class CloneVoiceRequest(BaseModel):
    reference_audio_artifact_id: str
    voice_name: str = ""
    confirmed_quote_id: str = ""


class ReferenceAnalyzeRequest(BaseModel):
    artifact_id: str
    duration_ms: int = Field(default=0, ge=0, le=86_400_000)
    source_uri: str = ""
    authorization: str = "owned"
    usage_scope: str = "director_language_only"


class ReferenceRevisionRequest(BaseModel):
    segments: list[dict[str, Any]]


class ReferenceBindRequest(BaseModel):
    shot_id: str
    segment_key: str
    reference_type: str
    weight: float = 1
    replace_subject: bool = True


def _reference_analysis_payload(row: ReferenceAnalysis) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "artifact_id": row.artifact_id,
        "revision": row.revision,
        "parent_revision_id": row.parent_revision_id,
        "status": row.status,
        "duration_ms": row.duration_ms,
        "segments": row.segments,
        "confidence": row.confidence,
        "prompt": row.prompt,
        "model_invocation_id": row.model_invocation_id,
        "model_snapshot": row.model_snapshot,
        "usage_snapshot": row.usage_snapshot,
        "estimated_cost": str(row.estimated_cost or 0),
        "source_uri": row.source_uri,
        "authorization": row.authorization,
        "usage_scope": row.usage_scope,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.post("/series/projects/{project_id}/artifacts/upload-reference-video")
async def upload_reference_video(
    project_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    project = await _owned_project(session, project_id, user)
    original_name = Path(file.filename or "reference.mp4").name
    extension = Path(original_name).suffix.lower()
    if extension not in {".mp4", ".mov", ".webm", ".mkv", ".m4v"}:
        raise HTTPException(status_code=400, detail="仅支持 mp4/mov/webm/mkv/m4v 视频")
    content_type = (file.content_type or "").lower()
    if content_type and not content_type.startswith("video/") and content_type != "application/octet-stream":
        raise HTTPException(status_code=400, detail="上传文件不是视频类型")
    import uuid

    video_dir = asset_service.STORAGE_DIR / "reference_videos"
    video_dir.mkdir(parents=True, exist_ok=True)
    filename = f"reference_{uuid.uuid4().hex}{extension}"
    output_path = video_dir / filename
    total_bytes = 0
    try:
        with output_path.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > 200 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="参考视频不能超过 200MB")
                output.write(chunk)
        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="参考视频不能为空")
        detected_duration_ms = None
        scene_boundaries_ms: list[int] = []
        try:
            detected_duration_ms, scene_boundaries_ms = reference_analysis_service.probe_reference_video(
                str(output_path)
            )
        except Exception:
            pass
        artifact = await asset_service.create_artifact(
            session,
            project_id=project.id,
            type="video",
            storage_key=f"reference_videos/{filename}",
            mime_type=content_type or "video/mp4",
            duration_ms=detected_duration_ms,
            meta={
                "source": "reference_analysis_upload",
                "original_filename": original_name,
                "user_id": user.id,
                "scene_boundaries_ms": scene_boundaries_ms,
            },
        )
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    return {
        "artifact_id": artifact.id,
        "type": artifact.type,
        "filename": original_name,
        "size": total_bytes,
        "duration_ms": artifact.duration_ms,
        "url": asset_service.artifact_url(artifact),
    }


@router.post("/series/projects/{project_id}/reference-analyses")
async def analyze_reference_video(
    project_id: str,
    body: ReferenceAnalyzeRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await _owned_project(session, project_id, user)
    try:
        row = await reference_analysis_service.create_analysis(
            session,
            project_id=project_id,
            artifact_id=body.artifact_id,
            duration_ms=body.duration_ms,
            source_uri=body.source_uri,
            authorization=body.authorization,
            usage_scope=body.usage_scope,
            created_by=user.id,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return _reference_analysis_payload(row)


@router.get("/series/projects/{project_id}/reference-analyses")
async def list_reference_analyses(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await _owned_project(session, project_id, user)
    return [
        _reference_analysis_payload(row)
        for row in await reference_analysis_service.list_analyses(session, project_id)
    ]


@router.post("/series/reference-analyses/{analysis_id}/revisions")
async def revise_reference_analysis(
    analysis_id: str,
    body: ReferenceRevisionRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    row = await session.get(ReferenceAnalysis, analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="拉片分析不存在")
    await _owned_project(session, row.project_id, user)
    try:
        revised = await reference_analysis_service.revise_analysis(
            session, analysis_id, body.segments, created_by=user.id
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return _reference_analysis_payload(revised)


@router.post("/series/reference-analyses/{analysis_id}/bind")
async def bind_reference_segment(
    analysis_id: str,
    body: ReferenceBindRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    row = await session.get(ReferenceAnalysis, analysis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="拉片分析不存在")
    await _owned_project(session, row.project_id, user)
    try:
        ref = await reference_analysis_service.bind_segment(
            session,
            analysis_id=analysis_id,
            segment_key=body.segment_key,
            shot_id=body.shot_id,
            reference_type=body.reference_type,
            weight=body.weight,
            replace_subject=body.replace_subject,
            created_by=user.id,
        )
    except PermissionError as error:
        raise _403(error)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {
        "id": ref.id,
        "shot_id": ref.shot_id,
        "analysis_id": ref.analysis_id,
        "analysis_revision": ref.analysis_revision,
        "segment_key": ref.segment_key,
        "reference_type": ref.reference_type,
        "replace_subject": ref.replace_subject,
    }


class CloneVoiceQuote(BaseModel):
    quote_id: str
    provider: str
    price: str  # CNY
    currency: str = "CNY"
    expires_at: str
    reference_artifact_id: str
    voice_asset_id: str


def _clone_quote_token(quote_id: str) -> str:
    import hashlib
    import hmac
    import os

    secret = os.getenv("CLONE_QUOTE_SECRET", os.getenv("JWT_SECRET", "dev-secret-change-me"))
    signature = hmac.new(secret.encode(), quote_id.encode(), hashlib.sha256).hexdigest()
    return f"vcq:{quote_id}:{signature}"


def _parse_clone_quote_token(token: str) -> str:
    import hmac

    parts = token.split(":")
    if len(parts) != 3 or parts[0] != "vcq":
        raise HTTPException(status_code=400, detail="无效的 quote_id 格式")
    quote_id, signature = parts[1], parts[2]
    expected = _clone_quote_token(quote_id).rsplit(":", 1)[1]
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400, detail="quote 签名验证失败，请重新获取")
    return quote_id


@router.get("/series/projects/{project_id}/assets/{asset_id}/clone-voice-quote")
async def get_clone_voice_quote(
    project_id: str,
    asset_id: str,
    reference_audio_artifact_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """返回音色复刻报价（不创建 Job）。用户确认后带 quote_id 调用 clone-voice。"""
    project = await _owned_project(session, project_id, user)
    asset = await session.get(AssetCard, asset_id)
    if asset is None or asset.project_id != project_id:
        raise HTTPException(status_code=404, detail="voice 资产不存在")
    if asset.type != "voice":
        raise HTTPException(status_code=400, detail="仅 voice 类型资产支持音色复刻")

    from decimal import Decimal

    from app.model_runtime.media import get_media_runtime
    from app.providers.pricing import PRICE_TABLE_VERSION, VOICE_ENROLL_PRICE

    try:
        qwen, _ = get_media_runtime().resolve_speech(explicit_name="qwen_tts")
    except RuntimeError:
        raise HTTPException(status_code=503, detail="TTS Provider 不可用，无法报价")

    # R8.5：签名绑定业务字段，防止跨资产重放
    ref_artifact = await session.get(Artifact, reference_audio_artifact_id)
    if ref_artifact is None or ref_artifact.project_id != project_id:
        raise HTTPException(status_code=404, detail="参考音频产物不存在或不属于当前项目")
    if ref_artifact.type != "audio":
        raise HTTPException(status_code=400, detail="参考音频产物类型须为 audio")

    import datetime as _dt
    import uuid as _uuid

    price = VOICE_ENROLL_PRICE.get("qwen_tts", Decimal("2.00"))
    now = _dt.datetime.now(_dt.timezone.utc)
    expires_at = now + _dt.timedelta(minutes=30)
    quote = CloneVoiceQuoteRecord(
        id=str(_uuid.uuid4()),
        nonce=_uuid.uuid4().hex,
        user_id=user.id,
        project_id=project_id,
        asset_id=asset_id,
        asset_version=asset.version,
        reference_artifact_id=ref_artifact.id,
        provider=qwen.name,
        model="qwen-tts-voice-cloning",
        price=price,
        currency="CNY",
        price_table_version=PRICE_TABLE_VERSION,
        expires_at=expires_at,
    )
    session.add(quote)
    await session.commit()
    return {
        "quote_id": _clone_quote_token(quote.id),
        "provider": qwen.name,
        "price": str(price),
        "currency": "CNY",
        "price_table_version": PRICE_TABLE_VERSION,
        "expires_at": expires_at.isoformat(),
        "voice_asset_id": asset_id,
        "voice_asset_version": asset.version,
        "reference_artifact_id": ref_artifact.id,
        "project_id": project_id,
        "user_id": user.id,
        "nonce": quote.nonce,
    }


@router.post("/series/projects/{project_id}/artifacts/upload-audio")
async def upload_project_audio(
    project_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """上传项目级参考音频并创建受保护 Artifact，供 clone_voice 报价使用。"""
    project = await _owned_project(session, project_id, user)
    original_name = Path(file.filename or "reference.wav").name
    extension = Path(original_name).suffix.lower()
    allowed_extensions = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wma"}
    content_type = (file.content_type or "").lower()
    if extension not in allowed_extensions:
        raise HTTPException(status_code=400, detail="仅支持 wav/mp3/m4a/aac/ogg/flac/wma 音频")
    if content_type and not content_type.startswith("audio/") and content_type != "application/octet-stream":
        raise HTTPException(status_code=400, detail="上传文件不是音频类型")

    import uuid

    audio_dir = asset_service.STORAGE_DIR / "audios"
    audio_dir.mkdir(parents=True, exist_ok=True)
    filename = f"reference_{uuid.uuid4().hex}{extension}"
    output_path = audio_dir / filename
    max_bytes = 25 * 1024 * 1024
    total_bytes = 0
    try:
        with output_path.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise HTTPException(status_code=413, detail="参考音频不能超过 25MB")
                output.write(chunk)
        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="参考音频不能为空")
        artifact = await asset_service.create_artifact(
            session,
            project_id=project.id,
            type="audio",
            storage_key=f"audios/{filename}",
            mime_type=content_type or "audio/wav",
            meta={
                "source": "voice_reference_upload",
                "original_filename": original_name,
                "user_id": user.id,
            },
        )
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    return {
        "artifact_id": artifact.id,
        "type": artifact.type,
        "filename": original_name,
        "size": total_bytes,
        "url": asset_service.artifact_url(artifact),
    }


@router.post("/series/projects/{project_id}/assets/{asset_id}/clone-voice")
async def create_clone_voice_job(
    project_id: str,
    asset_id: str,
    body: CloneVoiceRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """创建 clone_voice Job（R6.4：必须确认报价后携带 quote_id）。"""
    project = await _owned_project(session, project_id, user)
    asset = await session.get(AssetCard, asset_id, with_for_update=True)
    if asset is None or asset.project_id != project_id:
        raise HTTPException(status_code=404, detail="voice 资产不存在")
    if asset.type != "voice":
        raise HTTPException(status_code=400, detail="仅 voice 类型资产支持音色复刻")

    if not body.confirmed_quote_id:
        raise HTTPException(
            status_code=400, detail="请先获取 clone-voice-quote 并确认费用后携带 confirmed_quote_id"
        )
    import datetime as _dt
    from decimal import Decimal

    from app.services import cost_service

    quote_id = _parse_clone_quote_token(body.confirmed_quote_id)
    quote = await session.get(CloneVoiceQuoteRecord, quote_id, with_for_update=True)
    if quote is None:
        raise HTTPException(status_code=400, detail="quote 不存在或已失效，请重新获取")
    expires_at = quote.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=_dt.timezone.utc)
    now = _dt.datetime.now(_dt.timezone.utc)
    if now > expires_at:
        raise HTTPException(status_code=400, detail="quote 已过期，请重新获取")
    if (
        quote.user_id != user.id
        or quote.project_id != project_id
        or quote.asset_id != asset_id
        or quote.asset_version != asset.version
        or quote.reference_artifact_id != body.reference_audio_artifact_id
    ):
        raise HTTPException(status_code=400, detail="quote 业务字段不匹配，请重新获取")
    if quote.consumed_at is not None:
        if quote.consumed_job_id:
            return {"job_id": quote.consumed_job_id, "status": "exists"}
        raise HTTPException(status_code=409, detail="quote 已消费，请重新获取")

    ref_artifact = await session.get(Artifact, body.reference_audio_artifact_id)
    if ref_artifact is None or ref_artifact.project_id != project_id:
        raise HTTPException(status_code=404, detail="参考音频产物不存在或不属于当前项目")
    if ref_artifact.type != "audio":
        raise HTTPException(status_code=400, detail="参考音频产物类型须为 audio")

    storage_key = ref_artifact.storage_key
    from app.services.asset_service import STORAGE_DIR

    storage_root = STORAGE_DIR.resolve()
    ref_path = (storage_root / storage_key).resolve()
    if not ref_path.is_relative_to(storage_root) or not ref_path.is_file():
        raise HTTPException(status_code=400, detail="参考音频路径非法或文件不存在")

    idem_key = f"clone_voice:{asset_id}:{asset.version}:{ref_artifact.id}"
    existing = await session.scalar(select(GenerationJob.id).where(GenerationJob.idempotency_key == idem_key))
    if existing:
        quote.consumed_at = now
        quote.consumed_job_id = existing
        await session.commit()
        return {"job_id": existing, "status": "exists"}

    price = Decimal(str(quote.price))
    quote_snapshot = {
        "quote_id": quote.id,
        "provider": quote.provider,
        "model": quote.model,
        "price": str(price),
        "currency": quote.currency,
        "price_table_version": quote.price_table_version,
        "expires_at": expires_at.isoformat(),
        "reference_artifact_id": quote.reference_artifact_id,
        "asset_id": quote.asset_id,
        "asset_version": quote.asset_version,
    }
    run = WorkflowRun(
        project_id=project_id,
        status="queued",
        trigger_type="api",
        input_snapshot={"operation": "clone_voice", "cost_confirmed": True, "cost_quote": quote_snapshot},
        estimated_cost=Decimal("0"),
        cost_limit=price,
        created_by=user.id,
    )
    session.add(run)
    await session.flush()
    step = WorkflowStep(
        run_id=run.id,
        node_key="asset.voice.clone",
        node_type="asset.voice.clone",
        entity_type="asset",
        entity_id=asset_id,
        status="queued",
        input_snapshot=quote_snapshot,
    )
    session.add(step)
    await session.flush()
    if not await cost_service.reserve_budget(session, run.id, price):
        await session.rollback()
        raise HTTPException(status_code=409, detail="clone_voice 预算预留失败")

    job = GenerationJob(
        project_id=project_id,
        operation="clone_voice",
        provider=quote.provider,
        model=quote.model,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        params={
            "asset_id": asset_id,
            "reference_audio_storage_key": storage_key,
            "reference_audio_artifact_id": ref_artifact.id,
            "voice_name": body.voice_name or asset.name,
            "confirmed_quote_id": body.confirmed_quote_id,
            "quote_snapshot": quote_snapshot,
            "estimated_cost": str(price),
            "currency": quote.currency,
            "price_table_version": quote.price_table_version,
            "step_id": step.id,
            "run_id": run.id,
        },
        idempotency_key=idem_key,
        status="pending",
        step_attempt=0,
        logical_job_key=f"clone_voice:{asset_id}",
    )
    session.add(job)
    await session.flush()
    quote.consumed_at = now
    quote.consumed_job_id = job.id
    session.add(
        UsageEvent(
            run_id=run.id,
            job_id=job.id,
            user_id=user.id,
            project_id=project_id,
            operation="clone_voice.reserve",
            provider=quote.provider,
            model=quote.model,
            input_units=1,
            output_units=0,
            unit_type="request",
            unit_price_snapshot=price,
            estimated_cost=price,
            actual_cost=None,
            cost_source="estimate",
            currency=quote.currency,
            status="reserved",
            event_metadata={"quote_id": quote.id, "price_table_version": quote.price_table_version},
        )
    )
    await session.commit()
    return {"job_id": job.id, "run_id": run.id, "status": "pending"}
