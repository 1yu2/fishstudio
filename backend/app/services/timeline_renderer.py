"""时间线合成器（S10）：分镜镜头 → 整集视频。

纯 Python + ffmpeg subprocess（imageio-ffmpeg 自带可执行文件，不依赖系统 ffmpeg、不用 moviepy）。

流程：
1. 取最新 revision 的 shots（Scene.sort_order → Shot.sort_order → shot_no 排序）。
2. 每个 shot 找最新 succeeded 的 generate_video Artifact（job.shot_id 关联）；
   没有产物的镜头用 ffmpeg lavfi 生成 duration_ms 的黑场占位视频（不阻塞整集渲染，
   占位镜头记入 manifest.placeholder_shots）。
3. 每个镜头先 normalize 到统一分辨率/帧率（scale+pad），再用 concat demuxer 顺序拼接。
4. 音频：dialogue_tracks / narration_tracks 里带 "audio_storage_key" 的轨按
   （镜头偏移 + track.start_ms）adelay 后 amix 混到全局时间轴；没有音频文件就跳过
   （MVP：TTS 产物由别的流程生成后回填该字段）。BGM 给定时以 bgm_volume_db 降音量混入，
   整体单遍 loudnorm 响度标准化（sidechain ducking 太复杂，MVP 不做）。
5. 字幕：subtitle_tracks → SRT（全局时间轴）；burn_subtitles 时用 subtitles 滤镜烧录，
   依赖 libass，烧录失败退化为不烧录（manifest.subtitles_burned=False，SRT 单独交付）。
6. 输出 storage/videos/episode_<id>_<uuid8>.mp4 + manifest.json，登记 Artifact。
"""

import asyncio
import json
import logging
import subprocess
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Artifact, GenerationJob, Scene, Shot
from app.services import asset_service, timeline_service
from app.services.storyboard_service import get_current_storyboard

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STORAGE_DIR = BASE_DIR / "storage"
VIDEOS_DIR = STORAGE_DIR / "videos"

DEFAULT_FPS = 24
# 横竖版输出模板（MVP 固定 720p 短边）
ASPECT_SPECS = {
    "9:16": (720, 1280),
    "16:9": (1280, 720),
    "1:1": (720, 720),
}
DEFAULT_ASPECT = "9:16"


# ─────────────────────────────────────────
# 纯函数（便于测试）
# ─────────────────────────────────────────


def compute_shot_offsets(shots: Sequence[Any]) -> list[int]:
    """按顺序累计 duration_ms，返回每个镜头的全局起始偏移 ms。"""
    offsets: list[int] = []
    cursor = 0
    for shot in shots:
        offsets.append(cursor)
        duration = shot.get("duration_ms") if isinstance(shot, dict) else shot.duration_ms
        cursor += max(0, int(duration or 0))
    return offsets


def _tracks_of(shot: Any, key: str) -> list[dict]:
    tracks = shot.get(key) if isinstance(shot, dict) else getattr(shot, key, None)
    return [t for t in (tracks or []) if isinstance(t, dict)]


def _fmt_srt_ts(ms: int) -> str:
    """ms → HH:MM:SS,mmm"""
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def build_srt(shots_with_offsets: Sequence[tuple[Any, int]]) -> str:
    """由 [(shot, offset_ms)] 生成 SRT 文本（全局时间轴：镜头偏移 + start/end_ms）。

    无字幕轨返回空串；非法轨（缺 text / end<=start）跳过。
    """
    entries: list[str] = []
    idx = 0
    for shot, offset in shots_with_offsets:
        for track in _tracks_of(shot, "subtitle_tracks"):
            text = str(track.get("text") or "").strip()
            try:
                start = offset + int(track.get("start_ms") or 0)
                end = offset + int(track.get("end_ms") or 0)
            except (TypeError, ValueError):
                continue
            if not text or end <= start:
                continue
            idx += 1
            entries.append(f"{idx}\n{_fmt_srt_ts(start)} --> {_fmt_srt_ts(end)}\n{text}\n")
    return "\n".join(entries)


