"""参考视频拉片、人工修订与 Shot 选择性引用领域服务。"""

from __future__ import annotations

import re
import subprocess
import uuid
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Artifact,
    Episode,
    InvalidationEvent,
    ReferenceAnalysis,
    Scene,
    Shot,
    ShotReference,
)

ALLOWED_REFERENCE_TYPES = {"composition", "motion", "timing"}
ALLOWED_AUTHORIZATIONS = {"owned", "licensed", "public_domain"}


def probe_reference_video(path: str) -> tuple[int, list[int]]:
    """读取真实时长与场景变化切点；工具不可用时由上传链路安全回退。"""
    duration_result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, timeout=30, check=True,
    )
    duration_ms = round(float(duration_result.stdout.strip()) * 1000)
    scene_result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path, "-filter:v", "select=gt(scene\\,0.30),showinfo", "-an", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120, check=False,
    )
    boundaries = sorted({
        round(float(value) * 1000)
        for value in re.findall(r"pts_time:([0-9]+(?:\.[0-9]+)?)", scene_result.stderr)
        if 0 < float(value) * 1000 < duration_ms
    })
    return duration_ms, boundaries


def build_deterministic_segments(
    duration_ms: int, boundaries_ms: Optional[list[int]] = None
) -> list[dict[str, Any]]:
    """无模型时也可编辑的稳定基线；长片按约 4 秒拆段。"""
    duration_ms = max(1, int(duration_ms))
    boundaries = sorted({int(value) for value in (boundaries_ms or []) if 0 < int(value) < duration_ms})
    intervals = list(zip([0, *boundaries], [*boundaries, duration_ms]))
    if not boundaries:
        count = max(1, (duration_ms + 3999) // 4000)
        intervals = [
            (round(index * duration_ms / count), round((index + 1) * duration_ms / count))
            for index in range(count)
        ]
    segments = []
    for index, (start, end) in enumerate(intervals):
        segments.append({
            "key": f"segment-{index + 1}",
            "start_ms": start,
            "end_ms": end,
            "shot_size": "unknown",
            "camera_movement": "static",
            "composition": "",
            "action_intervals": [{"start_ms": start, "end_ms": end, "action": "待人工标注"}],
            "emotion_curve": [{"at_ms": start, "value": 0.5}, {"at_ms": end, "value": 0.5}],
            "keyframes_ms": sorted({start, (start + end) // 2, max(start, end - 1)}),
            "confidence": 0.35,
        })
    return segments


def validate_segments(segments: list[dict[str, Any]], duration_ms: int) -> list[dict[str, Any]]:
    keys: set[str] = set()
    normalized = []
    for index, raw in enumerate(segments):
        item = dict(raw)
        key = str(item.get("key") or f"segment-{index + 1}")
        start, end = int(item.get("start_ms", 0)), int(item.get("end_ms", 0))
        if key in keys or start < 0 or end <= start or end > duration_ms:
            raise ValueError(f"invalid reference segment: {key}")
        keys.add(key)
        item.update(key=key, start_ms=start, end_ms=end)
        normalized.append(item)
    if not normalized:
        raise ValueError("reference analysis requires at least one segment")
    return normalized


async def create_analysis(
    session: AsyncSession,
    *,
    project_id: str,
    artifact_id: str,
    duration_ms: int,
    source_uri: str,
    authorization: str,
    usage_scope: str = "director_language_only",
    created_by: Optional[int] = None,
    prompt: str = "",
    model_snapshot: Optional[dict[str, Any]] = None,
    usage_snapshot: Optional[dict[str, Any]] = None,
    estimated_cost: Optional[Decimal] = None,
) -> ReferenceAnalysis:
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None or artifact.project_id != project_id or artifact.type != "video":
        raise ValueError("reference video artifact 不存在或不属于当前项目")
    if authorization not in ALLOWED_AUTHORIZATIONS:
        raise ValueError("参考素材必须声明 owned、licensed 或 public_domain 授权")
    duration_ms = int(duration_ms or artifact.duration_ms or 0)
    if duration_ms <= 0:
        raise ValueError("无法识别视频时长，请人工提供 duration_ms")
    latest = await session.scalar(select(func.max(ReferenceAnalysis.revision)).where(ReferenceAnalysis.artifact_id == artifact_id))
    row = ReferenceAnalysis(
        project_id=project_id,
        artifact_id=artifact_id,
        revision=int(latest or 0) + 1,
        duration_ms=duration_ms,
        segments=build_deterministic_segments(
            duration_ms,
            list((artifact.artifact_metadata or {}).get("scene_boundaries_ms") or []),
        ),
        confidence=0.35,
        prompt=prompt,
        model_snapshot=model_snapshot or {"mode": "deterministic", "version": "reference-v1"},
        usage_snapshot=usage_snapshot or {"input_units": 0, "output_units": 0, "currency": "CNY"},
        estimated_cost=estimated_cost or Decimal("0"),
        source_uri=source_uri,
        authorization=authorization,
        usage_scope=usage_scope,
        created_by=created_by,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def revise_analysis(
    session: AsyncSession,
    analysis_id: str,
    segments: list[dict[str, Any]],
    *,
    created_by: Optional[int] = None,
) -> ReferenceAnalysis:
    previous = await session.get(ReferenceAnalysis, analysis_id)
    if previous is None:
        raise ValueError("reference analysis not found")
    normalized = validate_segments(segments, previous.duration_ms)
    revision = int(await session.scalar(select(func.max(ReferenceAnalysis.revision)).where(ReferenceAnalysis.artifact_id == previous.artifact_id)) or 0) + 1
    previous.status = "superseded"
    row = ReferenceAnalysis(
        project_id=previous.project_id, artifact_id=previous.artifact_id, revision=revision,
        parent_revision_id=previous.id, duration_ms=previous.duration_ms, segments=normalized,
        confidence=sum(float(item.get("confidence", 1)) for item in normalized) / len(normalized),
        prompt=previous.prompt, model_snapshot={**(previous.model_snapshot or {}), "human_revised": True},
        usage_snapshot=previous.usage_snapshot, estimated_cost=previous.estimated_cost,
        source_uri=previous.source_uri, authorization=previous.authorization,
        usage_scope=previous.usage_scope, created_by=created_by,
    )
    session.add(row)
    refs = list((await session.execute(select(ShotReference).where(ShotReference.analysis_id == previous.id))).scalars())
    affected = []
    for ref in refs:
        shot = await session.get(Shot, ref.shot_id)
        if shot is not None:
            shot.status = "stale"
            affected.append({"entity_type": "shot", "entity_id": shot.id, "stage": "reference"})
    if affected:
        session.add(InvalidationEvent(
            project_id=previous.project_id, idempotency_key=f"reference:{previous.id}:{revision}:{uuid.uuid4().hex}",
            source_type="reference_analysis", source_id=previous.id, source_revision=previous.revision,
            changed_fields=["segments"], reason="reference_analysis_revised", affected_entities=affected,
            created_by=created_by,
        ))
    await session.commit()
    await session.refresh(row)
    return row


async def list_analyses(session: AsyncSession, project_id: str) -> list[ReferenceAnalysis]:
    return list((await session.execute(select(ReferenceAnalysis).where(ReferenceAnalysis.project_id == project_id).order_by(ReferenceAnalysis.created_at.desc()))).scalars())


async def bind_segment(
    session: AsyncSession, *, analysis_id: str, segment_key: str, shot_id: str,
    reference_type: str, weight: float = 1, replace_subject: bool = True,
    created_by: Optional[int] = None,
) -> ShotReference:
    if reference_type not in ALLOWED_REFERENCE_TYPES:
        raise ValueError("reference_type 必须是 composition、motion 或 timing")
    if not 0 < weight <= 2:
        raise ValueError("weight 必须在 0~2 之间")
    analysis = await session.get(ReferenceAnalysis, analysis_id)
    shot = await session.get(Shot, shot_id)
    if analysis is None or shot is None:
        raise ValueError("analysis 或 shot 不存在")
    scene = await session.get(Scene, shot.scene_id)
    episode = await session.get(Episode, scene.episode_id) if scene else None
    if episode is None or episode.project_id != analysis.project_id:
        raise PermissionError("analysis 与 shot 不属于同一项目")
    segment = next((item for item in analysis.segments if item.get("key") == segment_key), None)
    if segment is None:
        raise ValueError("reference segment 不存在")
    existing = (await session.execute(select(ShotReference).where(
        ShotReference.shot_id == shot_id, ShotReference.analysis_id == analysis_id,
        ShotReference.segment_key == segment_key, ShotReference.reference_type == reference_type,
    ))).scalars().first()
    if existing:
        return existing
    ref = ShotReference(
        project_id=analysis.project_id, shot_id=shot_id, analysis_id=analysis.id,
        analysis_revision=analysis.revision, segment_key=segment_key, reference_type=reference_type,
        segment_snapshot=dict(segment), weight=weight, replace_subject=replace_subject,
        created_by=created_by,
    )
    session.add(ref)
    shot.status = "stale"
    await session.commit()
    await session.refresh(ref)
    return ref
