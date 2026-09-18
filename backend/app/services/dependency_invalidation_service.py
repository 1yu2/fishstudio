"""按内容差异计算并应用最小下游失效传播。"""

import hashlib
import json
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Approval,
    Artifact,
    AssetCard,
    DirectorPlanRevision,
    Episode,
    GenerationJob,
    InvalidationEvent,
    Publication,
    Scene,
    ScriptRevision,
    SeriesProject,
    Shot,
    ShotAssetRef,
    StoryboardRevision,
    TimelineRevision,
    WorkflowRun,
    WorkflowStep,
)
from app.services.ownership import check_project_access

VISUAL_FIELDS = {
    "shot_type", "narrative_purpose", "composition", "action_start", "action_end",
    "emotion", "first_frame_prompt", "last_frame_prompt", "generation_mode",
    "continuity_constraints", "risk_tags", "locked_asset_versions", "visual_prompt",
    "camera", "aspect_ratio",
}
DIALOGUE_FIELDS = {"dialogue_tracks", "narration_tracks"}
SUBTITLE_FIELDS = {"subtitle_tracks"}
DURATION_FIELDS = {"duration_ms"}

OPERATION_TO_STAGE = {
    "generate_image": "image",
    "generate_video": "video",
    "tts": "audio",
    "render_episode": "render",
}
STAGE_TO_NODE = {
    "script_review": "episode.script.review",
    "director_plan": "director.plan.generate",
    "director_approval": "director.plan.approve",
    "continuity_review": "storyboard.continuity.check",
    "image": "shot.image.generate",
    "video": "shot.video.generate",
    "audio": "shot.audio.generate",
    "timeline": "timeline.plan",
    "render": "episode.render.final",
    "publication": "publish.package.generate",
}


def stages_for_change(source_type: str, changed_fields: list[str]) -> set[str]:
    """纯规则映射，供 dry-run、执行和测试共用。"""
    fields = set(changed_fields)
    if source_type in {"series_bible", "style_bible", "script"}:
        stages = {"script_review", "director_plan", "director_approval", "storyboard", "continuity_review", "image", "video", "audio", "timeline", "render", "publication"}
        if source_type in {"series_bible", "style_bible"}:
            stages.add("script")
        return stages
    if source_type == "asset":
        return {"image", "video", "timeline", "render", "publication"}
    if source_type == "publication":
        return {"publication"}
    if source_type == "timeline":
        return {"render", "publication"}
    if source_type != "shot":
        raise ValueError(f"unsupported invalidation source_type: {source_type}")
    stages: set[str] = set()
    if "selected_image_artifact_id" in fields:
        stages.update({"video", "timeline", "render", "publication"})
    if "selected_video_artifact_id" in fields:
        stages.update({"timeline", "render", "publication"})
    if fields & VISUAL_FIELDS:
        stages.update({"image", "video", "timeline", "render", "publication"})
    if fields & DURATION_FIELDS:
        stages.update({"video", "audio", "subtitle", "timeline", "render", "publication"})
    if fields & DIALOGUE_FIELDS:
        stages.update({"audio", "subtitle", "timeline", "render", "publication"})
    if fields & SUBTITLE_FIELDS:
        stages.update({"subtitle", "timeline", "render", "publication"})
    if fields & {"transition"}:
        stages.update({"timeline", "render", "publication"})
    return stages


