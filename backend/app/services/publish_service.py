"""Final QC 与人工 Publish Package 生成。不会向外部平台执行发布。"""

import asyncio
import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Artifact,
    ArtifactCandidate,
    Episode,
    Publication,
    PublishPackage,
    QCReport,
    Scene,
    SeriesProject,
    Shot,
    TimelineRevision,
)
from app.services import asset_service, timeline_service
from app.services.timeline_renderer import STORAGE_DIR, _ffmpeg_exe


def _check(code: str, passed: bool, *, severity: str = "error", detail: str = "", target: Optional[dict] = None) -> dict[str, Any]:
    return {"code": code, "passed": passed, "severity": severity, "detail": detail, "target": target or {}}


def evaluate_manifest(
    *, artifact: Artifact, timeline: TimelineRevision, manifest: dict[str, Any],
    media_probe: dict[str, Any], require_publishable: bool = True,
) -> tuple[list[dict], list[dict], list[dict]]:
    """对已收集的媒体证据做纯确定性 QC，便于离线测试与复跑。"""
    technical: list[dict] = []
    semantic: list[dict] = []
    technical.append(_check("decode", bool(media_probe.get("decodable")), detail=media_probe.get("decode_error", ""), target={"artifact_id": artifact.id}))
    technical.append(_check("placeholder_or_black", not manifest.get("placeholder_shots") and not media_probe.get("black_detected"), detail="检测到占位镜头或连续黑帧", target={"artifact_id": artifact.id}))
    technical.append(_check("static_frame", not media_probe.get("freeze_detected"), detail="检测到异常长静帧", target={"artifact_id": artifact.id}))
    technical.append(_check("resolution", bool(artifact.width and artifact.height and min(artifact.width, artifact.height) >= 720), detail=f"{artifact.width}x{artifact.height}", target={"artifact_id": artifact.id}))
    fps = int(timeline.spec.get("fps") or 0)
    technical.append(_check("frame_rate", fps in {24, 25, 30, 50, 60}, detail=f"fps={fps}", target={"timeline_revision_id": timeline.id}))
    loudness = media_probe.get("loudness_lufs")
    technical.append(_check("loudness", loudness is None or -24 <= loudness <= -12, severity="warning" if loudness is None else "error", detail="无法检测响度" if loudness is None else f"{loudness:.1f} LUFS", target={"artifact_id": artifact.id}))
    expected_duration = max((int(c.timeline_start_ms + (c.source_out_ms - c.source_in_ms) / c.speed) for c in timeline_service.video_clips(timeline.spec)), default=0)
    actual_duration = int(artifact.duration_ms or 0)
    technical.append(_check("av_sync", actual_duration > 0 and abs(actual_duration - expected_duration) <= 500, detail=f"render={actual_duration}ms timeline={expected_duration}ms", target={"artifact_id": artifact.id, "timeline_revision_id": timeline.id}))
    subtitle_ok = True
    bad_cue = None
    for track in timeline.spec.get("subtitle_tracks", []):
        for cue in track.get("cues", []):
            bottom = float((cue.get("safe_area") or {}).get("bottom_percent", 8))
            if bottom < 5 or bottom > 20:
                subtitle_ok, bad_cue = False, cue.get("id")
                break
    technical.append(_check("subtitle_safe_area", subtitle_ok, detail="字幕超出安全区" if not subtitle_ok else "", target={"track_or_cue_id": bad_cue} if bad_cue else {}))

    clips = timeline_service.video_clips(timeline.spec)
    semantic.append(_check("timeline_approved", timeline.status == "approved", target={"timeline_revision_id": timeline.id}))
    semantic.append(_check("shot_order", all(clips[i].timeline_start_ms <= clips[i + 1].timeline_start_ms for i in range(len(clips) - 1)), target={"timeline_revision_id": timeline.id}))
    semantic.append(_check("narrative_complete", len(clips) > 0 and all(clip.shot_id for clip in clips), detail=f"clips={len(clips)}", target={"timeline_revision_id": timeline.id}))
    semantic.append(_check("platform_spec", timeline.spec.get("aspect_ratio") == "9:16", detail=f"aspect={timeline.spec.get('aspect_ratio')}", target={"timeline_revision_id": timeline.id}))
    semantic.append(_check("render_publishable", not require_publishable or manifest.get("is_publishable") is True, target={"artifact_id": artifact.id}))
    issues = [item for item in technical + semantic if not item["passed"]]
    return technical, semantic, issues


