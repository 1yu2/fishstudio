"""分镜服务：StoryboardRevision 保存 / 物化 Scene/Shot / 旧文本导入。

关键设计取舍：
- 物化策略：payload 中带 scene_id/shot_id 的行更新既有 ORM 行，不带则新建；
  新建/更新的 Shot 绑定到新 revision（storyboard_revision_id）。
- 删除策略（简单可靠方案）：payload 中消失、且仍挂在该 episode 下的 shot，
  仅当 shot.status == "pending"（从未进入生成流程）时物理删除；
  其余（已开始生成/已产出）保留并继续指向旧 revision，避免误删在制品。
- 每次 save 产生新 revision（max+1），旧 revision 与该 episode 的 storyboard
  类 Approval 全部置 superseded（修改即失效旧审批）。
"""

import json
import logging
import re
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Approval,
    DirectorPlanRevision,
    Episode,
    Scene,
    ScriptRevision,
    SeriesProject,
    Shot,
    StoryboardRevision,
)
from app.schemas.series import StoryboardPayload
from app.services.ownership import check_project_access

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────
# 旧聊天文本分镜解析（纯函数，与 frontend/src/components/storyboard.ts 对齐）
# ─────────────────────────────────────────

# 镜头1（3秒）【特写】：描述
_SHOT_RE = re.compile(
    r"^\s*(?:[-*]\s*)?镜头\s*(\d+)\s*(?:[（(]([^）)]+)[）)])?\s*(?:【([^】]+)】)?\s*[：:]\s*(.+?)\s*$"
)
_STYLE_RE = re.compile(r"^\s*整体风格\s*[：:]\s*(.+?)\s*$")
_TONE_RE = re.compile(r"^\s*色调\s*[：:]\s*(.+?)\s*$")
_DURATION_RE = re.compile(r"(\d+)\s*(?:秒|s|S)?")


def _parse_duration_ms(text: Optional[str]) -> int:
    if not text:
        return 5000
    m = _DURATION_RE.search(text)
    if not m:
        return 5000
    return max(1, int(m.group(1))) * 1000


def parse_legacy_storyboard_text(text: str, series_id: str = "") -> dict[str, Any]:
    """把旧聊天文本分镜解析成 StoryboardPayload dict（单场景承载全部镜头）。

    支持行格式：`镜头N（时长）【标签】：描述`，以及 `整体风格：` / `色调：` 行。
    无镜头行时抛 ValueError。
    """
    shots: list[dict[str, Any]] = []
    style = ""
    tone = ""
    for line in (text or "").splitlines():
        m = _SHOT_RE.match(line)
        if m:
            number, duration_text, tag, description = m.groups()
            shots.append(
                {
                    "number": int(number),
                    "duration_ms": _parse_duration_ms(duration_text),
                    "visual_prompt": description.strip(),
                    "camera": {"type": "fixed", "description": (tag or "").strip()},
                }
            )
            continue
        m = _STYLE_RE.match(line)
        if m:
            style = m.group(1).strip()
            continue
        m = _TONE_RE.match(line)
        if m:
            tone = m.group(1).strip()

    if not shots:
        raise ValueError("文本中没有可解析的镜头行")

    desc_parts = []
    if style:
        desc_parts.append(f"整体风格：{style}")
    if tone:
        desc_parts.append(f"色调：{tone}")

    payload = {
        "schema_version": 1,
        "series_id": series_id,
        "episodes": [
            {
                "number": 1,
                "scenes": [
                    {
                        "number": 1,
                        "description": "；".join(desc_parts),
                        "shots": shots,
                    }
                ],
            }
        ],
    }
    # 复用 pydantic 校验（时长边界、number 唯一等）
    StoryboardPayload.model_validate(payload)
    return payload


# ─────────────────────────────────────────
# revision 保存 / 查询
# ─────────────────────────────────────────


