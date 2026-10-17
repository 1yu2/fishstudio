"""从结构化 Shot + 锁定资产 + Reference Slots 重建版本化 Prompt/模型输入。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Artifact, AssetCard, Shot, ShotAssetRef, ShotReference

PROMPT_COMPILER_VERSION = "shot-prompt-v1"


@dataclass(frozen=True)
class CompiledShotInput:
    prompt: str
    prompt_version: str
    input_hash: str
    reference_slots: list[dict[str, Any]]
    first_frame: Optional[str] = None
    last_frame: Optional[str] = None
    reference_images: tuple[str, ...] = ()
    reference_videos: tuple[str, ...] = ()
    reference_audios: tuple[str, ...] = ()
    source_video: Optional[str] = None


async def compile_shot_input(session: AsyncSession, shot: Shot) -> CompiledShotInput:
    refs = list(
        (
            await session.execute(
                select(ShotAssetRef)
                .where(ShotAssetRef.shot_id == shot.id)
                .order_by(ShotAssetRef.sort_order, ShotAssetRef.id)
            )
        )
        .scalars()
        .all()
    )
    slots: list[dict[str, Any]] = []
    profile_parts: list[str] = []
    first_frame = last_frame = source_video = None
    images: list[str] = []
    videos: list[str] = []
    audios: list[str] = []
    for ref in refs:
        asset = await session.get(AssetCard, ref.asset_id)
        if asset is None or asset.version != ref.asset_version:
            raise ValueError(f"reference asset version mismatch: {ref.asset_id}@{ref.asset_version}")
        artifact = await session.get(Artifact, ref.artifact_id) if ref.artifact_id else None
        storage_key = artifact.storage_key if artifact else None
        slot = {
            "type": ref.reference_type,
            "asset_id": asset.id,
            "asset_version": ref.asset_version,
            "artifact_id": ref.artifact_id,
            "subject": ref.subject,
            "weight": ref.weight,
            "sort_order": ref.sort_order,
            "usage": ref.usage,
        }
        slots.append(slot)
        profile_parts.append(
            f"{asset.type}:{asset.name}@v{asset.version}={json.dumps(asset.profile or {}, ensure_ascii=False, sort_keys=True)}"
        )
        if storage_key:
            if ref.reference_type == "first_frame":
                first_frame = storage_key
            elif ref.reference_type == "last_frame":
                last_frame = storage_key
            elif ref.reference_type == "source_video":
                source_video = storage_key
            elif ref.reference_type in {"character", "scene", "style", "composition"}:
                images.append(storage_key)
            elif ref.reference_type == "motion":
                videos.append(storage_key)
            elif ref.reference_type == "audio":
                audios.append(storage_key)
    analysis_refs = list(
        (
            await session.execute(
                select(ShotReference)
                .where(ShotReference.shot_id == shot.id)
                .order_by(ShotReference.created_at, ShotReference.id)
            )
        )
        .scalars()
        .all()
    )
    for ref in analysis_refs:
        segment = ref.segment_snapshot or {}
        # 只编译导演语言，不把参考视频或原主体作为模型输入。
        slots.append(
            {
                "type": ref.reference_type,
                "analysis_id": ref.analysis_id,
                "analysis_revision": ref.analysis_revision,
                "segment_key": ref.segment_key,
                "weight": ref.weight,
                "replace_subject": ref.replace_subject,
                "director_language": segment,
            }
        )
        if ref.reference_type == "motion":
            profile_parts.append(
                f"镜头运动参考：{segment.get('camera_movement', '')}，区间 {segment.get('start_ms')}~{segment.get('end_ms')}ms"
            )
        elif ref.reference_type == "timing":
            profile_parts.append(
                f"节奏参考：动作区间 {json.dumps(segment.get('action_intervals', []), ensure_ascii=False)}"
            )
        else:
            profile_parts.append(
                f"构图参考：{segment.get('composition', '')}；景别 {segment.get('shot_size', '')}"
            )
        if ref.replace_subject:
            profile_parts.append("必须使用本项目角色与场景，禁止复制参考片人物身份、服装和场景外观")
    prompt = "\n".join(
        filter(
            None, [getattr(shot, "visual_prompt", ""), getattr(shot, "narrative_purpose", ""), *profile_parts]
        )
    )
    canonical = json.dumps(
        {"prompt": prompt, "slots": slots, "version": PROMPT_COMPILER_VERSION},
        ensure_ascii=False,
        sort_keys=True,
    )
    return CompiledShotInput(
        prompt=prompt,
        prompt_version=PROMPT_COMPILER_VERSION,
        input_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        reference_slots=slots,
        first_frame=first_frame,
        last_frame=last_frame,
        reference_images=tuple(images),
        reference_videos=tuple(videos),
        reference_audios=tuple(audios),
        source_video=source_video,
    )
