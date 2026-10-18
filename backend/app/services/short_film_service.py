"""短片创作流程：故事 → 角色与场景 → 分镜（关键帧 / 视频）→ 成片。

一个短片 = project_type="short_film" 的 SeriesProject + 1 集。复用已有领域对象：
角色 / 场景是 AssetCard，参考图候选挂 AssetArtifactRef（primary = 选定），镜头通过
ShotAssetRef 引用选定参考图；关键帧、视频都是挂在镜头上的 GenerationJob，由 Worker 执行。
这里只提供直接的命令，不经过带审批节点的工作流引擎。
"""

from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Artifact,
    ArtifactCandidate,
    AssetArtifactRef,
    AssetCard,
    Episode,
    GenerationJob,
    Scene,
    SeriesProject,
    Shot,
    ShotAssetRef,
)
from app.services import asset_service, job_service, series_service

logger = logging.getLogger(__name__)

PROJECT_TYPE = "short_film"
ASSET_TYPES = ("character", "scene")
ACTIVE_JOB_STATUSES = ("pending", "leased", "running")
VIDEO_TIERS = ("preview", "final")
ASPECT_RATIOS = ("9:16", "16:9", "1:1", "4:3", "3:4")


class ShortFilmError(ValueError):
    """面向用户的错误，消息可直接展示。"""


# ── 故事设定 ──────────────────────────────────────────────────────────────


class StoryFields(BaseModel):
    idea: str = Field(default="", max_length=20_000)
    logline: str = Field(default="", max_length=500)
    background: str = Field(default="", max_length=5_000)
    style: str = Field(default="", max_length=200)
    aspect_ratio: str = "9:16"
    target_seconds: int = Field(default=60, ge=10, le=600)

    @field_validator("aspect_ratio")
    @classmethod
    def _ratio(cls, value: str) -> str:
        if value not in ASPECT_RATIOS:
            raise ValueError(f"画幅只支持 {', '.join(ASPECT_RATIOS)}")
        return value


def story_of(project: SeriesProject) -> dict[str, Any]:
    stored = (project.settings or {}).get("story") or {}
    return StoryFields.model_validate({k: v for k, v in stored.items() if k in StoryFields.model_fields}).model_dump()


async def create_film(session: AsyncSession, user_id: int, *, title: str, story: StoryFields) -> SeriesProject:
    project = await series_service.create_project(
        session,
        user_id,
        project_type=PROJECT_TYPE,
        title=title.strip() or "未命名短片",
        settings={"workspace_mode": "short_film", "story": story.model_dump()},
        episode_count=1,
        target_duration_ms=story.target_seconds * 1000,
    )
    await session.flush()
    return project


async def get_film(session: AsyncSession, project_id: str, user_id: int, *, is_admin: bool = False) -> SeriesProject:
    try:
        project = await series_service.get_project(session, project_id, user_id, is_admin=is_admin)
    except PermissionError:
        project = None
    if project is None or project.project_type != PROJECT_TYPE:
        raise LookupError("短片不存在")
    return project


async def list_films(session: AsyncSession, user_id: int) -> list[SeriesProject]:
    result = await session.execute(
        select(SeriesProject)
        .where(SeriesProject.user_id == user_id, SeriesProject.project_type == PROJECT_TYPE)
        .order_by(SeriesProject.created_at.desc())
    )
    return list(result.scalars().all())


async def update_story(session: AsyncSession, project: SeriesProject, changes: dict[str, Any]) -> SeriesProject:
    title = changes.pop("title", None)
    if title is not None and title.strip():
        project.title = title.strip()
    merged = StoryFields.model_validate({**story_of(project), **changes})
    project.settings = {**(project.settings or {}), "story": merged.model_dump()}
    project.target_duration_ms = merged.target_seconds * 1000
    await session.flush()
    return project


async def episode_of(session: AsyncSession, project: SeriesProject) -> Episode:
    episode = (
        await session.execute(
            select(Episode).where(Episode.project_id == project.id).order_by(Episode.episode_no).limit(1)
        )
    ).scalar_one_or_none()
    if episode is None:
        episode = Episode(project_id=project.id, episode_no=1)
        session.add(episode)
        await session.flush()
    return episode


# ── AI 拆分：想法 / 剧本 → 角色、场景、镜头 ──────────────────────────────


class PlanCharacter(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=1_000)