async def _next_revision(session: AsyncSession, episode_id: str) -> int:
    stmt = select(func.max(StoryboardRevision.revision)).where(StoryboardRevision.episode_id == episode_id)
    current = (await session.execute(stmt)).scalar_one_or_none()
    return (current or 0) + 1


async def _supersede_storyboard_approvals(session: AsyncSession, episode_id: str) -> None:
    """该 episode 的 storyboard 审批和已批准状态全部失效。"""
    stmt = select(Approval).where(
        Approval.target_type == "storyboard",
        Approval.target_id == episode_id,
        Approval.status != "superseded",
    )
    for row in (await session.execute(stmt)).scalars().all():
        row.status = "superseded"
    revisions = (
        (
            await session.execute(
                select(StoryboardRevision).where(
                    StoryboardRevision.episode_id == episode_id,
                    StoryboardRevision.status == "approved",
                )
            )
        )
        .scalars()
        .all()
    )
    for revision in revisions:
        revision.status = "stale"


def _find_episode_spec(payload: StoryboardPayload, episode: Episode) -> Optional[Any]:
    """在 payload.episodes 里找对应本 episode 的 spec：先按 episode_id，再按 number。"""
    for ep in payload.episodes:
        if ep.episode_id and ep.episode_id == episode.id:
            return ep
    for ep in payload.episodes:
        if ep.number == episode.episode_no:
            return ep
    if len(payload.episodes) == 1:
        return payload.episodes[0]
    return None


