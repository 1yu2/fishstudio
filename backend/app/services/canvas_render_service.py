"""画布成片：由画布 graph 生成渲染用 EDL，并提供成片页数据、渲染任务与 BGM 上传。

- build_plan：镜头（canvas_shots）+ 各节点选用的素材 → EDL、阻止渲染的 problems、warnings、
  input_hash（选用素材 + 成片设置的稳定哈希，用于“素材已变更”提示）与只读时间线数据。
- 渲染任务 kind = render_canvas：EDL 在提交时生成并存进 job.params，Worker 只按它渲染，
  保证 input_hash 与产物对得上；产物登记在成片节点名下。
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Artifact, GenerationJob, NodeCanvas
from app.services import asset_service, job_service, timeline_service
from app.services import node_canvas_service as canvas_service
from app.services.canvas_shots import canvas_shots
from app.services.node_canvas_service import CanvasConflict, CanvasError

RENDER_OPERATION = "render_canvas"
NARRATOR = "旁白"
BGM_FADE_MS = 1500
BGM_DUCK_DB = 14
BGM_MAX_BYTES = 20 * 1024 * 1024


class RenderBlocked(CanvasError):
    """有阻止渲染的问题；problems 是结构化列表。"""

    def __init__(self, problems: list[dict[str, Any]]):
        super().__init__("；".join(item["message"] for item in problems) or "还不能渲染")
        self.problems = problems


@dataclass
class Plan:
    spec: dict[str, Any]
    settings: dict[str, Any]
    input_hash: str
    total_ms: int
    problems: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    timeline: dict[str, Any] = field(default_factory=dict)


def find_episode_node(graph: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    return next((node for node in (graph or {}).get("nodes", []) if node.get("type") == "episode"), None)


def _issue(code: str, message: str, shot: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "message": message}
    if shot is not None:
        item.update(shot_index=shot["index"], shot_id=shot["main_id"])
    return item


# ── 素材 ──────────────────────────────────────────────────────────────────


async def _node_artifacts(session: AsyncSession, canvas: Any) -> dict[str, list[Artifact]]:
    """节点 id → 它的结果素材（成功任务的产物 + 上传到该节点的素材），最新在前。"""
    node_id = GenerationJob.params["canvas_node_id"].astext
    succeeded = {
        job_id: node
        for job_id, node in (
            await session.execute(
                select(GenerationJob.id, node_id).where(
                    GenerationJob.project_id == canvas.project_id,
                    GenerationJob.status == "succeeded",
                    node_id.isnot(None),
                )
            )
        ).all()
    }
    artifacts = (
        (
            await session.execute(
                select(Artifact)
                .where(Artifact.project_id == canvas.project_id)
                .order_by(Artifact.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, list[Artifact]] = {}
    for artifact in artifacts:
        meta = artifact.artifact_metadata or {}
        if artifact.job_id in succeeded:
            out.setdefault(succeeded[artifact.job_id], []).append(artifact)
        elif not artifact.job_id and meta.get("source") == "canvas_upload" and meta.get("canvas_node_id"):
            out.setdefault(meta["canvas_node_id"], []).append(artifact)
    return out


def _pick(
    node: Optional[dict[str, Any]], results: dict[str, list[Artifact]], expected: str
) -> Optional[Artifact]:
    """节点选用的结果：用户点选的（仍在结果里），否则最新成功的。"""
    if node is None:
        return None
    items = [item for item in results.get(node["id"], []) if item.type == expected]
    chosen = (node.get("data") or {}).get("selected")
    return next((item for item in items if item.id == chosen), items[0] if items else None)


def _duration_ms(artifact: Artifact) -> Optional[int]:
    if artifact.duration_ms and artifact.duration_ms > 0:
        return int(artifact.duration_ms)
    value = (artifact.artifact_metadata or {}).get("duration_ms")
    return int(value) if isinstance(value, (int, float)) and value > 0 else None


async def _audio_length(artifact: Artifact) -> Optional[int]:
    """配音时长：优先用建档时记录的，没有就读文件。"""
    known = _duration_ms(artifact)
    if known:
        return known
    path = canvas_service.STORAGE_DIR / artifact.storage_key
    if not path.is_file():
        return None
    try:
        return (await asyncio.to_thread(_probe_audio, path))[0]
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return None


def _audio_text(node: dict[str, Any], nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    """台词：有文本节点连入 prompt 时取文本节点内容，否则取 data.text。"""
    upstream = [
        str((nodes[edge["source"]].get("data") or {}).get("text") or "").strip()
        for edge in edges
        if edge.get("target") == node["id"]
        and edge.get("targetHandle") == "prompt"
        and nodes.get(edge.get("source"), {}).get("type") == "text"
    ]
    upstream = [item for item in upstream if item]
    return "\n".join(upstream) if upstream else str((node.get("data") or {}).get("text") or "").strip()


# ── EDL ───────────────────────────────────────────────────────────────────


async def build_plan(session: AsyncSession, canvas: Any, episode_node: Optional[dict[str, Any]]) -> Plan:
    graph = canvas.graph or {}
    nodes = {node["id"]: node for node in graph.get("nodes", [])}
    edges = graph.get("edges", [])
    settings = canvas_service.episode_settings((episode_node or {}).get("data"))
    shots = canvas_shots(graph)
    results = await _node_artifacts(session, canvas)

    problems: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    video_clips: list[dict[str, Any]] = []
    voice_clips: list[dict[str, Any]] = []
    cues: list[dict[str, Any]] = []
    view_shots: list[dict[str, Any]] = []
    view_voices: list[dict[str, Any]] = []
    cursor = 0
    if not shots:
        problems.append(_issue("no_shots", "还没有镜头：先到分镜里添加镜头"))

    for shot in shots:
        number = shot["index"] + 1
        video_art = _pick(nodes.get(shot["video_id"] or ""), results, "video")
        frame_art = _pick(nodes.get(shot["frame_id"] or ""), results, "image")
        still = video_art is None and frame_art is not None
        clip_art = video_art or frame_art
        length = (_duration_ms(video_art) if video_art else None) or shot["duration_s"] * 1000
        if clip_art is None:
            problems.append(_issue("missing_video", f"镜头 {number} 还没有视频", shot))
        else:
            if still and shot["video_id"]:
                warnings.append(
                    _issue("still_frame", f"镜头 {number} 还没有视频，成片里会用首帧静帧代替", shot)
                )
            video_clips.append(
                {
                    "id": f"shot-{number}",
                    "artifact_id": clip_art.id,
                    "artifact_hash": clip_art.content_hash or "",
                    "source_in_ms": 0,
                    "source_out_ms": length,
                    "timeline_start_ms": cursor,
                    "speed": 1,
                }
            )
        thumb = frame_art or (clip_art if clip_art is not None and clip_art.type == "image" else None)
        view_shots.append(
            {
                "index": shot["index"],
                "shot_id": shot["main_id"],
                "title": shot["title"],
                "start_ms": cursor,
                "end_ms": cursor + length,
                "has_video": clip_art is not None,
                "still": still,
                "thumbnail_url": asset_service.artifact_url(thumb) if thumb is not None else None,
            }
        )

        used = 0
        shot_end = cursor + length
        overflow = False
        for position, audio_id in enumerate(shot["audio_ids"]):
            node = nodes[audio_id]
            data = node.get("data") or {}
            text = _audio_text(node, nodes, edges)
            artifact = _pick(node, results, "audio")
            speaker = str(data.get("speaker") or "").strip()
            if artifact is None:
                warnings.append(
                    _issue(
                        "missing_voice",
                        f"镜头 {number} 的台词「{text[:12]}」还没有配音，成片里没有这句",
                        shot,
                    )
                )
                continue
            duration = await _audio_length(artifact)
            if not duration:
                warnings.append(_issue("voice_unreadable", f"镜头 {number} 的配音读取不到时长，已跳过", shot))
                continue
            start = cursor + used
            used += duration
            keep = min(duration, shot_end - start)
            if keep < duration:
                overflow = True
            if keep <= 0:
                continue
            voice_clips.append(
                {
                    "id": f"voice-{number}-{position + 1}",
                    "kind": "narration" if speaker == NARRATOR else "dialogue",
                    "artifact_id": artifact.id,
                    "artifact_hash": artifact.content_hash or "",
                    "source_in_ms": 0,
                    "source_out_ms": keep,
                    "timeline_start_ms": start,
                    "label": f"镜头 {number} 的配音「{text[:10]}」",
                }
            )
            if text:
                cues.append(
                    {
                        "id": f"sub-{number}-{position + 1}",
                        "start_ms": start,
                        "end_ms": start + keep,
                        "text": text,
                    }
                )
            view_voices.append(
                {
                    "node_id": audio_id,
                    "shot_index": shot["index"],
                    "speaker": speaker,
                    "kind": "narration" if speaker == NARRATOR else "dialogue",
                    "text": text,
                    "start_ms": start,
                    "end_ms": start + keep,
                }
            )
        if overflow:
            warnings.append(
                _issue(
                    "voice_overflow",
                    f"镜头 {number} 的配音（{used / 1000:.1f} 秒）比镜头（{length / 1000:.1f} 秒）长，超出的部分会被截断",
                    shot,
                )
            )
        cursor += length

    total_ms = cursor
    bgm_clip: Optional[dict[str, Any]] = None
    view_bgm: Optional[dict[str, Any]] = None
    if settings["bgm_artifact_id"]:
        bgm = await session.get(Artifact, settings["bgm_artifact_id"])
        if bgm is None or bgm.project_id != canvas.project_id or bgm.type != "audio":
            warnings.append(_issue("bgm_missing", "背景音乐文件找不到，已忽略"))
        else:
            fade = BGM_FADE_MS if settings["fades"] else 0
            bgm_clip = {
                "id": "bgm",
                "kind": "bgm",
                "artifact_id": bgm.id,
                "artifact_hash": bgm.content_hash or "",
                "source_in_ms": 0,
                "timeline_start_ms": 0,
                "volume_keyframes": [{"time_ms": 0, "volume_db": float(settings["bgm_volume_db"])}],
                "ducking": {"enabled": True, "duck_db": BGM_DUCK_DB} if settings["ducking"] else {},
                "fade_in_ms": fade,
                "fade_out_ms": fade,
                "label": "背景音乐",
            }
            view_bgm = {
                "artifact_id": bgm.id,
                "name": (bgm.artifact_metadata or {}).get("filename") or "背景音乐",
                "start_ms": 0,
                "end_ms": total_ms,
            }

    audio_tracks = []
    if voice_clips:
        audio_tracks.append({"id": "voice", "clips": voice_clips})
    if bgm_clip:
        audio_tracks.append({"id": "bgm", "clips": [bgm_clip]})
    spec = {
        "aspect_ratio": canvas.aspect_ratio,
        "video_tracks": [{"id": "video", "clips": video_clips}],
        "audio_tracks": audio_tracks,
        "subtitle_tracks": [{"id": "subtitle", "cues": cues}] if cues else [],
    }
    input_hash = timeline_service.canonical_hash(
        {
            "spec": spec,
            "settings": {key: settings[key] for key in ("burn_subtitles", "export_srt", "resolution")},
        }
    )
    return Plan(
        spec=spec,
        settings=settings,
        input_hash=input_hash,
        total_ms=total_ms,
        problems=problems,
        warnings=warnings,
        timeline={
            "shots": view_shots,
            "voices": view_voices,
            "subtitles": [{k: cue[k] for k in ("start_ms", "end_ms", "text")} for cue in cues],
            "bgm": view_bgm,
        },
    )


async def build_spec(
    session: AsyncSession, canvas: Any, episode_node: Optional[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """(EDL, 阻止渲染的 problems, input_hash)。"""
    plan = await build_plan(session, canvas, episode_node)
    return plan.spec, plan.problems, plan.input_hash


async def current_input_hash(session: AsyncSession, canvas: Any) -> str:
    return (await build_plan(session, canvas, find_episode_node(canvas.graph))).input_hash


# ── 最新渲染 / 成片页数据 ──────────────────────────────────────────────────


def _render_entry(job: GenerationJob) -> dict[str, Any]:
    status, _ = canvas_service._display_status(job)
    result = job.result or {}
    artifact_id = result.get("artifact_id") if job.status == "succeeded" else None
    srt_id = result.get("srt_artifact_id") if job.status == "succeeded" else None
    return {
        "job_id": job.id,
        "status": status,
        "progress": job.progress or 0,
        "error": (job.error or "")[:300],
        "artifact_id": artifact_id,
        "url": f"/api/artifacts/{artifact_id}/download" if artifact_id else None,
        "srt_url": f"/api/artifacts/{srt_id}/download" if srt_id else None,
        "input_hash": (job.params or {}).get("input_hash"),
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


async def render_jobs(session: AsyncSession, canvas: Any, limit: int = 30) -> list[GenerationJob]:
    """这张画布的成片渲染任务，最新在前。"""
    return list(
        (
            await session.execute(
                select(GenerationJob)
                .where(
                    GenerationJob.project_id == canvas.project_id,
                    GenerationJob.operation == RENDER_OPERATION,
                    GenerationJob.params["canvas_id"].astext == canvas.id,
                )
                .order_by(GenerationJob.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def final_state(session: AsyncSession, canvas: Any) -> dict[str, Any]:
    node = find_episode_node(canvas.graph)
    plan = await build_plan(session, canvas, node)
    jobs = await render_jobs(session, canvas)
    latest = _render_entry(jobs[0]) if jobs else None
    success_job = next((job for job in jobs if job.status == "succeeded"), None)
    last_success = _render_entry(success_job) if success_job else None
    return {
        "episode_node_id": node["id"] if node else None,
        "settings": plan.settings,
        "timeline": plan.timeline,
        "total_ms": plan.total_ms,
        "problems": plan.problems,
        "warnings": plan.warnings,
        "input_hash": plan.input_hash,
        "latest_render": latest,
        # 最近一次成功的渲染（最新一次在跑或失败时，页面仍用它预览和导出）
        "last_success": last_success,
        "stale": bool(last_success and last_success["input_hash"] != plan.input_hash),
    }


# ── 成片节点 / 渲染任务 ────────────────────────────────────────────────────


async def ensure_episode_node(
    session: AsyncSession, canvas: NodeCanvas, base_revision: int
) -> dict[str, Any]:
    """没有成片节点时在所有镜头右侧建一个（默认设置）；已有则原样返回，不改版本。"""
    await session.refresh(canvas, with_for_update=True)
    existing = find_episode_node(canvas.graph)
    if existing is not None:
        return {"episode_node_id": existing["id"], "revision": canvas.revision}
    if base_revision != canvas.revision:
        raise CanvasConflict("画布已在其他窗口更新，请刷新后再编辑")
    graph = dict(canvas.graph or {})
    nodes = list(graph.get("nodes", []))
    right = max((node["position"]["x"] + (node.get("width") or 320) for node in nodes), default=0)
    ys = [node["position"]["y"] for node in nodes if node.get("type") == "video"] or [
        node["position"]["y"] for node in nodes
    ]
    node_id = f"episode-{uuid.uuid4().hex[:8]}"
    nodes.append(
        {
            "id": node_id,
            "type": "episode",
            "position": {"x": right + 200, "y": min(ys) if ys else 0},
            "data": {"label": "成片 · 第 1 集", **canvas_service.EPISODE_DEFAULTS},
        }
    )
    graph["nodes"] = nodes
    canvas.graph = graph
    canvas.revision += 1
    await session.flush()
    return {"episode_node_id": node_id, "revision": canvas.revision}


async def start_render(
    session: AsyncSession, canvas: NodeCanvas, *, base_revision: int, client_request_id: Optional[str]
) -> GenerationJob:
    """创建成片渲染任务。有 problems 抛 RenderBlocked；画布版本过期抛 CanvasConflict。"""
    # 同一个 client_request_id 的重试直接返回已有任务（不重新快照：期间素材可能已变）
    key = (
        f"canvas-render:{canvas.id}:{client_request_id}"
        if client_request_id
        else f"canvas-render:{canvas.id}:{uuid.uuid4().hex}"
    )
    if client_request_id:
        existing = await canvas_service._existing_job_for_key(session, key)
        if existing is not None:
            return existing
    if base_revision != canvas.revision:
        raise CanvasConflict("画布已更新，请刷新页面后再渲染")
    node = find_episode_node(canvas.graph)
    if node is None:
        raise CanvasError("还没有成片节点，请先打开成片页")
    plan = await build_plan(session, canvas, node)
    if plan.problems:
        raise RenderBlocked(plan.problems)
    return await job_service.create_job(
        session,
        operation=RENDER_OPERATION,
        project_id=canvas.project_id,
        params={
            "canvas_id": canvas.id,
            "canvas_node_id": node["id"],
            "input_hash": plan.input_hash,
            "spec": plan.spec,
            "burn_subtitles": plan.settings["burn_subtitles"],
            "export_srt": plan.settings["export_srt"],
            "resolution": plan.settings["resolution"],
        },
        idempotency_key=key,
        worker_class="render",
    )


# ── BGM 上传 ───────────────────────────────────────────────────────────────

_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def sniff_audio(data: bytes) -> Optional[tuple[str, str]]:
    """按文件内容判断音频类型 → (扩展名, mime)；不认识返回 None。不信任 Content-Type 与文件名。"""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav", "audio/wav"
    if data[4:8] == b"ftyp" and data[8:12] in (b"M4A ", b"M4B ", b"mp42", b"mp41", b"isom", b"iso2"):
        return "m4a", "audio/mp4"
    if data[:3] == b"ID3":
        return "mp3", "audio/mpeg"
    if len(data) > 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0:
        # MPEG 音频帧同步字；layer 位为 00 的是 ADTS（AAC）
        if data[1] & 0xF6 == 0xF0:
            return "aac", "audio/aac"
        if (data[1] >> 1) & 0x03 != 0:
            return "mp3", "audio/mpeg"
    return None


def _probe_audio(path: Path) -> tuple[int, bool]:
    """(时长 ms, 是否带画面)；读不出时长抛 RuntimeError。"""
    from app.services.timeline_renderer import _ffmpeg_exe

    proc = subprocess.run(
        [_ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    text = proc.stderr or ""
    match = _DURATION.search(text)
    if not match or "Audio:" not in text:
        raise RuntimeError("无法读取音频")
    hours, minutes, seconds = match.groups()
    has_video = any("Video:" in line and "attached pic" not in line for line in text.splitlines())
    return round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000), has_video


async def upload_bgm(
    session: AsyncSession, canvas: NodeCanvas, *, filename: str, data: bytes
) -> dict[str, Any]:
    if not data:
        raise CanvasError("文件是空的")
    if len(data) > BGM_MAX_BYTES:
        raise CanvasError(f"背景音乐不能超过 {BGM_MAX_BYTES // (1024 * 1024)}MB")
    kind = sniff_audio(data)
    if kind is None:
        raise CanvasError("只支持 MP3 / WAV / M4A / AAC 音频文件")
    ext, mime = kind
    relative = Path("uploads") / "canvas" / canvas.id / f"bgm-{uuid.uuid4().hex}.{ext}"
    target = canvas_service.STORAGE_DIR / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    try:
        duration_ms, has_video = await asyncio.to_thread(_probe_audio, target)
        if has_video:
            raise RuntimeError("带画面")
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        target.unlink(missing_ok=True)
        raise CanvasError("这个文件不是可以播放的音频") from error
    name = (filename or "背景音乐")[:200]
    artifact = await asset_service.create_artifact(
        session,
        project_id=canvas.project_id,
        type="audio",
        storage_key=relative.as_posix(),
        mime_type=mime,
        duration_ms=duration_ms,
        meta={"source": "bgm_upload", "filename": name, "canvas_id": canvas.id},
    )
    return {"artifact_id": artifact.id, "name": name, "duration_ms": duration_ms}