class PlanScene(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=1_000)


class PlanLine(BaseModel):
    character: str = ""
    text: str = Field(min_length=1, max_length=500)


class PlanShot(BaseModel):
    scene: str = ""
    characters: list[str] = Field(default_factory=list)
    description: str = Field(min_length=1, max_length=1_000)
    shot_size: str = Field(default="", max_length=32)
    camera: str = Field(default="", max_length=200)
    duration_s: int = Field(default=5, ge=2, le=15)
    dialogue: list[PlanLine] = Field(default_factory=list)


class FilmPlan(BaseModel):
    title: str = Field(default="", max_length=100)
    logline: str = Field(default="", max_length=500)
    background: str = Field(default="", max_length=5_000)
    characters: list[PlanCharacter] = Field(default_factory=list, max_length=12)
    scenes: list[PlanScene] = Field(default_factory=list, max_length=12)
    shots: list[PlanShot] = Field(min_length=1, max_length=40)


PLAN_SYSTEM_PROMPT = """你是短片导演，负责把创作者的想法或剧本拆成可直接生成的分镜。
只输出一个 JSON 对象，不要输出解释或 Markdown。字段：
{
  "title": "片名",
  "logline": "一句话梗概",
  "background": "故事背景与世界观，100 字以内",
  "characters": [{"key": "英文小写标识", "name": "角色名", "description": "外貌、年龄、服装、标志特征，用于生成定妆图"}],
  "scenes": [{"key": "英文小写标识", "name": "场景名", "description": "环境、时间、光线、色调"}],
  "shots": [{"scene": "场景 key", "characters": ["出镜角色 key"], "description": "画面内容与动作，具体可视化",
             "shot_size": "远景/全景/中景/近景/特写", "camera": "运镜，如 缓慢推近", "duration_s": 3 到 10 的整数,
             "dialogue": [{"character": "角色 key", "text": "台词"}]}]
}
要求：镜头总时长接近目标时长；每个镜头只表达一个动作；同一角色外貌描述前后一致；不要编造未出现的角色。"""


def _plan_user_prompt(story: dict[str, Any], text: str, source: str) -> str:
    kind = "剧本" if source == "script" else "想法"
    return (
        f"画风：{story.get('style') or '不限'}；画幅：{story.get('aspect_ratio')}；目标时长：约 {story.get('target_seconds')} 秒。\n"
        f"故事背景：{story.get('background') or '无'}\n"
        f"以下是创作者的{kind}：\n{text.strip()}"
    )


def _extract_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("模型没有返回 JSON")
    return json.loads(text[start : end + 1])


async def _call_planner(messages: list[dict[str, str]]) -> str:
    """调用「剧本拆分与规划」默认模型；测试中可替换。"""
    from app.llm.factory import create_llm

    try:
        llm = create_llm(purpose="planner")
    except Exception as error:  # noqa: BLE001 - 未配置模型时给出可操作的提示
        raise ShortFilmError("还没有可用的对话模型，请在「设置 → 模型服务 → 默认模型」里设置「剧本拆分与规划」") from error
    response = await llm.ainvoke(messages)
    return str(getattr(response, "content", response))


async def generate_plan(story: dict[str, Any], text: str, source: str) -> FilmPlan:
    """让模型输出分镜 JSON；格式不对时把错误发回去修一次。"""
    messages = [
        {"role": "system", "content": PLAN_SYSTEM_PROMPT},
        {"role": "user", "content": _plan_user_prompt(story, text, source)},
    ]
    raw = await _call_planner(messages)
    try:
        return FilmPlan.model_validate(_extract_json(raw))
    except (ValueError, ValidationError) as error:
        messages += [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": f"上面的输出不符合要求：{str(error)[:500]}。请只输出修正后的完整 JSON。"},
        ]
        repaired = await _call_planner(messages)
        try:
            return FilmPlan.model_validate(_extract_json(repaired))
        except (ValueError, ValidationError) as final_error:
            raise ShortFilmError("模型输出的分镜格式无法识别，请重试或换一个模型") from final_error