def make_idempotency_key(
    project_id: str,
    source_type: str,
    source_id: str,
    source_revision: Optional[int],
    changed_fields: list[str],
    change_token: str = "",
) -> str:
    body = json.dumps(
        [project_id, source_type, source_id, source_revision, sorted(changed_fields), change_token],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"invalidate:{hashlib.sha256(body.encode()).hexdigest()}"


async def _resolve_scope(
    session: AsyncSession, project_id: str, source_type: str, source_id: str
) -> tuple[list[str], list[str]]:
    """返回 (episode_ids, shot_ids)，并验证 source 属于项目。"""
    if source_type in {"series_bible", "style_bible", "publication"}:
        if source_id != project_id:
            raise PermissionError("source belongs to another project")
        episodes = (
            await session.execute(select(Episode.id).where(Episode.project_id == project_id))
        ).scalars().all()
        episode_ids = list(episodes)
        shots = (
            await session.execute(
                select(Shot.id).join(Scene, Shot.scene_id == Scene.id).where(
                    Scene.episode_id.in_(episode_ids)
                )
            )
        ).scalars().all() if episode_ids else []
        return episode_ids, list(shots)
    if source_type == "script":
        episode = await session.get(Episode, source_id)
        if episode is None:
            raise ValueError(f"episode {source_id} not found")
        if episode.project_id != project_id:
            raise PermissionError("source belongs to another project")
        shots = (
            await session.execute(
                select(Shot.id).join(Scene, Shot.scene_id == Scene.id).where(
                    Scene.episode_id == episode.id
                )
            )
        ).scalars().all()
        return [episode.id], list(shots)
    if source_type == "timeline":
        timeline = await session.get(TimelineRevision, source_id)
        if timeline is None or timeline.project_id != project_id:
            raise ValueError(f"timeline {source_id} not found")
        shots = (await session.execute(
            select(Shot.id).join(Scene, Shot.scene_id == Scene.id).where(
                Scene.episode_id == timeline.episode_id
            )
        )).scalars().all()
        return [timeline.episode_id], list(shots)
    if source_type == "shot":
        shot = await session.get(Shot, source_id)
        scene = await session.get(Scene, shot.scene_id) if shot else None
        episode = await session.get(Episode, scene.episode_id) if scene else None
        if shot is None or episode is None:
            raise ValueError(f"shot {source_id} not found")
        if episode.project_id != project_id:
            raise PermissionError("source belongs to another project")
        return [episode.id], [shot.id]
    if source_type == "asset":
        asset = await session.get(AssetCard, source_id)
        if asset is None:
            raise ValueError(f"asset {source_id} not found")
        if asset.project_id != project_id:
            raise PermissionError("source belongs to another project")
        refs = (
            await session.execute(
                select(ShotAssetRef.shot_id).where(ShotAssetRef.asset_id == source_id)
            )
        ).scalars().all()
        if not refs:
            return [], []
        rows = (
            await session.execute(
                select(Episode.id, Shot.id)
                .join(Scene, Episode.id == Scene.episode_id)
                .join(Shot, Scene.id == Shot.scene_id)
                .where(Shot.id.in_(refs), Episode.project_id == project_id)
            )
        ).all()
        if len(rows) != len(set(refs)):
            raise PermissionError("source belongs to another project")
        return sorted({row[0] for row in rows}), [row[1] for row in rows]
    raise ValueError(f"unsupported invalidation source_type: {source_type}")


def _impact(entity_type: str, entity_id: str, stage: str, reason: str) -> dict[str, str]:
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "stage": stage,
        "action": "mark_stale",
        "reason": reason,
    }