def build_timeline_srt(spec: dict[str, Any]) -> str:
    entries: list[str] = []
    index = 0
    for track in spec.get("subtitle_tracks", []):
        for cue in track.get("cues", []):
            text = str(cue.get("text") or "").strip()
            start, end = int(cue.get("start_ms") or 0), int(cue.get("end_ms") or 0)
            if not text or end <= start:
                continue
            index += 1
            entries.append(f"{index}\n{_fmt_srt_ts(start)} --> {_fmt_srt_ts(end)}\n{text}\n")
    return "\n".join(entries)


def build_normalize_filter(width: int, height: int, fps: int) -> str:
    """统一分辨率/帧率的 -vf 链：等比缩放 + 黑边填充 + fps + 方形像素。"""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={fps},setsar=1"
    )


def build_black_placeholder_args(duration_ms: int, width: int, height: int, fps: int) -> list[str]:
    """黑场占位镜头的 lavfi 输入参数（无视频产物的镜头用，不阻塞整集渲染）。"""
    dur = max(1, int(duration_ms)) / 1000.0
    return [
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:size={width}x{height}:rate={fps}:duration={dur:.3f}",
    ]


def build_concat_file_list(paths: Sequence[str]) -> str:
    """ffmpeg concat demuxer 的 list 文件内容。"""
    return "".join(f"file '{p}'\n" for p in paths)


def build_audio_mix_filter(audio_inputs: Sequence[dict[str, Any]], bgm_volume_db: float) -> tuple[str, str]:
    """构造音频混音 filter_complex。

    audio_inputs: [{"input_index": int, "delay_ms": int, "is_bgm": bool}]
    （input_index 为该音频文件在 ffmpeg 命令行中的 -i 序号）
    返回 (filter_complex, mixed_audio_label)。
    """
    parts: list[str] = []
    labels: list[str] = []
    for n, spec in enumerate(audio_inputs):
        label = f"a{n}"
        filters: list[str] = []
        if spec.get("is_bgm"):
            filters.append(f"volume={bgm_volume_db}dB")
        source_in = max(0, int(spec.get("source_in_ms") or 0))
        source_out = spec.get("source_out_ms")
        if source_in or source_out:
            end = f":end={max(source_in + 1, int(source_out)) / 1000:.3f}" if source_out else ""
            filters.append(f"atrim=start={source_in / 1000:.3f}{end}")
            filters.append("asetpts=PTS-STARTPTS")
        if spec.get("volume_db") is not None:
            filters.append(f"volume={float(spec['volume_db']):.2f}dB")
        delay = max(0, int(spec.get("delay_ms") or 0))
        if delay:
            filters.append(f"adelay={delay}|{delay}")
        if not filters:
            # 无任何处理时也必须给出一个合法 filter，否则生成 "[1:a][a0]" 空滤镜
            filters.append("acopy")
        parts.append(f"[{spec['input_index']}:a]" + ",".join(filters) + f"[{label}]")
        labels.append(f"[{label}]")
    mix = "".join(labels) + f"amix=inputs={len(labels)}:duration=longest,loudnorm[aout]"
    return ";".join(parts + [mix]), "[aout]"


# ─────────────────────────────────────────
# ffmpeg 执行
# ─────────────────────────────────────────


