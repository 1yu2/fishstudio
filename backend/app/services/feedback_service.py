"""Artifact 反馈闭环（review P2-2 后端部分）。

- record_feedback：写 ArtifactFeedback；action=selected 时同步写选中：
  step_id 非空 → WorkflowStep.selected_artifact_id（episode 级 render 选择）；
  artifact 来自某 shot 的 job → 按类型写 Shot 的图片/视频独立选择槽。
  step_id 会做强校验：必须属于 artifact 所在项目，且 artifact 是该 step
  关联 job 的产物（review 0.3，防跨项目写入）。
- aggregate_provider_scores：按 provider/model 离线聚合平均评分与选择率。
  **只做离线聚合**，不做在线路由学习——VideoProviderRouter 不读这里，
  质量分用于报表/人工调参，在线学习留待后续迭代。
"""

import logging
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Artifact,
    ArtifactFeedback,
    GenerationJob,
    Shot,
    WorkflowRun,
    WorkflowStep,
)

logger = logging.getLogger(__name__)

VALID_ACTIONS = ("selected", "rejected", "rated")


async def record_feedback(
    session: AsyncSession,
    *,
    artifact: Artifact,
    user_id: Optional[int],
    action: str,
    score: Optional[int] = None,
    reason: str = "",
    step_id: Optional[str] = None,
) -> ArtifactFeedback:
    """写一条反馈；selected 时同步写选中（step 级 + shot 级）。

    归属校验（review 0.3：step_id 跨项目写入）：
    - step 所属 run 的 project_id 必须等于 artifact.project_id；
    - artifact 必须由该 step 关联的 GenerationJob 产生
      （job.workflow_step_id == step_id 且 artifact.job_id == job.id）。

    选中语义（简单版）：直接覆盖同类型的已有选择（覆盖行为本身也留下一条
    selected 反馈，历史可追溯；不单独引入 superseded 反馈类型）。
    两级选择：WorkflowStep.selected_artifact_id（episode 级 render 用）保留；
    artifact 来自某 shot 的 job 时，图片写 selected_image_artifact_id，视频写
    selected_video_artifact_id，两个槽位互不覆盖。
    step_id 可空：前端按 shot 选择时仅写 Shot 级（要求 job.shot_id 非空且
    job.project_id == artifact.project_id，即该 shot 属于本项目）。
    """
    if action not in VALID_ACTIONS:
        raise ValueError(f"非法 action: {action}（可选 {VALID_ACTIONS}）")
    if artifact.project_id is None:
        raise ValueError("artifact 无 project_id，无法归属反馈")

    job: Optional[GenerationJob] = None
    if artifact.job_id:
        job = await session.get(GenerationJob, artifact.job_id)

    step: Optional[WorkflowStep] = None
    if step_id:
        step = await session.get(WorkflowStep, step_id)
        if step is None:
            raise ValueError(f"step {step_id} not found")
        run = await session.get(WorkflowRun, step.run_id)
        if run is None or run.project_id != artifact.project_id:
            raise PermissionError("step 不属于该 artifact 所在项目")
        if job is None or job.workflow_step_id != step_id:
            raise PermissionError("artifact 不是该 step 关联 job 的产物")

    feedback = ArtifactFeedback(
        project_id=artifact.project_id,
        step_id=step_id,
        artifact_id=artifact.id,
        user_id=user_id,
        action=action,
        score=score,
        reason=reason,
    )
    session.add(feedback)
    if action == "selected":
        if step is not None:
            step.selected_artifact_id = artifact.id
        if job is not None and job.shot_id and job.project_id == artifact.project_id:
            shot = await session.get(Shot, job.shot_id)
            if shot is not None:
                # 复审 0.8：按产物类型写入独立选择槽，图片/视频不互相覆盖
                if artifact.type == "image":
                    shot.selected_image_artifact_id = artifact.id
                elif artifact.type == "video":
                    shot.selected_video_artifact_id = artifact.id
    await session.commit()
    await session.refresh(feedback)
    return feedback


async def list_feedback(session: AsyncSession, project_id: str, limit: int = 200) -> list[ArtifactFeedback]:
    stmt = (
        select(ArtifactFeedback)
        .where(ArtifactFeedback.project_id == project_id)
        .order_by(ArtifactFeedback.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def aggregate_provider_scores(session: AsyncSession, project_id: Optional[str] = None) -> list[dict]:
    """按 provider/model 聚合：平均 score（rated）、选择率（selected/总反馈数）。

    离线报表用途；不做在线路由学习（见模块 docstring）。
    """
    stmt = (
        select(
            GenerationJob.provider,
            GenerationJob.model,
            func.avg(ArtifactFeedback.score).label("avg_score"),
            func.count().label("total"),
            func.sum(case((ArtifactFeedback.action == "selected", 1), else_=0)).label("selected"),
        )
        .join(Artifact, ArtifactFeedback.artifact_id == Artifact.id)
        .join(GenerationJob, Artifact.job_id == GenerationJob.id)
        .group_by(GenerationJob.provider, GenerationJob.model)
    )
    if project_id:
        stmt = stmt.where(ArtifactFeedback.project_id == project_id)
    rows = (await session.execute(stmt)).all()
    return [
        {
            "provider": r.provider or "",
            "model": r.model or "",
            "avg_score": float(r.avg_score) if r.avg_score is not None else None,
            "feedback_count": int(r.total),
            "selected_count": int(r.selected or 0),
            "selection_rate": (float(r.selected or 0) / float(r.total)) if r.total else 0.0,
        }
        for r in rows
    ]