async def _materialize(
    session: AsyncSession,
    episode: Episode,
    ep_spec: Any,
    revision_id: str,
) -> None:
    """把 payload 的 scene/shot 物化为 ORM 行（更新或新建），并清理消失的 pending shot。"""
    director_plan = (
        (
            await session.execute(
                select(DirectorPlanRevision)
                .where(DirectorPlanRevision.episode_id == episode.id)
                .order_by(DirectorPlanRevision.revision.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    # 现有场景（按 id 索引）
    scene_rows = (await session.execute(select(Scene).where(Scene.episode_id == episode.id))).scalars().all()
    scenes_by_id = {s.id: s for s in scene_rows}

    keep_shot_ids: set[str] = set()
    for s_idx, scene_spec in enumerate(ep_spec.scenes):
        if scene_spec.scene_id and scene_spec.scene_id in scenes_by_id:
            scene = scenes_by_id[scene_spec.scene_id]
            scene.scene_no = scene_spec.number
            scene.location = scene_spec.location
            scene.time_of_day = scene_spec.time_of_day
            scene.description = scene_spec.description
            scene.sort_order = s_idx
        else:
            scene = Scene(
                episode_id=episode.id,
                scene_no=scene_spec.number,
                location=scene_spec.location,
                time_of_day=scene_spec.time_of_day,
                description=scene_spec.description,
                sort_order=s_idx,
            )
            session.add(scene)
            await session.flush()

        shot_rows = (await session.execute(select(Shot).where(Shot.scene_id == scene.id))).scalars().all()
        shots_by_id = {s.id: s for s in shot_rows}
        payload_shot_ids: set[str] = set()

        for sh_idx, shot_spec in enumerate(scene_spec.shots):
            camera = shot_spec.camera.model_dump()
            transition = shot_spec.transition.model_dump()
            dialogue = [t.model_dump() for t in shot_spec.dialogue_tracks]
            narration = [t.model_dump() for t in shot_spec.narration_tracks]
            subtitle = [t.model_dump() for t in shot_spec.subtitle_tracks]
            if shot_spec.shot_id and shot_spec.shot_id in shots_by_id:
                shot = shots_by_id[shot_spec.shot_id]
                shot.shot_no = shot_spec.number
                shot.duration_ms = shot_spec.duration_ms
                shot.shot_type = shot_spec.shot_type
                shot.narrative_purpose = shot_spec.narrative_purpose
                shot.composition = shot_spec.composition
                shot.action_start = shot_spec.action_start
                shot.action_end = shot_spec.action_end
                shot.emotion = shot_spec.emotion
                shot.first_frame_prompt = shot_spec.first_frame_prompt
                shot.last_frame_prompt = shot_spec.last_frame_prompt
                shot.generation_mode = shot_spec.generation_mode
                shot.continuity_constraints = shot_spec.continuity_constraints
                shot.risk_tags = shot_spec.risk_tags
                shot.locked_asset_versions = shot_spec.locked_asset_versions
                shot.qa_status = shot_spec.qa_status
                shot.visual_prompt = shot_spec.visual_prompt
                shot.camera = camera
                shot.transition = transition
                shot.aspect_ratio = shot_spec.aspect_ratio
                shot.dialogue_tracks = dialogue
                shot.narration_tracks = narration
                shot.subtitle_tracks = subtitle
                shot.sort_order = sh_idx
                shot.storyboard_revision_id = revision_id
                shot.director_plan_revision_id = director_plan.id if director_plan else None
                shot.director_intent_key = shot_spec.director_intent_key
            else:
                shot = Shot(
                    scene_id=scene.id,
                    storyboard_revision_id=revision_id,
                    director_plan_revision_id=director_plan.id if director_plan else None,
                    director_intent_key=shot_spec.director_intent_key,
                    shot_no=shot_spec.number,
                    duration_ms=shot_spec.duration_ms,
                    shot_type=shot_spec.shot_type,
                    narrative_purpose=shot_spec.narrative_purpose,
                    composition=shot_spec.composition,
                    action_start=shot_spec.action_start,
                    action_end=shot_spec.action_end,
                    emotion=shot_spec.emotion,
                    first_frame_prompt=shot_spec.first_frame_prompt,
                    last_frame_prompt=shot_spec.last_frame_prompt,
                    generation_mode=shot_spec.generation_mode,
                    continuity_constraints=shot_spec.continuity_constraints,
                    risk_tags=shot_spec.risk_tags,
                    locked_asset_versions=shot_spec.locked_asset_versions,
                    qa_status=shot_spec.qa_status,
                    visual_prompt=shot_spec.visual_prompt,
                    camera=camera,
                    transition=transition,
                    aspect_ratio=shot_spec.aspect_ratio,
                    dialogue_tracks=dialogue,
                    narration_tracks=narration,
                    subtitle_tracks=subtitle,
                    sort_order=sh_idx,
                )
                session.add(shot)
                await session.flush()
            keep_shot_ids.add(shot.id)
            payload_shot_ids.add(shot.id)

        # 删除策略：payload 中消失且 status="pending" 的 shot 物理删除；
        # 非 pending（已有生成投入）的保留，仍指向旧 revision。
        for old in shot_rows:
            if old.id not in payload_shot_ids and old.status == "pending":
                await session.delete(old)

    # 场景中消失的旧场景：保留（可能挂着非 pending 的 shot），不做级联删除。
    # payload 明确带 scene_id 引用了不属于本 episode 的场景时忽略（当新建处理）。


async def save_storyboard(
    session: AsyncSession,
    project: SeriesProject,
    episode_id: str,
    payload: "StoryboardPayload | dict[str, Any]",
    created_by: str = "",
    based_on_script_revision_id: Optional[str] = None,
) -> StoryboardRevision:
    """校验并保存新 revision，同步物化 Scene/Shot，旧 revision/审批置 superseded。"""
    if isinstance(payload, dict):
        payload = StoryboardPayload.model_validate(payload)

    episode = await session.get(Episode, episode_id)
    if episode is None or episode.project_id != project.id:
        raise ValueError(f"episode {episode_id} not found in project {project.id}")

    if based_on_script_revision_id:
        script_revision = await session.get(ScriptRevision, based_on_script_revision_id)
        if script_revision is None or script_revision.episode_id != episode_id:
            raise ValueError("based_on_script_revision_id 不属于当前集")
    else:
        script_revision = (
            (
                await session.execute(
                    select(ScriptRevision)
                    .where(ScriptRevision.episode_id == episode_id)
                    .order_by(ScriptRevision.revision.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if script_revision is not None:
            based_on_script_revision_id = script_revision.id

    revision_no = await _next_revision(session, episode_id)
    payload_dict = payload.model_dump()
    row = StoryboardRevision(
        episode_id=episode_id,
        revision=revision_no,
        schema_version=payload.schema_version,
        payload=payload_dict,
        based_on_script_revision_id=based_on_script_revision_id,
        status="draft",
        created_by=created_by,
    )
    session.add(row)
    await session.flush()

    ep_spec = _find_episode_spec(payload, episode)
    if ep_spec is not None:
        await _materialize(session, episode, ep_spec, row.id)

    # 旧 revision 置 superseded
    stmt = select(StoryboardRevision).where(
        StoryboardRevision.episode_id == episode_id,
        StoryboardRevision.id != row.id,
        StoryboardRevision.status != "superseded",
    )
    for old in (await session.execute(stmt)).scalars().all():
        old.status = "superseded"

    await _supersede_storyboard_approvals(session, episode_id)

    await session.commit()
    await session.refresh(row)
    return row


async def get_current_storyboard(
    session: AsyncSession, episode_id: str
) -> tuple[Optional[StoryboardRevision], Optional[dict[str, Any]]]:
    """返回最新 revision 行与其 payload dict；无 revision 返回 (None, None)。"""
    stmt = (
        select(StoryboardRevision)
        .where(StoryboardRevision.episode_id == episode_id)
        .order_by(StoryboardRevision.revision.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        return None, None
    return row, row.payload


async def list_episodes_with_storyboard(
    session: AsyncSession, project_id: str, episode_id: Optional[str] = None
) -> list[dict[str, Any]]:
    """集 → 场景 → 镜头 层级结构输出（API 渲染用），每集附当前 revision 信息。"""
    stmt = select(Episode).where(Episode.project_id == project_id).order_by(Episode.episode_no)
    if episode_id:
        stmt = stmt.where(Episode.id == episode_id)
    episodes = (await session.execute(stmt)).scalars().all()

    result: list[dict[str, Any]] = []
    for ep in episodes:
        revision_row, _ = await get_current_storyboard(session, ep.id)
        scenes = (
            (await session.execute(select(Scene).where(Scene.episode_id == ep.id).order_by(Scene.sort_order)))
            .scalars()
            .all()
        )
        scene_dicts: list[dict[str, Any]] = []
        for scene in scenes:
            shots = (
                (
                    await session.execute(
                        select(Shot).where(Shot.scene_id == scene.id).order_by(Shot.sort_order)
                    )
                )
                .scalars()
                .all()
            )
            scene_dicts.append(
                {
                    "id": scene.id,
                    "episode_id": scene.episode_id,
                    "scene_no": scene.scene_no,
                    "location": scene.location,
                    "time_of_day": scene.time_of_day,
                    "description": scene.description,
                    "sort_order": scene.sort_order,
                    "shots": [
                        {
                            "id": sh.id,
                            "scene_id": sh.scene_id,
                            "storyboard_revision_id": sh.storyboard_revision_id,
                            "shot_no": sh.shot_no,
                            "duration_ms": sh.duration_ms,
                            "shot_type": sh.shot_type,
                            "narrative_purpose": sh.narrative_purpose,
                            "composition": sh.composition,
                            "action_start": sh.action_start,
                            "action_end": sh.action_end,
                            "emotion": sh.emotion,
                            "first_frame_prompt": sh.first_frame_prompt,
                            "last_frame_prompt": sh.last_frame_prompt,
                            "generation_mode": sh.generation_mode,
                            "continuity_constraints": sh.continuity_constraints,
                            "risk_tags": sh.risk_tags,
                            "locked_asset_versions": sh.locked_asset_versions,
                            "qa_status": sh.qa_status,
                            "visual_prompt": sh.visual_prompt,
                            "camera": sh.camera,
                            "transition": sh.transition,
                            "aspect_ratio": sh.aspect_ratio,
                            "status": sh.status,
                            "sort_order": sh.sort_order,
                            "selected_image_artifact_id": sh.selected_image_artifact_id,
                            "selected_video_artifact_id": sh.selected_video_artifact_id,
                            "dialogue_tracks": sh.dialogue_tracks,
                            "narration_tracks": sh.narration_tracks,
                            "subtitle_tracks": sh.subtitle_tracks,
                        }
                        for sh in shots
                    ],
                }
            )
        result.append(
            {
                "id": ep.id,
                "project_id": ep.project_id,
                "episode_no": ep.episode_no,
                "title": ep.title,
                "synopsis": ep.synopsis,
                "status": ep.status,
                "objective": ep.objective,
                "hook": ep.hook,
                "conflict": ep.conflict,
                "payoff": ep.payoff,
                "cliffhanger": ep.cliffhanger,
                "target_duration_ms": ep.target_duration_ms,
                "current_revision": revision_row.revision if revision_row else None,
                "revision_status": revision_row.status if revision_row else None,
                "based_on_script_revision_id": (
                    revision_row.based_on_script_revision_id if revision_row else None
                ),
                "scenes": scene_dicts,
            }
        )
    return result


# update_shot 允许修改的白名单字段
SHOT_EDITABLE_FIELDS = {
    "duration_ms",
    "shot_type",
    "narrative_purpose",
    "composition",
    "action_start",
    "action_end",
    "emotion",
    "first_frame_prompt",
    "last_frame_prompt",
    "generation_mode",
    "continuity_constraints",
    "risk_tags",
    "locked_asset_versions",
    "qa_status",
    "visual_prompt",
    "camera",
    "transition",
    "aspect_ratio",
    "dialogue_tracks",
    "narration_tracks",
    "subtitle_tracks",
    "status",
}
# 改了会触发审批失效的内容字段（status 流转不影响审批）
SHOT_CONTENT_FIELDS = SHOT_EDITABLE_FIELDS - {"status"}


async def update_shot(
    session: AsyncSession, shot_id: str, fields: dict[str, Any], user_id: Optional[int]
) -> Shot:
    """白名单更新 shot；改内容字段则使所在 episode 的 storyboard 审批失效。"""
    shot = await session.get(Shot, shot_id)
    if shot is None:
        raise ValueError(f"shot {shot_id} not found")

    # 权限：shot → scene → episode → project（无主项目同样拒绝，见 ownership）
    scene = await session.get(Scene, shot.scene_id)
    episode = await session.get(Episode, scene.episode_id) if scene else None
    project = await session.get(SeriesProject, episode.project_id) if episode else None
    if project is not None:
        try:
            check_project_access(project, user_id=user_id)
        except PermissionError:
            raise PermissionError("shot belongs to another user") from None

    changed_fields: list[str] = []
    before: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in SHOT_EDITABLE_FIELDS:
            continue
        if key in SHOT_CONTENT_FIELDS and getattr(shot, key) != value:
            changed_fields.append(key)
            before[key] = getattr(shot, key)
        setattr(shot, key, value)

    if changed_fields and episode is not None:
        from app.services import dependency_invalidation_service

        change_token = json.dumps(
            {"before": before, "after": {key: fields[key] for key in changed_fields}},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        await dependency_invalidation_service.apply_invalidation(
            session,
            project_id=episode.project_id,
            source_type="shot",
            source_id=shot.id,
            changed_fields=changed_fields,
            user_id=user_id,
            reason="shot_content_changed",
            change_token=change_token,
        )

    await session.commit()
    await session.refresh(shot)
    return shot


async def import_legacy_text(
    session: AsyncSession,
    project: SeriesProject,
    episode_id: str,
    text: str,
    created_by: str = "",
) -> StoryboardRevision:
    """把旧聊天文本分镜解析为 StoryboardPayload 并存为新 revision。"""
    payload = parse_legacy_storyboard_text(text, series_id=project.id)
    return await save_storyboard(session, project, episode_id, payload, created_by=created_by)