async def preview_invalidation(
    session: AsyncSession,
    *,
    project_id: str,
    source_type: str,
    source_id: str,
    changed_fields: list[str],
    user_id: Optional[int] = None,
    is_admin: bool = False,
    reason: str = "content_changed",
) -> dict[str, Any]:
    project = await session.get(SeriesProject, project_id)
    if project is None:
        raise ValueError(f"project {project_id} not found")
    check_project_access(project, user_id=user_id, is_admin=is_admin)
    stages = stages_for_change(source_type, changed_fields)
    episode_ids, shot_ids = await _resolve_scope(session, project_id, source_type, source_id)
    impacts: list[dict[str, str]] = []

    if "script" in stages and episode_ids:
        scripts = (
            await session.execute(
                select(ScriptRevision).where(
                    ScriptRevision.episode_id.in_(episode_ids),
                    ScriptRevision.status.notin_({"superseded", "stale"}),
                )
            )
        ).scalars().all()
        impacts.extend(_impact("script", row.id, "script", reason) for row in scripts)

    if "storyboard" in stages and episode_ids:
        revisions = (
            await session.execute(
                select(StoryboardRevision).where(
                    StoryboardRevision.episode_id.in_(episode_ids),
                    StoryboardRevision.status.notin_({"superseded", "stale"}),
                )
            )
        ).scalars().all()
        impacts.extend(_impact("storyboard", row.id, "storyboard", reason) for row in revisions)

    if "director_plan" in stages and episode_ids:
        plans = (
            await session.execute(
                select(DirectorPlanRevision).where(
                    DirectorPlanRevision.episode_id.in_(episode_ids),
                    DirectorPlanRevision.status.notin_({"superseded", "stale"}),
                )
            )
        ).scalars().all()
        impacts.extend(_impact("director_plan", row.id, "director_plan", reason) for row in plans)

    if "timeline" in stages and episode_ids:
        timelines = (await session.execute(select(TimelineRevision).where(
            TimelineRevision.episode_id.in_(episode_ids),
            TimelineRevision.status.notin_({"stale", "rejected"}),
        ))).scalars().all()
        impacts.extend(_impact("timeline", row.id, "timeline", reason) for row in timelines)

    job_filters = []
    wanted_operations = {
        operation for operation, stage in OPERATION_TO_STAGE.items() if stage in stages
    }
    if wanted_operations and shot_ids:
        job_filters.append(
            (GenerationJob.shot_id.in_(shot_ids))
            & (GenerationJob.operation.in_(wanted_operations - {"render_episode"}))
        )
    if "render" in stages and episode_ids:
        render_steps = select(WorkflowStep.id).where(
            WorkflowStep.entity_id.in_(episode_ids),
            WorkflowStep.node_type.in_({"episode.render", "episode.render.final"}),
        )
        job_filters.append(
            (GenerationJob.workflow_step_id.in_(render_steps))
            & (GenerationJob.operation == "render_episode")
        )
    jobs = []
    if job_filters:
        from sqlalchemy import or_

        jobs = list(
            (
                await session.execute(
                    select(GenerationJob).where(
                        GenerationJob.project_id == project_id,
                        or_(*job_filters),
                    )
                )
            ).scalars().all()
        )
    for job in jobs:
        stage = OPERATION_TO_STAGE.get(job.operation, "unknown")
        impacts.append(_impact("job", job.id, stage, reason))
    if jobs:
        artifacts = (
            await session.execute(select(Artifact).where(Artifact.job_id.in_([j.id for j in jobs])))
        ).scalars().all()
        job_stage = {job.id: OPERATION_TO_STAGE.get(job.operation, "unknown") for job in jobs}
        impacts.extend(
            _impact("artifact", artifact.id, job_stage.get(artifact.job_id, "unknown"), reason)
            for artifact in artifacts
        )

    if "publication" in stages and episode_ids:
        publications = (await session.execute(select(Publication).where(
            Publication.episode_id.in_(episode_ids), Publication.status != "stale"
        ))).scalars().all()
        impacts.extend(_impact("publication", row.id, "publication", reason) for row in publications)

    node_types = {STAGE_TO_NODE[stage] for stage in stages if stage in STAGE_TO_NODE}
    if "render" in stages:
        node_types.add("episode.render")  # legacy workflow templates
    if node_types and episode_ids:
        steps = (
            await session.execute(
                select(WorkflowStep)
                .join(WorkflowRun, WorkflowStep.run_id == WorkflowRun.id)
                .where(
                    WorkflowRun.project_id == project_id,
                    WorkflowStep.entity_id.in_(episode_ids),
                    WorkflowStep.node_type.in_(node_types),
                )
            )
        ).scalars().all()
        impacts.extend(
            _impact("workflow_step", step.id, next(
                (stage for stage, node in STAGE_TO_NODE.items() if node == step.node_type),
                "unknown",
            ), reason)
            for step in steps
        )
    # 去重并稳定排序，保证 dry-run 与 apply 可比较。
    unique = {
        (item["entity_type"], item["entity_id"], item["stage"]): item for item in impacts
    }
    ordered = sorted(unique.values(), key=lambda item: (
        item["stage"], item["entity_type"], item["entity_id"]
    ))
    return {
        "project_id": project_id,
        "source_type": source_type,
        "source_id": source_id,
        "changed_fields": sorted(set(changed_fields)),
        "stages": sorted(stages),
        "affected": ordered,
        "affected_count": len(ordered),
    }