async def apply_plan(session: AsyncSession, project: SeriesProject, plan: FilmPlan, user_id: int) -> None:
    """用拆分结果替换分镜；同名角色 / 场景保留（连同已选定的参考图）。"""
    from app.services import storyboard_service

    story = story_of(project)
    await update_story(
        session,
        project,
        {
            "logline": plan.logline or story["logline"],
            "background": plan.background or story["background"],
            **({"title": plan.title} if plan.title and project.title in {"", "未命名短片"} else {}),
        },
    )
    existing = {(card.type, card.name): card for card in await _assets(session, project)}
    ids: dict[tuple[str, str], str] = {}
    for kind, items in (("character", plan.characters), ("scene", plan.scenes)):
        for item in items:
            card = existing.get((kind, item.name))
            if card is None:
                card = await asset_service.create_asset_card(
                    session, project.id, user_id, type=kind, name=item.name, description=item.description,
                    source="short_film_plan",
                )
            elif item.description and not card.description:
                card.description = item.description
            ids[(kind, item.key)] = card.id

    episode = await episode_of(session, project)
    scene_order = [scene.key for scene in plan.scenes]
    for shot in plan.shots:
        if shot.scene not in scene_order:
            scene_order.append(shot.scene)
    scene_names = {scene.key: scene for scene in plan.scenes}
    scenes_payload = []
    for number, key in enumerate(scene_order, start=1):
        spec = scene_names.get(key)
        shots = [shot for shot in plan.shots if shot.scene == key]
        scenes_payload.append(
            {
                "number": number,
                "location": spec.name if spec else (key or "未命名场景"),
                "description": spec.description if spec else "",
                "shots": [
                    {
                        "number": index,
                        "duration_ms": shot.duration_s * 1000,
                        "shot_type": shot.shot_size,
                        "visual_prompt": shot.description,
                        "camera": {"type": "custom" if shot.camera else "fixed", "description": shot.camera},
                        "aspect_ratio": story["aspect_ratio"],
                        "character_asset_ids": [ids[("character", key)] for key in shot.characters if ("character", key) in ids],
                        "dialogue_tracks": [
                            {"speaker_asset_id": ids.get(("character", line.character)), "text": line.text}
                            for line in shot.dialogue
                        ],
                    }
                    for index, shot in enumerate(shots, start=1)
                ],
            }
        )
    payload = {"schema_version": 1, "series_id": project.id, "episodes": [{"number": episode.episode_no, "scenes": scenes_payload}]}
    await storyboard_service.save_storyboard(session, project, episode.id, payload, created_by=str(user_id))

    # 绑定镜头 ↔ 角色 / 场景参考（选定参考图之后自动带上）
    shots = await current_shots(session, project)
    scene_assets = {
        scene.location: ids.get(("scene", key)) for key in scene_order for scene in [await _scene_by_location(session, episode, key, scene_names)] if scene
    }
    for shot, row in shots:
        for asset_id in (shot.locked_asset_versions or {}).keys():
            del asset_id
        refs = [(asset_id, "character") for asset_id in _character_ids(shot)]
        scene_asset = scene_assets.get(row.location)
        if scene_asset:
            refs.append((scene_asset, "scene"))
        for order, (asset_id, kind) in enumerate(refs):
            await _bind(session, shot.id, asset_id, kind, order)
    await session.flush()


async def _scene_by_location(session, episode, key, scene_names) -> Optional[Scene]:
    location = scene_names[key].name if key in scene_names else (key or "未命名场景")
    return (
        await session.execute(select(Scene).where(Scene.episode_id == episode.id, Scene.location == location).limit(1))
    ).scalar_one_or_none()


def _character_ids(shot: Shot) -> list[str]:
    ids: list[str] = []
    for track in shot.dialogue_tracks or []:
        speaker = (track or {}).get("speaker_asset_id")
        if speaker and speaker not in ids:
            ids.append(speaker)
    for asset_id in (shot.continuity_constraints or {}).get("character_asset_ids", []):
        if asset_id not in ids:
            ids.append(asset_id)
    return ids


