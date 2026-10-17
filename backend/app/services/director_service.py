"""DirectorPlan 版本链与 Observer 结构化复审。"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    DirectorPlanRevision,
    Episode,
    ModelInvocation,
    ObserverReview,
    ScriptRevision,
    StoryboardRevision,
)
from app.schemas.series import DirectorPlanPayload, ObserverFinding


async def create_plan(
    session: AsyncSession,
    episode_id: str,
    *,
    based_on_script_revision_id: str,
    payload: dict[str, Any],
    asset_version_snapshot: dict[str, Any],
    platform_constraints: dict[str, Any],
    source_type: str,
    created_by: str,
) -> DirectorPlanRevision:
    episode = await session.get(Episode, episode_id)
    script = await session.get(ScriptRevision, based_on_script_revision_id)
    if episode is None or script is None or script.episode_id != episode_id:
        raise ValueError("episode 或 based_on_script_revision 不存在/不匹配")
    validated = DirectorPlanPayload.model_validate(payload).model_dump(mode="json")
    revision = (
        await session.scalar(
            select(func.max(DirectorPlanRevision.revision)).where(
                DirectorPlanRevision.episode_id == episode_id
            )
        )
        or 0
    ) + 1
    row = DirectorPlanRevision(
        episode_id=episode_id,
        revision=revision,
        based_on_script_revision_id=script.id,
        series_bible_revision_id=episode.series_bible_revision_id,
        style_bible_revision_id=episode.style_bible_revision_id,
        asset_version_snapshot=asset_version_snapshot,
        platform_constraints=platform_constraints,
        payload=validated,
        source_type=source_type,
        created_by=created_by,
    )
    session.add(row)
    await session.flush()
    return row


async def list_plans(session: AsyncSession, episode_id: str) -> list[DirectorPlanRevision]:
    return list(
        (
            await session.execute(
                select(DirectorPlanRevision)
                .where(DirectorPlanRevision.episode_id == episode_id)
                .order_by(DirectorPlanRevision.revision.desc())
            )
        )
        .scalars()
        .all()
    )


def deterministic_script_findings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for field in ("episode_objective", "hook", "conflict", "cliffhanger"):
        if not str(payload.get(field) or "").strip():
            findings.append(
                {
                    "code": f"script.missing_{field}",
                    "category": "story",
                    "severity": "critical",
                    "evidence": f"{field} 为空",
                    "location": {"field": field},
                    "suggested_fix": f"补充 {field}",
                }
            )
    scenes = payload.get("scenes") or []
    if len(scenes) > 20:
        findings.append(
            {
                "code": "script.scene_load",
                "category": "shootability",
                "severity": "major",
                "evidence": f"共 {len(scenes)} 场",
                "location": {},
                "suggested_fix": "合并场景或拆分集数",
            }
        )
    for scene in scenes:
        if not str(scene.get("objective") or "").strip():
            findings.append(
                {
                    "code": "script.scene_objective",
                    "category": "motivation",
                    "severity": "warning",
                    "evidence": "场景缺少目标",
                    "location": {"scene_number": scene.get("number")},
                    "suggested_fix": "明确本场人物目标和阻力",
                }
            )
    return findings


def deterministic_continuity_findings(payload: dict[str, Any], intent_keys: set[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    required = {"direction", "location", "costume", "action", "eyeline", "lighting"}
    for episode in payload.get("episodes") or []:
        for scene in episode.get("scenes") or []:
            for shot in scene.get("shots") or []:
                location = {"scene_number": scene.get("number"), "shot_number": shot.get("number")}
                intent_key = str(shot.get("director_intent_key") or "")
                if not intent_key or intent_key not in intent_keys:
                    findings.append(
                        {
                            "code": "continuity.director_intent",
                            "category": "directing",
                            "severity": "major",
                            "evidence": f"导演意图 {intent_key or '未填写'} 无法追溯",
                            "location": location,
                            "suggested_fix": "绑定当前 DirectorPlan 中的 intent key",
                        }
                    )
                constraints = shot.get("continuity_constraints") or {}
                missing = sorted(required - set(constraints))
                if missing:
                    findings.append(
                        {
                            "code": "continuity.constraints_missing",
                            "category": "continuity",
                            "severity": "warning",
                            "evidence": f"缺少约束: {', '.join(missing)}",
                            "location": location,
                            "suggested_fix": "补充人物方向、位置、服装、动作、视线和光线约束",
                        }
                    )
    return findings


async def create_review(
    session: AsyncSession,
    episode: Episode,
    *,
    review_type: str,
    target_type: str,
    target_id: str,
    target_revision: int,
    score: float,
    deterministic_findings: list[dict[str, Any]],
    specialist_findings: list[dict[str, Any]],
    llm_findings: list[dict[str, Any]],
    summary: str,
    model_invocation_id: Optional[str],
    review_scope: Optional[dict[str, Any]],
    created_by: str,
) -> ObserverReview:
    if target_type == "script":
        target = await session.get(ScriptRevision, target_id)
    elif target_type == "storyboard":
        target = await session.get(StoryboardRevision, target_id)
    else:
        raise ValueError("Observer target_type 仅支持 script 或 storyboard")
    if target is None or target.episode_id != episode.id or target.revision != target_revision:
        raise ValueError("reviewed revision 不存在或与 episode 不匹配")
    if model_invocation_id:
        invocation = await session.get(ModelInvocation, model_invocation_id)
        if invocation is None or invocation.project_id != episode.project_id:
            raise ValueError("model invocation 不存在或不属于当前项目")
    groups = [deterministic_findings, specialist_findings, llm_findings]
    normalized = [
        [ObserverFinding.model_validate(item).model_dump(mode="json") for item in group] for group in groups
    ]
    blocking = any(item["severity"] in {"major", "critical"} for group in normalized for item in group)
    row = ObserverReview(
        project_id=episode.project_id,
        episode_id=episode.id,
        review_type=review_type,
        target_type=target_type,
        target_id=target_id,
        target_revision=target_revision,
        score=score,
        blocking=blocking,
        deterministic_findings=normalized[0],
        specialist_findings=normalized[1],
        llm_findings=normalized[2],
        review_scope=review_scope or {},
        summary=summary,
        model_invocation_id=model_invocation_id,
        created_by=created_by,
    )
    session.add(row)
    await session.flush()
    return row


async def list_reviews(
    session: AsyncSession, episode_id: str, review_type: Optional[str] = None
) -> list[ObserverReview]:
    stmt = select(ObserverReview).where(ObserverReview.episode_id == episode_id)
    if review_type:
        stmt = stmt.where(ObserverReview.review_type == review_type)
    return list((await session.execute(stmt.order_by(ObserverReview.created_at.desc()))).scalars().all())