def _probe_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"decodable": False, "decode_error": "file missing"}
    decode = subprocess.run([_ffmpeg_exe(), "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, timeout=600)
    evidence = subprocess.run([
        _ffmpeg_exe(), "-v", "info", "-i", str(path),
        "-vf", "blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-50dB:d=2",
        "-af", "ebur128=peak=true", "-f", "null", "-",
    ], capture_output=True, timeout=600)
    stderr = evidence.stderr.decode("utf-8", errors="replace")
    loudness_matches = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", stderr)
    return {
        "decodable": decode.returncode == 0,
        "decode_error": decode.stderr.decode("utf-8", errors="replace")[-500:] if decode.returncode else "",
        "black_detected": "black_start:" in stderr,
        "freeze_detected": "freeze_start:" in stderr,
        "loudness_lufs": float(loudness_matches[-1]) if loudness_matches else None,
    }


async def latest_render(
    session: AsyncSession, project_id: str, episode_id: str, *, final_only: bool = True
) -> Optional[Artifact]:
    artifacts = (await session.execute(select(Artifact).where(
        Artifact.project_id == project_id, Artifact.type == "video"
    ).order_by(Artifact.created_at.desc()))).scalars().all()
    return next((a for a in artifacts if (
        (a.artifact_metadata or {}).get("episode_id") == episode_id
        and (not final_only or (a.artifact_metadata or {}).get("render_mode") == "final")
    )), None)


async def run_qc(
    session: AsyncSession, *, project_id: str, episode_id: str, render_artifact_id: Optional[str],
    phase: str = "final_qc", created_by: Optional[int] = None,
) -> QCReport:
    timeline = await timeline_service.get_render_revision(session, episode_id)
    if timeline is None:
        raise ValueError("final QC requires an approved TimelineRevision")
    artifact = await session.get(Artifact, render_artifact_id) if render_artifact_id else await latest_render(
        session, project_id, episode_id, final_only=phase == "final_qc"
    )
    if artifact is None and phase == "director_review":
        clips = timeline_service.video_clips(timeline.spec)
        artifact = await session.get(Artifact, clips[0].artifact_id) if clips else None
    if artifact is None or artifact.project_id != project_id:
        raise ValueError("final render artifact not found")
    manifest = dict(artifact.artifact_metadata or {})
    manifest_path = STORAGE_DIR / f"videos/episode_{episode_id}_manifest.json"
    if manifest_path.is_file() and (artifact.artifact_metadata or {}).get("episode_id") == episode_id:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    probe = await asyncio.to_thread(_probe_file, STORAGE_DIR / artifact.storage_key)
    technical, semantic, issues = evaluate_manifest(
        artifact=artifact, timeline=timeline, manifest=manifest, media_probe=probe,
        require_publishable=phase == "final_qc",
    )

    ordered_shots = (await session.execute(select(Shot).join(Scene, Shot.scene_id == Scene.id).where(
        Scene.episode_id == episode_id, Shot.selected_video_artifact_id.is_not(None)
    ).order_by(Scene.sort_order, Shot.sort_order, Shot.shot_no))).scalars().all()
    expected_order = [shot.id for shot in ordered_shots]
    actual_order = [clip.shot_id for clip in timeline_service.video_clips(timeline.spec)]
    order_check = _check(
        "storyboard_shot_order", actual_order == expected_order,
        detail=f"expected={expected_order} actual={actual_order}",
        target={"shot_ids": actual_order},
    )
    semantic.append(order_check)
    if not order_check["passed"]:
        issues.append(order_check)

    candidate_ids = [clip.artifact_id for clip in timeline_service.video_clips(timeline.spec)]
    rejected = (await session.execute(select(ArtifactCandidate).where(
        ArtifactCandidate.artifact_id.in_(candidate_ids), ArtifactCandidate.status == "rejected"
    ))).scalars().all() if candidate_ids else []
    if rejected:
        issue = _check("character_or_continuity", False, detail="Timeline 使用了已淘汰候选", target={"artifact_ids": [row.artifact_id for row in rejected]})
        semantic.append(issue)
        issues.append(issue)
    repairs = [{
        "issue_code": item["code"], "target": item["target"],
        "return_to": "timeline.plan" if item["code"] in {"shot_order", "storyboard_shot_order", "narrative_complete", "subtitle_safe_area", "av_sync"} else "shot.video.generate" if item["code"] in {"placeholder_or_black", "static_frame", "character_or_continuity"} else "episode.render.final",
    } for item in issues]
    blocking = [
        item for item in issues
        if item["severity"] == "error"
        and (phase == "final_qc" or item["code"] in {"decode", "shot_order", "storyboard_shot_order", "narrative_complete", "platform_spec", "character_or_continuity"})
    ]
    score = max(0.0, 1.0 - len(blocking) * 0.15 - (len(issues) - len(blocking)) * 0.05)
    report = QCReport(
        project_id=project_id, episode_id=episode_id, timeline_revision_id=timeline.id,
        render_artifact_id=artifact.id, phase=phase, status="passed" if not blocking else "failed",
        score=score, technical_checks=technical, semantic_checks=semantic,
        issues=issues, repair_targets=repairs, created_by=created_by,
    )
    session.add(report)
    await session.flush()
    return report


async def _cover_artifact(session: AsyncSession, project_id: str, episode_id: str, video: Artifact) -> Artifact:
    first_shot = (await session.execute(select(Shot).join(Scene, Shot.scene_id == Scene.id).where(
        Scene.episode_id == episode_id, Shot.selected_image_artifact_id.is_not(None)
    ).order_by(Scene.sort_order, Shot.sort_order).limit(1))).scalars().first()
    if first_shot and first_shot.selected_image_artifact_id:
        cover = await session.get(Artifact, first_shot.selected_image_artifact_id)
        if cover:
            return cover
    name = f"publish_cover_{episode_id}_{uuid.uuid4().hex[:8]}.jpg"
    rel = f"images/{name}"
    path = STORAGE_DIR / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(subprocess.run, [_ffmpeg_exe(), "-y", "-ss", "0", "-i", str(STORAGE_DIR / video.storage_key), "-frames:v", "1", str(path)], capture_output=True, timeout=120, check=True)
    return await asset_service.create_artifact(session, project_id=project_id, type="image", storage_key=rel, mime_type="image/jpeg", meta={"episode_id": episode_id, "kind": "publish_cover"})


async def create_publish_package(
    session: AsyncSession, *, project_id: str, episode_id: str, platform: str = "xiaohongshu",
    title: str = "", description: str = "", tags: Optional[list[str]] = None,
    created_by: Optional[int] = None,
) -> tuple[Publication, PublishPackage]:
    report = (await session.execute(select(QCReport).where(
        QCReport.project_id == project_id, QCReport.episode_id == episode_id,
        QCReport.phase == "final_qc", QCReport.status == "passed",
    ).order_by(QCReport.created_at.desc()).limit(1))).scalars().first()
    if report is None:
        raise ValueError("final QC 未通过，不能生成发布包")
    video = await session.get(Artifact, report.render_artifact_id)
    timeline = await session.get(TimelineRevision, report.timeline_revision_id)
    episode = await session.get(Episode, episode_id)
    project = await session.get(SeriesProject, project_id)
    if video is None or timeline is None or episode is None or project is None:
        raise ValueError("publish package inputs missing")
    cover = await _cover_artifact(session, project_id, episode_id, video)
    publication = Publication(
        project_id=project_id, episode_id=episode_id, timeline_revision_id=timeline.id,
        render_artifact_id=video.id, cover_artifact_id=cover.id, qc_report_id=report.id,
        platform=platform, title=title or episode.title or project.title,
        description=description or f"{project.title} · 第 {episode.index} 集",
        tags=tags or ["AI短剧", project.title],
        specifications={"aspect_ratio": timeline.spec.get("aspect_ratio"), "fps": timeline.spec.get("fps"), "manual_publish_only": True},
        status="ready_to_publish", created_by=created_by,
    )
    session.add(publication)
    await session.flush()
    manifest = {
        "schema_version": 1, "publication_id": publication.id, "platform": platform,
        "timeline_revision_id": timeline.id, "timeline_content_hash": timeline.content_hash,
        "render_artifact_id": video.id, "render_content_hash": video.content_hash,
        "cover_artifact_id": cover.id, "cover_content_hash": cover.content_hash,
        "qc_report_id": report.id, "title": publication.title,
        "description": publication.description, "tags": publication.tags,
        "specifications": publication.specifications,
    }
    rel = f"documents/publish_package_{publication.id}_v1.json"
    path = STORAGE_DIR / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    artifact = await asset_service.create_artifact(session, project_id=project_id, type="document", storage_key=rel, mime_type="application/json", meta={"kind": "publish_package_manifest", "publication_id": publication.id})
    package = PublishPackage(publication_id=publication.id, version=1, manifest_artifact_id=artifact.id, package_metadata=manifest, status="ready")
    session.add(package)
    await session.flush()
    return publication, package


async def list_publications(session: AsyncSession, project_id: str) -> list[Publication]:
    return list((await session.execute(select(Publication).where(
        Publication.project_id == project_id
    ).order_by(Publication.created_at.desc()))).scalars().all())