async def _bind(session: AsyncSession, shot_id: str, asset_id: str, kind: str, order: int) -> None:
    asset = await session.get(AssetCard, asset_id)
    if asset is None:
        return
    primary = await primary_image(session, asset_id)
    existing = (
        await session.execute(
            select(ShotAssetRef).where(ShotAssetRef.shot_id == shot_id, ShotAssetRef.asset_id == asset_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.asset_version = asset.version
        existing.artifact_id = primary.id if primary else None
        existing.reference_type = kind
        return
    session.add(
        ShotAssetRef(
            shot_id=shot_id,
            asset_id=asset_id,
            role=kind,
            asset_version=asset.version,
            reference_type=kind,
            artifact_id=primary.id if primary else None,
            subject=asset.name,
            sort_order=order,
            usage="identity",
        )
    )


# ── 角色与场景 ────────────────────────────────────────────────────────────


async def _assets(session: AsyncSession, project: SeriesProject) -> list[AssetCard]:
    result = await session.execute(
        select(AssetCard)
        .where(AssetCard.project_id == project.id, AssetCard.type.in_(ASSET_TYPES))
        .order_by(AssetCard.created_at)
    )
    return list(result.scalars().all())


async def owned_asset(session: AsyncSession, project: SeriesProject, asset_id: str) -> AssetCard:
    asset = await session.get(AssetCard, asset_id)
    if asset is None or asset.project_id != project.id or asset.type not in ASSET_TYPES:
        raise LookupError("角色或场景不存在")
    return asset


async def add_asset(session: AsyncSession, project: SeriesProject, user_id: int, *, kind: str, name: str, description: str) -> AssetCard:
    if kind not in ASSET_TYPES:
        raise ShortFilmError("类型只能是角色或场景")
    return await asset_service.create_asset_card(
        session, project.id, user_id, type=kind, name=name.strip() or "未命名", description=description, source="short_film"
    )


async def update_asset(session: AsyncSession, asset: AssetCard, *, name: Optional[str], description: Optional[str]) -> AssetCard:
    changed = False
    if name is not None and name.strip() and name.strip() != asset.name:
        asset.name = name.strip()
        changed = True
    if description is not None and description != asset.description:
        asset.description = description
        changed = True
    if changed:
        # 描述变化后版本 +1，镜头引用同步到新版本（提示词编译会校验版本）
        asset.version += 1
        refs = (await session.execute(select(ShotAssetRef).where(ShotAssetRef.asset_id == asset.id))).scalars().all()
        for ref in refs:
            ref.asset_version = asset.version
            ref.subject = asset.name
    await session.flush()
    return asset


async def delete_asset(session: AsyncSession, asset: AssetCard) -> None:
    await session.execute(delete(ShotAssetRef).where(ShotAssetRef.asset_id == asset.id))
    await session.execute(delete(AssetArtifactRef).where(AssetArtifactRef.asset_id == asset.id))
    await session.delete(asset)
    await session.flush()


async def primary_image(session: AsyncSession, asset_id: str) -> Optional[Artifact]:
    ref = (
        await session.execute(
            select(AssetArtifactRef).where(AssetArtifactRef.asset_id == asset_id, AssetArtifactRef.role == "primary").limit(1)
        )
    ).scalar_one_or_none()
    return await session.get(Artifact, ref.artifact_id) if ref else None


def _asset_prompt(asset: AssetCard, story: dict[str, Any]) -> str:
    style = story.get("style") or "电影感"
    if asset.type == "character":
        return f"{style}风格，角色设定图，{asset.name}：{asset.description}。单人，全身正面，干净纯色背景，清晰面部特征"
    return f"{style}风格，场景概念图，{asset.name}：{asset.description}。无人物，构图完整"


async def generate_asset_images(session: AsyncSession, project: SeriesProject, asset: AssetCard, count: int = 2) -> GenerationJob:
    story = story_of(project)
    return await job_service.create_job(
        session,
        operation="generate_image",
        project_id=project.id,
        params={
            "asset_id": asset.id,
            "asset_version": asset.version,
            "prompt": _asset_prompt(asset, story),
            "aspect_ratio": "3:4" if asset.type == "character" else story["aspect_ratio"],
            "candidate_count": max(1, min(count, 4)),
            "purpose_hint": "asset_image",
        },
        idempotency_key=f"short-film:asset-image:{asset.id}:{uuid.uuid4().hex}",
        worker_class="image",
    )


async def select_asset_image(session: AsyncSession, asset: AssetCard, artifact_id: str) -> None:
    refs = (await session.execute(select(AssetArtifactRef).where(AssetArtifactRef.asset_id == asset.id))).scalars().all()
    target = next((ref for ref in refs if ref.artifact_id == artifact_id), None)
    if target is None:
        raise LookupError("这张图不属于该角色或场景")
    for ref in refs:
        ref.role = "primary" if ref is target else "candidate"
    # 已绑定的镜头立即改用新选定的参考图
    shot_refs = (await session.execute(select(ShotAssetRef).where(ShotAssetRef.asset_id == asset.id))).scalars().all()
    for shot_ref in shot_refs:
        shot_ref.artifact_id = artifact_id
        shot_ref.asset_version = asset.version
    await session.flush()


# ── 镜头 ──────────────────────────────────────────────────────────────────


async def current_shots(session: AsyncSession, project: SeriesProject) -> list[tuple[Shot, Scene]]:
    from app.services import job_planner

    episode = await episode_of(session, project)
    shots = await job_planner._current_revision_shots(session, episode.id)
    scenes = {scene.id: scene for scene in (await session.execute(select(Scene).where(Scene.episode_id == episode.id))).scalars()}
    rows = [(shot, scenes[shot.scene_id]) for shot in shots if shot.scene_id in scenes]
    rows.sort(key=lambda pair: (pair[1].sort_order, pair[1].scene_no, pair[0].sort_order, pair[0].shot_no))
    return rows


async def owned_shot(session: AsyncSession, project: SeriesProject, shot_id: str) -> Shot:
    for shot, _scene in await current_shots(session, project):
        if shot.id == shot_id:
            return shot
    raise LookupError("镜头不存在")


async def update_shot(session: AsyncSession, project: SeriesProject, shot: Shot, changes: dict[str, Any]) -> Shot:
    if "description" in changes and changes["description"] is not None:
        shot.visual_prompt = changes["description"]
    if changes.get("shot_size") is not None:
        shot.shot_type = changes["shot_size"]
    if changes.get("camera") is not None:
        shot.camera = {"type": "custom" if changes["camera"] else "fixed", "description": changes["camera"]}
    if changes.get("duration_s") is not None:
        shot.duration_ms = int(changes["duration_s"]) * 1000
    if changes.get("dialogue") is not None:
        shot.dialogue_tracks = [
            {"speaker_asset_id": line.get("character_id"), "text": line["text"], "lang": "zh", "start_ms": 0, "end_ms": 0}
            for line in changes["dialogue"]
            if (line.get("text") or "").strip()
        ]
    if changes.get("character_ids") is not None:
        wanted = [asset_id for asset_id in changes["character_ids"] if asset_id]
        for asset_id in wanted:
            await owned_asset(session, project, asset_id)
        await session.execute(
            delete(ShotAssetRef).where(
                ShotAssetRef.shot_id == shot.id, ShotAssetRef.reference_type == "character", ShotAssetRef.asset_id.notin_(wanted)
            )
        )
        for order, asset_id in enumerate(wanted):
            await _bind(session, shot.id, asset_id, "character", order)
    await session.flush()
    return shot


def _shot_prompt(shot: Shot, story: dict[str, Any], names: list[str]) -> str:
    parts = [story.get("style") or "", shot.shot_type or "", shot.visual_prompt or ""]
    camera = (shot.camera or {}).get("description")
    if names:
        parts.append(f"出镜：{'、'.join(names)}")
    if camera:
        parts.append(f"运镜：{camera}")
    return "，".join(part for part in parts if part)


async def _shot_reference_names(session: AsyncSession, shot: Shot) -> list[str]:
    refs = (
        await session.execute(
            select(ShotAssetRef).where(ShotAssetRef.shot_id == shot.id, ShotAssetRef.reference_type == "character")
        )
    ).scalars().all()
    return [ref.subject for ref in refs if ref.subject]


async def generate_keyframes(session: AsyncSession, project: SeriesProject, shot_ids: Optional[list[str]] = None) -> list[GenerationJob]:
    from app.services.prompt_compiler import compile_shot_input

    story = story_of(project)
    jobs = []
    for shot, _scene in await current_shots(session, project):
        if shot_ids and shot.id not in shot_ids:
            continue
        compiled = await compile_shot_input(session, shot)
        prompt = _shot_prompt(shot, story, await _shot_reference_names(session, shot))
        jobs.append(
            await job_service.create_job(
                session,
                operation="generate_image",
                project_id=project.id,
                shot_id=shot.id,
                params={
                    "shot_id": shot.id,
                    "prompt": prompt,
                    "prompt_input_hash": compiled.input_hash,
                    "reference_slots": compiled.reference_slots,
                    # 有选定的角色 / 场景参考图时以它为参考生成，保持形象一致
                    "source_image": compiled.reference_images[0] if compiled.reference_images else None,
                    "aspect_ratio": story["aspect_ratio"],
                    "candidate_count": 1,
                    "purpose_hint": "keyframe",
                },
                idempotency_key=f"short-film:keyframe:{shot.id}:{uuid.uuid4().hex}",
                worker_class="image",
            )
        )
    await session.flush()
    return jobs


async def _video_request(session: AsyncSession, shot: Shot, story: dict[str, Any], tier: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """镜头视频的请求参数与路由计划。已有关键帧时做图生视频，不再重复传角色参考图。"""
    from app.model_runtime.video import resolve_video_plan
    from app.services.job_planner import build_video_job_params, find_first_frame_storage_key

    first_frame = await find_first_frame_storage_key(session, shot.id)
    names = await _shot_reference_names(session, shot)
    policy = {"resolution": "768P", "video_tier": tier}
    params = build_video_job_params(
        shot_id=shot.id,
        step_id="",
        run_id="",
        prompt=_shot_prompt(shot, story, names),
        duration_ms=shot.duration_ms,
        aspect_ratio=story["aspect_ratio"],
        policy=policy,
        first_frame=first_frame,
    )
    plan = await resolve_video_plan(
        session,
        duration=max(1, shot.duration_ms // 1000),
        tier=tier,
        mode=params["mode"],
        aspect_ratio=story["aspect_ratio"],
        reference_count=0,
    )
    return params, plan


async def quote_videos(session: AsyncSession, project: SeriesProject, tier: str, shot_ids: Optional[list[str]] = None) -> dict[str, Any]:
    from app.model_runtime.video import VideoRoutingError

    if tier not in VIDEO_TIERS:
        raise ShortFilmError("档位只能是 预览 或 终版")
    story = story_of(project)
    items, total = [], Decimal("0")
    for shot, _scene in await current_shots(session, project):
        if shot_ids and shot.id not in shot_ids:
            continue
        try:
            params, plan = await _video_request(session, shot, story, tier)
        except VideoRoutingError as error:
            items.append({"shot_id": shot.id, "ok": False, "error": str(error)})
            continue
        cost = Decimal(str(plan["estimated_total_cost"]))
        total += cost
        items.append(
            {
                "shot_id": shot.id,
                "ok": True,
                "mode": params["mode"],
                "deployment": plan.get("deployment_name") or plan["provider"],
                "model": plan["model"],
                "resolution": plan["resolution"],
                "estimated_cost": float(cost),
            }
        )
    return {"tier": tier, "total": float(total), "items": items}


async def generate_videos(session: AsyncSession, project: SeriesProject, tier: str, shot_ids: Optional[list[str]] = None) -> list[GenerationJob]:
    from app.model_runtime.video import VideoRoutingError
    from app.providers.pricing import PRICE_TABLE_VERSION

    if tier not in VIDEO_TIERS:
        raise ShortFilmError("档位只能是 预览 或 终版")
    story = story_of(project)
    jobs = []
    for shot, _scene in await current_shots(session, project):
        if shot_ids and shot.id not in shot_ids:
            continue
        try:
            params, plan = await _video_request(session, shot, story, tier)
        except VideoRoutingError as error:
            raise ShortFilmError(f"第 {shot.shot_no} 镜没有可用的视频模型：{error}") from error
        params["reference_images"] = []
        params["routing_snapshot"] = {
            "provider": plan["provider"],
            "model": plan["model"],
            "resolution": plan["resolution"],
            "protocol": plan.get("protocol", ""),
            "billable_duration": plan["billable_duration"],
            "estimated_total_cost": str(plan["estimated_total_cost"]),
            "price_snapshot": plan.get("price_snapshot", {}),
            "price_table_version": plan.get("price_table_version", PRICE_TABLE_VERSION),
            "deployment_name": plan.get("deployment_name", ""),
            "deployment_revision": plan.get("deployment_revision"),
            "deployment_config_hash": plan.get("deployment_config_hash", ""),
            "catalog_revision": plan.get("catalog_revision", ""),
            "route": plan.get("route", {}),
        }
        params["resolution"] = plan["resolution"]
        params["video_tier"] = tier
        jobs.append(
            await job_service.create_job(
                session,
                operation="generate_video",
                project_id=project.id,
                shot_id=shot.id,
                params=params,
                provider=plan["provider"],
                model=plan["model"],
                idempotency_key=f"short-film:video:{tier}:{shot.id}:{uuid.uuid4().hex}",
                worker_class="video",
            )
        )
    await session.flush()
    return jobs


async def select_shot_artifact(session: AsyncSession, project: SeriesProject, shot: Shot, artifact_id: str, user_id: int) -> None:
    from app.services import candidate_service

    candidate = (
        await session.execute(
            select(ArtifactCandidate).where(ArtifactCandidate.shot_id == shot.id, ArtifactCandidate.artifact_id == artifact_id)
        )
    ).scalar_one_or_none()
    if candidate is None:
        raise LookupError("这个结果不属于该镜头")
    await candidate_service.promote_candidate(session, candidate, user_id=user_id)


# ── 成片 ──────────────────────────────────────────────────────────────────


async def _chosen_video(session: AsyncSession, shot: Shot) -> Optional[Artifact]:
    from app.services.job_planner import _selected_shot_artifact

    selected = await _selected_shot_artifact(session, shot.id, "video")
    if selected is not None:
        return selected
    return (
        await session.execute(
            select(Artifact)
            .join(GenerationJob, Artifact.job_id == GenerationJob.id)
            .where(
                GenerationJob.shot_id == shot.id,
                GenerationJob.operation == "generate_video",
                GenerationJob.status == "succeeded",
                Artifact.type == "video",
            )
            .order_by(Artifact.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def compose_film(session: AsyncSession, project: SeriesProject) -> GenerationJob:
    clips, missing = [], []
    for shot, _scene in await current_shots(session, project):
        artifact = await _chosen_video(session, shot)
        if artifact is None:
            missing.append(shot.shot_no)
        else:
            clips.append({"shot_id": shot.id, "artifact_id": artifact.id, "storage_key": artifact.storage_key})
    if not clips:
        raise ShortFilmError("还没有任何镜头生成视频")
    if missing:
        raise ShortFilmError(f"还有 {len(missing)} 个镜头没有视频，先生成后再合成")
    episode = await episode_of(session, project)
    return await job_service.create_job(
        session,
        operation="compose_film",
        project_id=project.id,
        params={"episode_id": episode.id, "clips": clips},
        idempotency_key=f"short-film:compose:{project.id}:{uuid.uuid4().hex}",
        worker_class="render",
    )


# ── 页面视图 ──────────────────────────────────────────────────────────────


def _artifact_view(artifact: Optional[Artifact]) -> Optional[dict[str, Any]]:
    if artifact is None:
        return None
    return {"artifact_id": artifact.id, "url": asset_service.artifact_url(artifact)}


def _job_state(jobs: list[GenerationJob]) -> dict[str, Any]:
    if not jobs:
        return {"status": "none"}
    latest = max(jobs, key=lambda job: job.created_at)
    if any(job.status in ACTIVE_JOB_STATUSES for job in jobs):
        return {"status": "running", "progress": max((job.progress or 0) for job in jobs if job.status in ACTIVE_JOB_STATUSES)}
    if latest.status == "failed":
        return {"status": "failed", "error": (latest.error or "")[:300], "error_code": latest.error_code}
    return {"status": latest.status}


async def film_view(session: AsyncSession, project: SeriesProject) -> dict[str, Any]:
    from app.services import cost_report_service

    story = story_of(project)
    episode = await episode_of(session, project)
    jobs = list((await session.execute(select(GenerationJob).where(GenerationJob.project_id == project.id))).scalars().all())
    artifacts = {
        artifact.id: artifact
        for artifact in (await session.execute(select(Artifact).where(Artifact.project_id == project.id))).scalars().all()
    }

    assets_out: dict[str, list[dict[str, Any]]] = {"character": [], "scene": []}
    for asset in await _assets(session, project):
        refs = (await session.execute(select(AssetArtifactRef).where(AssetArtifactRef.asset_id == asset.id))).scalars().all()
        asset_jobs = [job for job in jobs if (job.params or {}).get("asset_id") == asset.id]
        primary = next((ref for ref in refs if ref.role == "primary"), None)
        assets_out[asset.type].append(
            {
                "id": asset.id,
                "type": asset.type,
                "name": asset.name,
                "description": asset.description,
                "version": asset.version,
                "image": _artifact_view(artifacts.get(primary.artifact_id)) if primary else None,
                "candidates": [
                    {**(_artifact_view(artifacts.get(ref.artifact_id)) or {}), "selected": ref.role == "primary"}
                    for ref in refs
                    if ref.artifact_id in artifacts
                ],
                "job": _job_state(asset_jobs),
            }
        )

    shots_out = []
    candidates = (
        await session.execute(select(ArtifactCandidate).where(ArtifactCandidate.project_id == project.id))
    ).scalars().all()
    for index, (shot, scene) in enumerate(await current_shots(session, project), start=1):
        shot_jobs = [job for job in jobs if job.shot_id == shot.id]
        image_jobs = [job for job in shot_jobs if job.operation == "generate_image"]
        video_jobs = [job for job in shot_jobs if job.operation == "generate_video"]
        tier_of_job = {job.id: (job.params or {}).get("video_tier", "final") for job in video_jobs}
        shot_candidates = [cand for cand in candidates if cand.shot_id == shot.id and cand.artifact_id in artifacts]
        image_candidates = [cand for cand in shot_candidates if artifacts[cand.artifact_id].type == "image"]
        video_candidates = [cand for cand in shot_candidates if artifacts[cand.artifact_id].type == "video"]
        refs = (await session.execute(select(ShotAssetRef).where(ShotAssetRef.shot_id == shot.id))).scalars().all()
        from app.services.job_planner import find_first_frame_storage_key

        keyframe_key = await find_first_frame_storage_key(session, shot.id)
        keyframe = next((artifacts[c.artifact_id] for c in image_candidates if artifacts[c.artifact_id].storage_key == keyframe_key), None)
        chosen_video = await _chosen_video(session, shot)
        shots_out.append(
            {
                "id": shot.id,
                "index": index,
                "scene": {"id": scene.id, "name": scene.location},
                "description": shot.visual_prompt,
                "shot_size": shot.shot_type,
                "camera": (shot.camera or {}).get("description", ""),
                "duration_s": max(1, shot.duration_ms // 1000),
                "dialogue": [
                    {"character_id": (track or {}).get("speaker_asset_id"), "text": (track or {}).get("text", "")}
                    for track in shot.dialogue_tracks or []
                ],
                "character_ids": [ref.asset_id for ref in refs if ref.reference_type == "character"],
                "keyframe": _artifact_view(keyframe),
                "keyframe_candidates": [
                    {**(_artifact_view(artifacts[c.artifact_id]) or {}), "selected": keyframe is not None and c.artifact_id == keyframe.id}
                    for c in image_candidates
                ],
                "keyframe_job": _job_state(image_jobs),
                "video": {**_artifact_view(chosen_video), "tier": tier_of_job.get(chosen_video.job_id, "final")} if chosen_video else None,
                "video_candidates": [
                    {
                        **(_artifact_view(artifacts[c.artifact_id]) or {}),
                        "tier": tier_of_job.get(artifacts[c.artifact_id].job_id, "final"),
                        "selected": chosen_video is not None and c.artifact_id == chosen_video.id,
                    }
                    for c in video_candidates
                ],
                "video_job": _job_state(video_jobs),
            }
        )

    compose_jobs = [job for job in jobs if job.operation == "compose_film"]
    film_job = max(compose_jobs, key=lambda job: job.created_at) if compose_jobs else None
    film_artifact = artifacts.get(((film_job.result or {}).get("artifact_id")) or "") if film_job else None
    cost = await cost_report_service.cost_report(session, project_id=project.id, dimensions=("purpose",))
    return {
        "id": project.id,
        "title": project.title,
        "episode_id": episode.id,
        "story": story,
        "characters": assets_out["character"],
        "scenes": assets_out["scene"],
        "shots": shots_out,
        "film": {**_artifact_view(film_artifact), "created_at": film_job.finished_at.isoformat() if film_job and film_job.finished_at else None}
        if film_artifact
        else None,
        "film_job": _job_state(compose_jobs),
        "active_jobs": sum(job.status in ACTIVE_JOB_STATUSES for job in jobs),
        "cost": {
            "spent": cost["totals"]["effective_cost"] or 0,
            "estimated": cost["totals"]["estimated_cost"] or 0,
            "by_purpose": cost["breakdown"]["purpose"],
        },
    }