async def apply_invalidation(
    session: AsyncSession,
    *,
    project_id: str,
    source_type: str,
    source_id: str,
    changed_fields: list[str],
    user_id: Optional[int] = None,
    is_admin: bool = False,
    source_revision: Optional[int] = None,
    reason: str = "content_changed",
    change_token: str = "",
) -> InvalidationEvent:
    key = make_idempotency_key(
        project_id, source_type, source_id, source_revision, changed_fields, change_token
    )
    existing = await session.scalar(
        select(InvalidationEvent).where(InvalidationEvent.idempotency_key == key)
    )
    if existing is not None:
        return existing
    preview = await preview_invalidation(
        session,
        project_id=project_id,
        source_type=source_type,
        source_id=source_id,
        changed_fields=changed_fields,
        user_id=user_id,
        is_admin=is_admin,
        reason=reason,
    )
    affected = preview["affected"]
    for item in affected:
        entity_type, entity_id = item["entity_type"], item["entity_id"]
        if entity_type == "storyboard":
            row = await session.get(StoryboardRevision, entity_id)
            if row is not None:
                row.status = "stale"
        elif entity_type == "script":
            row = await session.get(ScriptRevision, entity_id)
            if row is not None:
                row.status = "stale"
        elif entity_type == "director_plan":
            row = await session.get(DirectorPlanRevision, entity_id)
            if row is not None:
                row.status = "stale"
        elif entity_type == "timeline":
            row = await session.get(TimelineRevision, entity_id)
            if row is not None:
                row.status = "stale"
        elif entity_type == "publication":
            row = await session.get(Publication, entity_id)
            if row is not None:
                row.status = "stale"
        elif entity_type == "artifact":
            row = await session.get(Artifact, entity_id)
            if row is not None:
                row.artifact_metadata = {
                    **(row.artifact_metadata or {}),
                    "stale": True,
                    "stale_reason": reason,
                    "invalidation_key": key,
                }
        elif entity_type == "job":
            row = await session.get(GenerationJob, entity_id)
            if row is not None:
                row.result = {**(row.result or {}), "stale": True, "invalidation_key": key}
        elif entity_type == "workflow_step":
            row = await session.get(WorkflowStep, entity_id)
            if row is not None and row.status not in {"running", "queued"}:
                row.status = "draft"
                row.attempt += 1
                row.output_snapshot = {}
                row.selected_artifact_id = None
                row.error_code = ""
                row.error = ""
                run = await session.get(WorkflowRun, row.run_id)
                if run is not None and run.status in {"succeeded", "failed"}:
                    run.status = "ready"
                    run.finished_at = None

    episode_ids, shot_ids = await _resolve_scope(session, project_id, source_type, source_id)
    stages = set(preview["stages"])
    if source_type == "shot":
        shot = await session.get(Shot, source_id)
        if shot is not None:
            if not set(changed_fields) <= {"selected_image_artifact_id", "selected_video_artifact_id"}:
                shot.status = "stale"
            if "image" in stages and "selected_image_artifact_id" not in changed_fields:
                shot.selected_image_artifact_id = None
            if "video" in stages and "selected_video_artifact_id" not in changed_fields:
                shot.selected_video_artifact_id = None
    if episode_ids and source_type in {"shot", "script", "series_bible", "style_bible"}:
        approval_types = {"storyboard", "director_plan"}
        if source_type in {"series_bible", "style_bible"}:
            approval_types.add("script")
        approvals = (
            await session.execute(
                select(Approval).where(
                    Approval.target_type.in_(approval_types),
                    Approval.target_id.in_(episode_ids),
                    Approval.status != "superseded",
                )
            )
        ).scalars().all()
        for approval in approvals:
            approval.status = "superseded"

    event = InvalidationEvent(
        project_id=project_id,
        idempotency_key=key,
        source_type=source_type,
        source_id=source_id,
        source_revision=source_revision,
        changed_fields=preview["changed_fields"],
        reason=reason,
        affected_entities=affected,
        created_by=user_id,
    )
    session.add(event)
    await session.flush()
    return event
