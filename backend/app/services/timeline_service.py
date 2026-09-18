"""不可变 TimelineRevision / EDL 的创建、校验、审批与确定性指纹。"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Approval, Artifact, Episode, Scene, Shot, TimelineRevision


class VolumeKeyframe(BaseModel):
    time_ms: int = Field(ge=0)
    volume_db: float = Field(default=0, ge=-96, le=24)


class VideoClip(BaseModel):
    id: str
    shot_id: Optional[str] = None
    artifact_id: str
    artifact_hash: str = ""
    source_in_ms: int = Field(default=0, ge=0)
    source_out_ms: int = Field(gt=0)
    timeline_start_ms: int = Field(ge=0)
    speed: float = Field(default=1, gt=0, le=8)
    transition: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_range(self):
        if self.source_out_ms <= self.source_in_ms:
            raise ValueError("source_out_ms must be greater than source_in_ms")
        return self


class AudioClip(BaseModel):
    id: str
    kind: Literal["dialogue", "narration", "sfx", "ambience", "bgm"]
    artifact_id: str
    artifact_hash: str = ""
    source_in_ms: int = Field(default=0, ge=0)
    source_out_ms: Optional[int] = Field(default=None, gt=0)
    timeline_start_ms: int = Field(ge=0)
    volume_keyframes: list[VolumeKeyframe] = Field(default_factory=list)
    ducking: dict[str, Any] = Field(default_factory=dict)
    edit: Literal["normal", "j_cut", "l_cut"] = "normal"


class SubtitleCue(BaseModel):
    id: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)
    style: dict[str, Any] = Field(default_factory=dict)
    safe_area: dict[str, Any] = Field(default_factory=lambda: {"bottom_percent": 8})

    @model_validator(mode="after")
    def valid_range(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("subtitle end_ms must be greater than start_ms")
        return self


class OverlayClip(BaseModel):
    id: str
    artifact_id: Optional[str] = None
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    layout: dict[str, Any] = Field(default_factory=dict)


class TimelineSpec(BaseModel):
    schema_version: int = 1
    fps: int = Field(default=24, ge=1, le=120)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    video_tracks: list[dict[str, Any]] = Field(default_factory=list)
    audio_tracks: list[dict[str, Any]] = Field(default_factory=list)
    subtitle_tracks: list[dict[str, Any]] = Field(default_factory=list)
    overlay_tracks: list[dict[str, Any]] = Field(default_factory=list)
    export_profiles: dict[str, dict[str, Any]] = Field(default_factory=lambda: {
        "preview": {"burn_subtitles": True, "allow_placeholders": True},
        "review": {"burn_subtitles": True, "watermark": True},
        "no-subtitle": {"burn_subtitles": False},
        "final": {"burn_subtitles": True, "allow_placeholders": False},
    })


def canonical_hash(spec: dict[str, Any]) -> str:
    body = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def video_clips(spec: dict[str, Any]) -> list[VideoClip]:
    clips: list[VideoClip] = []
    for track in spec.get("video_tracks", []):
        clips.extend(VideoClip.model_validate(item) for item in track.get("clips", []))
    return sorted(clips, key=lambda item: (item.timeline_start_ms, item.id))


async def validate_artifacts(session: AsyncSession, project_id: str, spec: dict[str, Any]) -> None:
    artifact_ids: set[str] = set()
    for group in ("video_tracks", "audio_tracks", "overlay_tracks"):
        for track in spec.get(group, []):
            artifact_ids.update(str(c["artifact_id"]) for c in track.get("clips", []) if c.get("artifact_id"))
    if not artifact_ids:
        return
    artifacts = (await session.execute(select(Artifact).where(Artifact.id.in_(artifact_ids)))).scalars().all()
    found = {a.id: a for a in artifacts}
    missing = sorted(artifact_ids - set(found))
    if missing:
        raise ValueError(f"timeline artifacts not found: {missing}")
    foreign = [aid for aid, artifact in found.items() if artifact.project_id != project_id]
    if foreign:
        raise PermissionError(f"timeline artifacts belong to another project: {foreign}")


async def create_revision(
    session: AsyncSession, *, project_id: str, episode_id: str, spec: dict[str, Any],
    created_by: Optional[int], parent_revision_id: Optional[str] = None,
    storyboard_revision_id: Optional[str] = None,
) -> TimelineRevision:
    episode = await session.get(Episode, episode_id)
    if episode is None or episode.project_id != project_id:
        raise ValueError("episode not found")
    validated = TimelineSpec.model_validate(spec).model_dump(mode="json")
    # Validate typed clip/cue payloads kept inside extensible track containers.
    video_clips(validated)
    for track in validated.get("audio_tracks", []):
        [AudioClip.model_validate(item) for item in track.get("clips", [])]
    for track in validated.get("subtitle_tracks", []):
        [SubtitleCue.model_validate(item) for item in track.get("cues", [])]
    for track in validated.get("overlay_tracks", []):
        [OverlayClip.model_validate(item) for item in track.get("clips", [])]
    await validate_artifacts(session, project_id, validated)
    revision = int(await session.scalar(select(func.max(TimelineRevision.revision)).where(
        TimelineRevision.episode_id == episode_id
    )) or 0) + 1
    row = TimelineRevision(
        project_id=project_id, episode_id=episode_id, revision=revision,
        parent_revision_id=parent_revision_id, storyboard_revision_id=storyboard_revision_id,
        spec=validated, content_hash=canonical_hash(validated), created_by=created_by,
    )
    session.add(row)
    await session.flush()
    return row


async def plan_from_storyboard(
    session: AsyncSession, *, project_id: str, episode_id: str, created_by: Optional[int]
) -> TimelineRevision:
    shots = (await session.execute(select(Shot).join(Scene, Shot.scene_id == Scene.id).where(
        Scene.episode_id == episode_id
    ).order_by(Scene.sort_order, Shot.sort_order, Shot.shot_no))).scalars().all()
    clips: list[dict[str, Any]] = []
    audio_clips: list[dict[str, Any]] = []
    cues: list[dict[str, Any]] = []
    cursor = 0
    storyboard_revision_id = None
    for shot in shots:
        storyboard_revision_id = storyboard_revision_id or shot.storyboard_revision_id
        if not shot.selected_video_artifact_id:
            continue
        artifact = await session.get(Artifact, shot.selected_video_artifact_id)
        if artifact is None:
            continue
        duration = min(shot.duration_ms, artifact.duration_ms or shot.duration_ms)
        clips.append({
            "id": f"video-{shot.id}", "shot_id": shot.id, "artifact_id": artifact.id,
            "artifact_hash": artifact.content_hash, "source_in_ms": 0,
            "source_out_ms": duration, "timeline_start_ms": cursor, "speed": 1,
            "transition": shot.transition or {},
        })
        for kind, tracks in (("dialogue", shot.dialogue_tracks), ("narration", shot.narration_tracks)):
            for index, item in enumerate(tracks or []):
                audio_artifact = None
                artifact_id = item.get("artifact_id") or item.get("audio_artifact_id")
                if artifact_id:
                    audio_artifact = await session.get(Artifact, artifact_id)
                elif item.get("audio_storage_key"):
                    audio_artifact = (await session.execute(select(Artifact).where(
                        Artifact.project_id == project_id,
                        Artifact.storage_key == item["audio_storage_key"],
                        Artifact.type == "audio",
                    ).limit(1))).scalars().first()
                if audio_artifact:
                    start = max(0, cursor + int(item.get("start_ms") or 0))
                    audio_clips.append({
                        "id": f"{kind}-{shot.id}-{index}", "kind": kind,
                        "artifact_id": audio_artifact.id, "artifact_hash": audio_artifact.content_hash,
                        "source_in_ms": 0, "source_out_ms": audio_artifact.duration_ms,
                        "timeline_start_ms": start,
                        "volume_keyframes": item.get("volume_keyframes") or [],
                        "ducking": item.get("ducking") or {}, "edit": item.get("edit", "normal"),
                    })
        for index, subtitle in enumerate(shot.subtitle_tracks or []):
            text = str(subtitle.get("text") or "").strip()
            if text:
                cues.append({
                    "id": f"subtitle-{shot.id}-{index}", "text": text,
                    "start_ms": cursor + int(subtitle.get("start_ms") or 0),
                    "end_ms": cursor + int(subtitle.get("end_ms") or duration),
                    "style": subtitle.get("style") or {},
                    "safe_area": subtitle.get("safe_area") or {"bottom_percent": 8},
                })
        cursor += duration
    spec = TimelineSpec(
        aspect_ratio=shots[0].aspect_ratio if shots else "9:16",
        video_tracks=[{"id": "video-main", "clips": clips}],
        audio_tracks=[{"id": "audio-main", "clips": audio_clips}],
        subtitle_tracks=[{"id": "subtitle-main", "cues": cues}],
    ).model_dump(mode="json")
    return await create_revision(
        session, project_id=project_id, episode_id=episode_id, spec=spec,
        created_by=created_by, storyboard_revision_id=storyboard_revision_id,
    )


async def list_revisions(session: AsyncSession, episode_id: str) -> list[TimelineRevision]:
    return list((await session.execute(select(TimelineRevision).where(
        TimelineRevision.episode_id == episode_id
    ).order_by(TimelineRevision.revision.desc()))).scalars().all())


async def approve_revision(
    session: AsyncSession, revision: TimelineRevision, *, user_id: Optional[int]
) -> TimelineRevision:
    previous = (await session.execute(select(TimelineRevision).where(
        TimelineRevision.episode_id == revision.episode_id,
        TimelineRevision.status == "approved",
        TimelineRevision.id != revision.id,
    ))).scalars().all()
    for row in previous:
        row.status = "stale"
    approvals = (await session.execute(select(Approval).where(
        Approval.project_id == revision.project_id,
        Approval.target_type == "timeline",
        Approval.target_id == revision.episode_id,
        Approval.status == "approved",
    ))).scalars().all()
    for approval in approvals:
        approval.status = "superseded"
    revision.status = "approved"
    revision.approved_by = user_id
    revision.approved_at = datetime.now(timezone.utc)
    session.add(Approval(
        project_id=revision.project_id, target_type="timeline",
        target_id=revision.episode_id, target_revision=revision.revision,
        status="approved", approved_by=user_id, approved_at=revision.approved_at,
        comment=f"timeline_revision_id={revision.id}",
    ))
    await session.flush()
    return revision


async def get_render_revision(
    session: AsyncSession, episode_id: str, revision_id: Optional[str] = None
) -> Optional[TimelineRevision]:
    if revision_id:
        row = await session.get(TimelineRevision, revision_id)
        return row if row and row.episode_id == episode_id else None
    return (await session.execute(select(TimelineRevision).where(
        TimelineRevision.episode_id == episode_id, TimelineRevision.status == "approved"
    ).order_by(TimelineRevision.revision.desc()).limit(1))).scalars().first()