def _ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _run_ffmpeg(args: list[str], timeout: int = 600) -> None:
    proc = subprocess.run(
        [_ffmpeg_exe(), "-y", *args],
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", errors="replace")[-2000:]
        raise RuntimeError(f"ffmpeg 失败 (code {proc.returncode}): {tail}")


# ─────────────────────────────────────────
# 数据收集
# ─────────────────────────────────────────


async def _load_shots(session: AsyncSession, episode_id: str) -> list[Shot]:
    """最新 revision 的 shots（Scene.sort_order → Shot.sort_order → shot_no）。

    revision 无 status 过滤：approved 直接用，未审批也允许渲染最新草稿（便于预览）。
    无 revision 时退化为该 episode 全部 shots。
    """
    revision, _ = await get_current_storyboard(session, episode_id)
    stmt = (
        select(Shot)
        .join(Scene, Shot.scene_id == Scene.id)
        .where(Scene.episode_id == episode_id)
        .order_by(Scene.sort_order, Shot.sort_order, Shot.shot_no)
    )
    if revision is not None:
        stmt = stmt.where(Shot.storyboard_revision_id == revision.id)
    return list((await session.execute(stmt)).scalars().all())


async def _latest_video_artifact(session: AsyncSession, shot_id: str) -> Optional[Artifact]:
    """该 shot 最新 succeeded generate_video 任务的视频产物。"""
    stmt = (
        select(Artifact)
        .join(GenerationJob, Artifact.job_id == GenerationJob.id)
        .where(
            GenerationJob.shot_id == shot_id,
            GenerationJob.operation == "generate_video",
            GenerationJob.status == "succeeded",
            Artifact.type == "video",
        )
        .order_by(Artifact.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def _pick_shot_video(session: AsyncSession, shot_id: str) -> tuple[Optional[Artifact], str]:
    """该 shot 的视频产物选择（P2-2 消费侧）：返回 (artifact, selection)。

    selection ∈ selected | latest | placeholder：
    1. selected：Shot.selected_artifact_id 对应的 video artifact（须属于该 shot
       的 job 产物；选定的是别 shot 的产物时忽略并回退）；
    2. latest：最新 succeeded generate_video 产物；
    3. placeholder：无产物（preview 黑场占位 / final 直接失败）。
    """
    from app.services.job_planner import _selected_shot_artifact

    selected = await _selected_shot_artifact(session, shot_id, "video")
    if selected is not None:
        return selected, "selected"
    latest = await _latest_video_artifact(session, shot_id)
    if latest is not None:
        return latest, "latest"
    return None, "placeholder"


# ─────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────


def find_missing_shot_videos(
    shots: Sequence[Any], shot_videos: Sequence[Optional[Artifact]], storage_dir: Path
) -> list[str]:
    """缺视频产物的 shot id 列表（artifact 不存在或文件缺失）。纯函数（P1-11）。"""
    missing: list[str] = []
    for shot, artifact in zip(shots, shot_videos):
        if artifact is None or not (storage_dir / artifact.storage_key).is_file():
            missing.append(shot.id if not isinstance(shot, dict) else shot["id"])
    return missing


async def render_episode(
    session: AsyncSession,
    *,
    project_id: str,
    episode_id: str,
    output_aspect: Optional[str] = None,
    burn_subtitles: bool = True,
    bgm_storage_key: Optional[str] = None,
    bgm_volume_db: float = -18.0,
    render_mode: Literal["preview", "final"] = "preview",
    timeline_revision_id: Optional[str] = None,
    export_profile: Literal["preview", "review", "no-subtitle", "final"] = "preview",
) -> dict:
    """整集渲染：返回 {artifact_id, storage_key, manifest, duration_ms}。

    render_mode（P1-11）：
    - preview：缺产物的镜头用黑场占位，manifest.is_publishable=False 且
      placeholder_shots 记录占位镜头；
    - final：任何镜头缺视频产物直接抛 ValueError（worker finalize 为 failed，
      error_code=missing_shot_video, retryable=False）；全部齐全时
      manifest.is_publishable=True——不允许黑场冒充成片成功。
    """
    if render_mode not in ("preview", "final"):
        raise ValueError(f"非法 render_mode: {render_mode}")
    timeline = (
        await timeline_service.get_render_revision(session, episode_id, timeline_revision_id)
        if timeline_revision_id
        else None
    )
    shots = await _load_shots(session, episode_id)
    if not shots:
        raise ValueError(f"episode {episode_id} 没有可渲染的镜头")
    timeline_clips = timeline_service.video_clips(timeline.spec) if timeline else []
    timeline_artifacts: list[Artifact] = []
    if timeline:
        shot_map = {shot.id: shot for shot in shots}
        rendered_shots: list[Any] = []
        for index, clip in enumerate(timeline_clips):
            artifact = await session.get(Artifact, clip.artifact_id)
            if artifact is None or artifact.project_id != project_id:
                raise ValueError(f"timeline clip artifact not found: {clip.artifact_id}")
            if clip.artifact_hash and artifact.content_hash != clip.artifact_hash:
                raise ValueError(f"timeline clip artifact hash changed: {clip.artifact_id}")
            source = shot_map.get(clip.shot_id or "")
            duration = max(1, round((clip.source_out_ms - clip.source_in_ms) / clip.speed))
            rendered_shots.append(
                SimpleNamespace(
                    id=clip.shot_id or clip.id,
                    shot_no=getattr(source, "shot_no", index + 1),
                    duration_ms=duration,
                    aspect_ratio=getattr(source, "aspect_ratio", "9:16"),
                    dialogue_tracks=[],
                    narration_tracks=[],
                    subtitle_tracks=[],
                )
            )
            timeline_artifacts.append(artifact)
        shots = rendered_shots
        if not shots:
            raise ValueError("TimelineRevision 没有视频 clip")

    # 画幅：参数 > 第一镜头 aspect_ratio > 默认竖版
    aspect = output_aspect or (shots[0].aspect_ratio if shots else "") or DEFAULT_ASPECT
    width, height = ASPECT_SPECS.get(aspect, ASPECT_SPECS[DEFAULT_ASPECT])
    fps = DEFAULT_FPS
    offsets = [clip.timeline_start_ms for clip in timeline_clips] if timeline else compute_shot_offsets(shots)
    total_ms = max(offset + max(0, int(shot.duration_ms or 0)) for shot, offset in zip(shots, offsets))

    # 收集每个镜头的视频产物（选定产物优先，回退最新；来源记入 manifest）
    shot_videos: list[Optional[Artifact]] = []
    artifact_selections: list[str] = []
    if timeline:
        shot_videos = list(timeline_artifacts)
        artifact_selections = ["timeline" for _ in timeline_artifacts]
    else:
        for shot in shots:
            artifact, selection = await _pick_shot_video(session, shot.id)
            shot_videos.append(artifact)
            artifact_selections.append(selection)

    if render_mode == "final":
        missing = find_missing_shot_videos(shots, shot_videos, STORAGE_DIR)
        if missing:
            raise ValueError(f"final 渲染要求所有镜头都有视频产物，缺失 {len(missing)} 个: {missing}")

    placeholder_shots: list[str] = []
    input_keys: list[Optional[str]] = []
    norm_filter = build_normalize_filter(width, height, fps)

    with tempfile.TemporaryDirectory(prefix="timeline_") as tmp:
        tmpdir = Path(tmp)

        # 1) 逐镜头 normalize（缺产物 → 黑场占位）
        normalized: list[str] = []
        for i, (shot, artifact) in enumerate(zip(shots, shot_videos)):
            out = tmpdir / f"shot_{i:03d}.mp4"
            normalized.append(str(out))
            if artifact is not None and (STORAGE_DIR / artifact.storage_key).is_file():
                input_keys.append(artifact.storage_key)
                clip = timeline_clips[i] if timeline else None
                speed_filter = f",setpts=PTS/{clip.speed:.8f}" if clip and clip.speed != 1 else ""
                args = [
                    # -stream_loop -1：素材短于镜头时长时循环补足（-t 只截不补）
                    "-stream_loop",
                    "-1",
                    *(["-ss", f"{clip.source_in_ms / 1000:.3f}"] if clip else []),
                    "-i",
                    str(STORAGE_DIR / artifact.storage_key),
                    "-t",
                    (
                        f"{(clip.source_out_ms - clip.source_in_ms) / 1000:.3f}"
                        if clip
                        else f"{max(1, shot.duration_ms) / 1000:.3f}"
                    ),
                    "-vf",
                    norm_filter + speed_filter,
                    "-an",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(out),
                ]
            else:
                placeholder_shots.append(shot.id)
                input_keys.append(None)
                args = [
                    *build_black_placeholder_args(shot.duration_ms, width, height, fps),
                    "-an",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(out),
                ]
            await asyncio.to_thread(_run_ffmpeg, args)

        # 2) concat demuxer 顺序拼接（规格已统一，-c copy）
        list_file = tmpdir / "concat.txt"
        list_file.write_text(build_concat_file_list(normalized), encoding="utf-8")
        concat_out = tmpdir / "concat.mp4"
        await asyncio.to_thread(
            _run_ffmpeg,
            ["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(concat_out)],
        )

        # 3) 收集音频输入（对白/旁白轨 + BGM）
        audio_files: list[Path] = []
        audio_specs: list[dict[str, Any]] = []
        for shot, offset in zip(shots, offsets):
            for key in ("dialogue_tracks", "narration_tracks"):
                for track in _tracks_of(shot, key):
                    # MVP：TTS 产物由别的流程生成后回填 track["audio_storage_key"]
                    storage_key = track.get("audio_storage_key")
                    if not storage_key:
                        continue
                    path = STORAGE_DIR / storage_key
                    if not path.is_file():
                        continue
                    audio_files.append(path)
                    audio_specs.append(
                        {
                            "input_index": len(audio_files),  # 0 是 concat.mp4
                            "delay_ms": offset + int(track.get("start_ms") or 0),
                            "is_bgm": False,
                        }
                    )
        if timeline:
            for track in timeline.spec.get("audio_tracks", []):
                for clip in track.get("clips", []):
                    artifact = await session.get(Artifact, clip.get("artifact_id"))
                    if artifact is None or artifact.project_id != project_id:
                        raise ValueError(f"timeline audio artifact not found: {clip.get('artifact_id')}")
                    path = STORAGE_DIR / artifact.storage_key
                    if not path.is_file():
                        raise ValueError(f"timeline audio file missing: {artifact.id}")
                    audio_files.append(path)
                    keyframes = clip.get("volume_keyframes") or []
                    audio_specs.append(
                        {
                            "input_index": len(audio_files),
                            "delay_ms": int(clip.get("timeline_start_ms") or 0),
                            "source_in_ms": int(clip.get("source_in_ms") or 0),
                            "source_out_ms": clip.get("source_out_ms"),
                            "volume_db": keyframes[0].get("volume_db", 0) if keyframes else 0,
                            "is_bgm": clip.get("kind") == "bgm",
                            "ducking": clip.get("ducking") or {},
                            "edit": clip.get("edit", "normal"),
                        }
                    )
        bgm_used = False
        if bgm_storage_key:
            bgm_path = STORAGE_DIR / bgm_storage_key
            if bgm_path.is_file():
                audio_files.append(bgm_path)
                audio_specs.append({"input_index": len(audio_files), "delay_ms": 0, "is_bgm": True})
                bgm_used = True
            else:
                logger.warning(f"BGM 文件不存在，跳过: {bgm_storage_key}")

        # 4) 混音 + 可选字幕烧录（libass 不可用时退化为不烧录）
        uid = uuid.uuid4().hex[:8]
        final_path = VIDEOS_DIR / f"episode_{episode_id}_{uid}.mp4"
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

        srt_text = build_timeline_srt(timeline.spec) if timeline else build_srt(list(zip(shots, offsets)))
        srt_rel = f"videos/episode_{episode_id}_sub.srt"
        srt_path = STORAGE_DIR / srt_rel
        subtitles_burned = False

        base_args: list[str] = ["-i", str(concat_out)]
        for p in audio_files:
            base_args += ["-i", str(p)]

        def _encode_args(burn: bool) -> list[str]:
            args = list(base_args)
            filters: list[str] = []
            maps: list[str] = []
            if audio_specs:
                fc, aout = build_audio_mix_filter(audio_specs, bgm_volume_db)
                filters.append(fc)
                maps += ["-map", aout]
            if burn:
                filters.append(f"[0:v]subtitles={srt_path}[vout]")
                maps += ["-map", "[vout]"]
            else:
                maps += ["-map", "0:v"]
            if filters:
                args += ["-filter_complex", ";".join(filters)]
            args += maps + ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
            if audio_specs:
                args += ["-c:a", "aac", "-shortest"]
            else:
                args += ["-an"]
            args += [str(final_path)]
            return args

        profile = timeline.spec.get("export_profiles", {}).get(export_profile, {}) if timeline else {}
        burn_subtitles = bool(profile.get("burn_subtitles", burn_subtitles))
        want_burn = burn_subtitles and bool(srt_text.strip())
        if want_burn:
            srt_path.write_text(srt_text, encoding="utf-8")
            try:
                await asyncio.to_thread(_run_ffmpeg, _encode_args(burn=True))
                subtitles_burned = True
            except Exception as e:
                # ffmpeg 未编译 libass 等情况：退化为不烧录，SRT 单独交付
                logger.warning(f"字幕烧录失败，退化为不烧录: {e}")
        if not subtitles_burned:
            await asyncio.to_thread(_run_ffmpeg, _encode_args(burn=False))

    # 5) SRT 与 manifest 建档
    if srt_text.strip():
        srt_path.write_text(srt_text, encoding="utf-8")
        await asset_service.create_artifact(
            session,
            project_id=project_id,
            type="subtitle",
            storage_key=srt_rel,
            mime_type="application/x-subrip",
            meta={"episode_id": episode_id, "burned": subtitles_burned},
        )

    # final 模式前面已校验齐全；is_publishable 仅 final 且无占位镜头时为 True
    is_publishable = render_mode == "final" and not placeholder_shots
    video_artifact = await asset_service.create_artifact(
        session,
        project_id=project_id,
        type="video",
        storage_key=f"videos/{final_path.name}",
        mime_type="video/mp4",
        duration_ms=total_ms,
        width=width,
        height=height,
        meta={
            "episode_id": episode_id,
            "subtitles_burned": subtitles_burned,
            "render_mode": render_mode,
            "is_publishable": is_publishable,
            "placeholder_shots": placeholder_shots,
        },
    )

    manifest = {
        "episode_id": episode_id,
        "project_id": project_id,
        "timeline_revision_id": timeline.id if timeline else None,
        "timeline_revision": timeline.revision if timeline else None,
        "timeline_content_hash": timeline.content_hash if timeline else None,
        "export_profile": export_profile,
        "render_fingerprint": timeline_service.canonical_hash(
            {
                "timeline_content_hash": timeline.content_hash if timeline else "legacy",
                "artifacts": [
                    {"id": artifact.id, "hash": artifact.content_hash}
                    for artifact in shot_videos
                    if artifact is not None
                ],
                "profile": export_profile,
                "aspect": aspect,
                "fps": fps,
            }
        ),
        "render_mode": render_mode,
        "is_publishable": is_publishable,
        "output_aspect": aspect,
        "resolution": {"width": width, "height": height},
        "fps": fps,
        "duration_ms": total_ms,
        "shots": [
            {
                "shot_id": shot.id,
                "shot_no": shot.shot_no,
                "duration_ms": shot.duration_ms,
                "offset_ms": offsets[i],
                "storage_key": input_keys[i],
                "placeholder": input_keys[i] is None,
                # 产物来源：selected（用户选定）| latest（最新成功）| placeholder（黑场）
                "artifact_selection": artifact_selections[i],
                "artifact_id": shot_videos[i].id if shot_videos[i] else None,
                "artifact_hash": shot_videos[i].content_hash if shot_videos[i] else "",
                "source_in_ms": timeline_clips[i].source_in_ms if timeline else 0,
                "source_out_ms": timeline_clips[i].source_out_ms if timeline else shot.duration_ms,
                "speed": timeline_clips[i].speed if timeline else 1,
                "transition": timeline_clips[i].transition if timeline else {},
            }
            for i, shot in enumerate(shots)
        ],
        "placeholder_shots": placeholder_shots,
        "subtitles": {
            "srt_storage_key": srt_rel if srt_text.strip() else None,
            "subtitles_burned": subtitles_burned,
        },
        "bgm": {"storage_key": bgm_storage_key if bgm_used else None, "volume_db": bgm_volume_db},
        "audio_tracks": timeline.spec.get("audio_tracks", []) if timeline else [],
        "subtitle_tracks": timeline.spec.get("subtitle_tracks", []) if timeline else [],
        "overlay_tracks": timeline.spec.get("overlay_tracks", []) if timeline else [],
        "export": {"codec": "h264/aac", "container": "mp4", "renderer": "ffmpeg"},
    }
    manifest_rel = f"videos/episode_{episode_id}_manifest.json"
    (STORAGE_DIR / manifest_rel).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    await asset_service.create_artifact(
        session,
        project_id=project_id,
        type="document",
        storage_key=manifest_rel,
        mime_type="application/json",
        meta={"episode_id": episode_id, "kind": "render_manifest"},
    )

    return {
        "artifact_id": video_artifact.id,
        "storage_key": video_artifact.storage_key,
        "manifest": manifest,
        "duration_ms": total_ms,
    }
