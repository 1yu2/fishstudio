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
   整体单遍 loudnorm 响度标准化（-14 LUFS）。EDL 路径的 BGM 支持循环 / 截断到全片长度、
   首尾淡入淡出，以及在人声区间用 volume 逐帧表达式压低（ducking）。
5. 字幕：subtitle_tracks → SRT（全局时间轴）；burn_subtitles 时用 subtitles 滤镜烧录，
   依赖 libass，烧录失败退化为不烧录（manifest.subtitles_burned=False，SRT 单独交付）。
6. 输出 storage/videos/episode_<id>_<uuid8>.mp4 + manifest.json，登记 Artifact。
"""

import asyncio
import json
import logging
import math
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Literal, Optional, Sequence

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
# 清晰度档位 → 输出短边
SHORT_SIDE = {"720p": 720, "1080p": 1080}
ProgressCallback = Callable[[int], Awaitable[None]]

# AIGC 显式标识：半透明“AI 生成”角标 PNG（带透明背景），ffmpeg overlay 叠到右上角，运行时不依赖字体
AIGC_BADGE_PATH = BASE_DIR / "app" / "assets" / "aigc_badge.png"
BADGE_HEIGHT_RATIO = 0.065  # 角标高度占画面短边的比例（宽约为高的 2.6 倍）
BADGE_MARGIN_RATIO = 0.03  # 距右上角的安全边距占短边的比例；字幕在底部，不会相撞


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


# 成片响度目标 -14 LUFS、真峰值上限 -1.5 dBTP。LRA 放宽到 30：先测量再按固定增益线性归一
# （见 _measure_loudness），不让 loudnorm 的动态模式把被压低的 BGM 段又拉回去
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=30"
# 压低 BGM 的深度（dB）：比无人声段低 14 dB，留出余量满足“至少低 10 dB”
DEFAULT_DUCK_DB = 14.0
DUCK_RAMP_S = 0.2
BGM_FADE_MS = 1500


def output_size(aspect: str, short_side: int = 720) -> tuple[int, int]:
    """画幅 + 短边 → (宽, 高)，宽高取偶数（yuv420p 要求）。短边 720 时与 ASPECT_SPECS 一致。"""
    if short_side == 720 and aspect in ASPECT_SPECS:
        return ASPECT_SPECS[aspect]
    try:
        w, h = (int(part) for part in aspect.split(":"))
    except ValueError:
        return ASPECT_SPECS[DEFAULT_ASPECT] if short_side == 720 else output_size(DEFAULT_ASPECT, short_side)
    if w >= h:
        height, width = short_side, short_side * w / h
    else:
        width, height = short_side, short_side * h / w
    return round(width / 2) * 2, round(height / 2) * 2


def merge_ranges(ranges: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """合并重叠 / 相邻的区间（ms），丢弃空区间。"""
    merged: list[list[int]] = []
    for start, end in sorted((s, e) for s, e in ranges if e > s):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def _max_tree(terms: list[str]) -> str:
    """把多个表达式用 max() 平衡嵌套，避免表达式嵌套过深。"""
    if len(terms) == 1:
        return terms[0]
    mid = len(terms) // 2
    return f"max({_max_tree(terms[:mid])},{_max_tree(terms[mid:])})"


def build_duck_expression(ranges_ms: Sequence[tuple[int, int]], depth_db: float = DEFAULT_DUCK_DB) -> str:
    """volume 滤镜的逐帧增益表达式：区间内压低 depth_db dB，区间边缘各 DUCK_RAMP_S 秒平滑过渡。

    ranges_ms 是以该音频起点为 0 的人声区间；没有区间时返回 "1"（不压低）。
    """
    merged = merge_ranges(ranges_ms)
    if not merged:
        return "1"
    r = DUCK_RAMP_S
    terms = [
        f"min(clip((t-{s / 1000 - r:.3f})/{r},0,1),clip(({e / 1000 + r:.3f}-t)/{r},0,1))" for s, e in merged
    ]
    depth = 1 - 10 ** (-abs(depth_db) / 20)
    return f"1-{depth:.4f}*{_max_tree(terms)}"


def _fade_filters(total_s: float, fade_in_ms: int, fade_out_ms: int) -> list[str]:
    filters: list[str] = []
    half = max(total_s / 2, 0.05)
    if fade_in_ms:
        filters.append(f"afade=t=in:st=0:d={min(fade_in_ms / 1000, half):.3f}")
    if fade_out_ms:
        d = min(fade_out_ms / 1000, half)
        filters.append(f"afade=t=out:st={max(0.0, total_s - d):.3f}:d={d:.3f}")
    return filters


def build_audio_mix_filter(
    audio_inputs: Sequence[dict[str, Any]],
    bgm_volume_db: float,
    total_ms: Optional[int] = None,
    loudnorm: str = LOUDNORM,
) -> tuple[str, str]:
    """构造音频混音 filter_complex。

    audio_inputs: [{"input_index": int, "delay_ms": int, "is_bgm": bool, ...}]
    （input_index 为该音频文件在 ffmpeg 命令行中的 -i 序号）
    可选键：source_in_ms / source_out_ms / volume_db / fade_in_ms / fade_out_ms /
    duck_ranges（clip 内部时间的人声区间 ms）/ duck_db / loop（输入已 -stream_loop，需截到全片长度）。
    没有 volume_db 的 BGM 用 bgm_volume_db。total_ms 给定时，输出补静音 / 截断到恰好这个长度。
    loudnorm 是响度归一滤镜字符串（默认单遍动态；两遍线性归一时由调用方带上测量值）。
    返回 (filter_complex, mixed_audio_label)。
    """
    parts: list[str] = []
    labels: list[str] = []
    total_s = (total_ms or 0) / 1000
    for n, spec in enumerate(audio_inputs):
        label = f"a{n}"
        filters: list[str] = []
        delay = max(0, int(spec.get("delay_ms") or 0))
        source_in = max(0, int(spec.get("source_in_ms") or 0))
        source_out = spec.get("source_out_ms")
        if source_in or source_out:
            end = f":end={max(source_in + 1, int(source_out)) / 1000:.3f}" if source_out else ""
            filters.append(f"atrim=start={source_in / 1000:.3f}{end}")
            filters.append("asetpts=PTS-STARTPTS")
        if spec.get("loop") and total_ms:
            # 循环输入是无限长：截到“全片长度 - 起点”，之后淡出才对得上片尾
            filters.append(f"atrim=end={max(0.05, total_s - delay / 1000):.3f}")
            filters.append("asetpts=PTS-STARTPTS")
        gain = spec.get("volume_db")
        if gain is None and spec.get("is_bgm"):
            gain = bgm_volume_db
        if gain is not None:
            filters.append(f"volume={float(gain)}dB")
        if total_ms and (spec.get("fade_in_ms") or spec.get("fade_out_ms")):
            filters += _fade_filters(
                max(0.05, total_s - delay / 1000),
                int(spec.get("fade_in_ms") or 0),
                int(spec.get("fade_out_ms") or 0),
            )
        if spec.get("duck_ranges"):
            expr = build_duck_expression(spec["duck_ranges"], float(spec.get("duck_db") or DEFAULT_DUCK_DB))
            filters.append(f"volume=volume='{expr}':eval=frame:precision=float")
        if delay:
            filters.append(f"adelay={delay}|{delay}")
        if not filters:
            # 无任何处理时也必须给出一个合法 filter，否则生成 "[1:a][a0]" 空滤镜
            filters.append("acopy")
        parts.append(f"[{spec['input_index']}:a]" + ",".join(filters) + f"[{label}]")
        labels.append(f"[{label}]")
    # normalize=0：各路按原电平相加（对白之间不重叠，BGM 压低深度才可预期），整体电平交给 loudnorm
    tail = f"{loudnorm},aresample=48000"
    if total_ms:
        tail += f",apad=whole_dur={total_s:.3f},atrim=end={total_s:.3f}"
    mix = "".join(labels) + f"amix=inputs={len(labels)}:duration=longest:normalize=0,{tail}[aout]"
    return ";".join(parts + [mix]), "[aout]"


def badge_geometry(width: int, height: int) -> tuple[int, int]:
    """角标高度与边距（像素）：随画面短边缩放，高度取偶数。"""
    short = min(width, height)
    return max(2, round(short * BADGE_HEIGHT_RATIO / 2) * 2), max(2, round(short * BADGE_MARGIN_RATIO))


def build_badge_filter(video_in: str, badge_index: int, width: int, height: int, out: str = "[vout]") -> str:
    """把角标（第 badge_index 个输入）缩放后叠到 video_in 的右上角；返回 filter_complex 片段。"""
    badge_h, margin = badge_geometry(width, height)
    return (
        f"[{badge_index}:v]scale=-2:{badge_h}[badge];"
        f"{video_in}[badge]overlay=x=W-w-{margin}:y={margin}:format=auto{out}"
    )


def build_aigc_metadata(produce_id: str, reserved_code1: str = "") -> dict[str, dict[str, str]]:
    """AIGC 隐式标识（《人工智能生成合成内容标识办法》元数据要素）。"""
    return {
        "AIGC": {
            "Label": "1",
            "ContentProducer": "FishStudio",
            "ProduceID": produce_id,
            "ReservedCode1": reserved_code1,
            "ContentPropagator": "",
            "PropagateID": "",
            "ReservedCode2": "",
        }
    }


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


def _measure_loudness(args: list[str]) -> Optional[dict[str, str]]:
    """跑一遍只输出音频的 loudnorm 分析，返回测量值；全静音 / 解析失败返回 None（调用方退回单遍动态）。"""
    proc = subprocess.run([_ffmpeg_exe(), "-y", *args], capture_output=True, timeout=600)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", errors="replace")[-2000:]
        raise RuntimeError(f"ffmpeg 失败 (code {proc.returncode}): {tail}")
    text = proc.stderr.decode("utf-8", errors="replace")
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", text)
    if not blocks:
        return None
    try:
        stats = json.loads(blocks[-1])
        if not all(
            math.isfinite(float(stats[key])) for key in ("input_i", "input_tp", "input_lra", "input_thresh")
        ):
            return None
        return {
            key: str(stats[key])
            for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
        }
    except (ValueError, KeyError, TypeError):
        return None


def build_linear_loudnorm(stats: dict[str, str]) -> str:
    """用测量值构造两遍线性 loudnorm（固定增益，保持 BGM 压低的电平差）。"""
    return (
        f"{LOUDNORM}:measured_I={stats['input_i']}:measured_TP={stats['input_tp']}"
        f":measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}"
        f":offset={stats['target_offset']}:linear=true"
    )


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
    return await _render(
        session,
        project_id=project_id,
        episode_id=episode_id,
        shots=shots,
        timeline=timeline,
        output_aspect=output_aspect,
        burn_subtitles=burn_subtitles,
        bgm_storage_key=bgm_storage_key,
        bgm_volume_db=bgm_volume_db,
        render_mode=render_mode,
        export_profile=export_profile,
    )


async def render_timeline(
    session: AsyncSession,
    *,
    project_id: str,
    spec: dict[str, Any],
    output_aspect: Optional[str] = None,
    burn_subtitles: bool = True,
    bgm_storage_key: Optional[str] = None,
    bgm_volume_db: float = -18.0,
    render_mode: Literal["preview", "final"] = "preview",
    episode_id: Optional[str] = None,
    short_side: int = 720,
    export_srt: bool = True,
    job_id: Optional[str] = None,
    artifact_meta: Optional[dict[str, Any]] = None,
    on_progress: Optional[ProgressCallback] = None,
    aigc_label: bool = False,
    aigc_reserved_code: str = "",
) -> dict:
    """直接渲染 EDL，不依赖 Shot；可附带画布对应的 episode 归属。

    short_side：输出短边（720 / 1080）；export_srt=False 时 SRT 不单独保存（烧录失败退化时仍会保存）；
    job_id 写到视频产物上；artifact_meta 并入视频 / SRT 产物的 metadata（如 canvas_node_id）；
    on_progress(0..100) 在各阶段结束时回调；aigc_label=True 时全程叠加“AI 生成”角标，并把隐式标识
    写进 MP4 元数据（ProduceID 取 job_id，没有则取输出文件名；ReservedCode1 取 aigc_reserved_code）。
    """
    if render_mode not in ("preview", "final"):
        raise ValueError(f"非法 render_mode: {render_mode}")
    validated = timeline_service.validate_spec(spec)
    timeline = SimpleNamespace(
        spec=validated,
        id=None,
        revision=None,
        content_hash=timeline_service.canonical_hash(validated),
    )
    return await _render(
        session,
        project_id=project_id,
        episode_id=episode_id,
        shots=[],
        timeline=timeline,
        output_aspect=output_aspect or validated.get("aspect_ratio") or DEFAULT_ASPECT,
        burn_subtitles=burn_subtitles,
        bgm_storage_key=bgm_storage_key,
        bgm_volume_db=bgm_volume_db,
        render_mode=render_mode,
        export_profile="preview",
        source="edl",
        short_side=short_side,
        export_srt=export_srt,
        job_id=job_id,
        artifact_meta=artifact_meta,
        on_progress=on_progress,
        aigc_label=aigc_label,
        aigc_reserved_code=aigc_reserved_code,
    )


def _filter_path(path: Path) -> str:
    """滤镜参数里的文件路径转义（\\ ' : , [ ] ;）。"""
    out = str(path)
    for char in ("\\", "'", ":", ",", "[", "]", ";"):
        out = out.replace(char, "\\" + char)
    return out


def _audio_duration_ms(artifact: Artifact) -> Optional[int]:
    if artifact.duration_ms:
        return int(artifact.duration_ms)
    value = (artifact.artifact_metadata or {}).get("duration_ms")
    return int(value) if isinstance(value, (int, float)) and value > 0 else None


async def _render(
    session: AsyncSession,
    *,
    project_id: str,
    episode_id: Optional[str],
    shots: Sequence[Any],
    timeline: Any,
    output_aspect: Optional[str],
    burn_subtitles: bool,
    bgm_storage_key: Optional[str],
    bgm_volume_db: float,
    render_mode: str,
    export_profile: str,
    source: Optional[str] = None,
    short_side: int = 720,
    export_srt: bool = True,
    job_id: Optional[str] = None,
    artifact_meta: Optional[dict[str, Any]] = None,
    on_progress: Optional[ProgressCallback] = None,
    aigc_label: bool = False,
    aigc_reserved_code: str = "",
) -> dict:
    """共用 normalize、拼接、混音、字幕和产物封装流程。"""
    if not shots and not timeline:
        raise ValueError(f"episode {episode_id} 没有可渲染的镜头")

    async def progress(value: int) -> None:
        if on_progress is not None:
            await on_progress(value)

    timeline_clips = timeline_service.video_clips(timeline.spec) if timeline else []
    timeline_artifacts: list[Artifact] = []
    # 时间线上每个镜头的实际起点（间隙补黑场、重叠顺延）与它前面要补的黑场时长
    effective_starts: list[int] = []
    gaps: list[int] = []
    if timeline:
        if not timeline_clips:
            raise ValueError("TimelineRevision 没有视频 clip")
        try:
            await timeline_service.validate_artifacts(session, project_id, timeline.spec)
        except PermissionError as exc:
            raise ValueError(str(exc)) from exc
        shot_map = {shot.id: shot for shot in shots}
        rendered_shots: list[Any] = []
        cursor = 0
        for index, clip in enumerate(timeline_clips):
            artifact = await session.get(Artifact, clip.artifact_id)
            shot_source = shot_map.get(clip.shot_id or "")
            duration = max(1, round((clip.source_out_ms - clip.source_in_ms) / clip.speed))
            start = max(clip.timeline_start_ms, cursor)
            gaps.append(start - cursor)
            effective_starts.append(start)
            cursor = start + duration
            rendered_shots.append(
                SimpleNamespace(
                    id=clip.shot_id or clip.id,
                    shot_no=getattr(shot_source, "shot_no", index + 1),
                    duration_ms=duration,
                    aspect_ratio=getattr(shot_source, "aspect_ratio", "9:16"),
                    dialogue_tracks=[],
                    narration_tracks=[],
                    subtitle_tracks=[],
                )
            )
            timeline_artifacts.append(artifact)
        shots = rendered_shots

    # 画幅：参数 > 第一镜头 aspect_ratio > 默认竖版
    aspect = output_aspect or (shots[0].aspect_ratio if shots else "") or DEFAULT_ASPECT
    width, height = output_size(aspect, short_side)
    fps = (timeline.spec.get("fps") if timeline else None) or DEFAULT_FPS
    offsets = effective_starts if timeline else compute_shot_offsets(shots)
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
            if source == "edl":
                numbers = [
                    str(i + 1) for i, shot in enumerate(shots) if shot.id in missing or shot_videos[i] is None
                ]
                raise ValueError(f"镜头 {'、'.join(numbers)} 的视频文件不存在")
            raise ValueError(f"final 渲染要求所有镜头都有视频产物，缺失 {len(missing)} 个: {missing}")

    # 音频输入：先于任何 ffmpeg 编码收集并检查文件，缺文件时不浪费视频编码时间
    audio_files: list[Path] = []
    audio_specs: list[dict[str, Any]] = []
    voice_ranges: list[tuple[int, int]] = []
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
                label = clip.get("label") or "音频"
                if artifact is None or artifact.project_id != project_id:
                    raise ValueError(f"timeline audio artifact not found: {clip.get('artifact_id')}")
                path = STORAGE_DIR / artifact.storage_key
                if not path.is_file():
                    if source == "edl":
                        raise ValueError(f"{label}的音频文件不存在")
                    raise ValueError(f"timeline audio file missing: {artifact.id}")
                audio_files.append(path)
                keyframes = clip.get("volume_keyframes") or []
                kind = clip.get("kind")
                start = int(clip.get("timeline_start_ms") or 0)
                source_in = int(clip.get("source_in_ms") or 0)
                source_out = clip.get("source_out_ms")
                length = (
                    int(source_out) - source_in
                    if source_out
                    else (_audio_duration_ms(artifact) or 0) - source_in
                )
                if kind in ("dialogue", "narration") and length > 0:
                    voice_ranges.append((start, start + length))
                ducking = clip.get("ducking") or {}
                audio_specs.append(
                    {
                        "input_index": len(audio_files),
                        "delay_ms": start,
                        "source_in_ms": source_in,
                        "source_out_ms": source_out,
                        "volume_db": keyframes[0].get("volume_db", 0) if keyframes else None,
                        "is_bgm": kind == "bgm",
                        # BGM 比全片短时循环（输入层 -stream_loop），比全片长时在滤镜里截断
                        "loop": kind == "bgm",
                        "fade_in_ms": int(clip.get("fade_in_ms") or 0),
                        "fade_out_ms": int(clip.get("fade_out_ms") or 0),
                        "duck_enabled": bool(ducking.get("enabled")),
                        "duck_db": ducking.get("duck_db"),
                        "ducking": ducking,
                        "edit": clip.get("edit", "normal"),
                    }
                )
    for spec_item in audio_specs:
        if spec_item.pop("duck_enabled", False):
            start = spec_item["delay_ms"]
            spec_item["duck_ranges"] = [
                (max(0, s - start), e - start) for s, e in merge_ranges(voice_ranges) if e > start
            ]
    bgm_used = False
    if bgm_storage_key:
        bgm_path = STORAGE_DIR / bgm_storage_key
        if bgm_path.is_file():
            audio_files.append(bgm_path)
            audio_specs.append({"input_index": len(audio_files), "delay_ms": 0, "is_bgm": True})
            bgm_used = True
        else:
            logger.warning(f"BGM 文件不存在，跳过: {bgm_storage_key}")

    placeholder_shots: list[str] = []
    input_keys: list[Optional[str]] = []
    norm_filter = build_normalize_filter(width, height, fps)
    encode_video = ["-an", "-c:v", "libx264", "-pix_fmt", "yuv420p"]

    with tempfile.TemporaryDirectory(prefix="timeline_") as tmp:
        tmpdir = Path(tmp)

        # 1) 逐镜头 normalize（缺产物 → 黑场占位；图片素材 → 静帧；镜头之间的间隙补黑场）
        normalized: list[str] = []
        for i, (shot, artifact) in enumerate(zip(shots, shot_videos)):
            if timeline and gaps[i] > 0:
                gap_out = tmpdir / f"gap_{i:03d}.mp4"
                normalized.append(str(gap_out))
                await asyncio.to_thread(
                    _run_ffmpeg,
                    [*build_black_placeholder_args(gaps[i], width, height, fps), *encode_video, str(gap_out)],
                )
            out = tmpdir / f"shot_{i:03d}.mp4"
            normalized.append(str(out))
            if artifact is not None and (STORAGE_DIR / artifact.storage_key).is_file():
                input_keys.append(artifact.storage_key)
                clip = timeline_clips[i] if timeline else None
                speed_filter = f"setpts=(PTS-STARTPTS)/{clip.speed:.8f}," if clip else ""
                if artifact.type == "image":
                    # 静帧：循环一张图片到镜头时长
                    source_args = ["-loop", "1", "-framerate", str(fps)]
                else:
                    # -stream_loop -1：素材短于镜头时长时循环补足（-t 只截不补）
                    source_args = [
                        "-stream_loop",
                        "-1",
                        *(["-ss", f"{clip.source_in_ms / 1000:.3f}"] if clip else []),
                    ]
                args = [
                    *source_args,
                    "-i",
                    str(STORAGE_DIR / artifact.storage_key),
                    "-t",
                    f"{max(1, shot.duration_ms) / 1000:.3f}",
                    "-vf",
                    speed_filter + norm_filter,
                    *encode_video,
                    str(out),
                ]
            else:
                placeholder_shots.append(shot.id)
                input_keys.append(None)
                args = [
                    *build_black_placeholder_args(shot.duration_ms, width, height, fps),
                    *encode_video,
                    str(out),
                ]
            await asyncio.to_thread(_run_ffmpeg, args)
            await progress(5 + round(65 * (i + 1) / len(shots)))

        # 2) concat demuxer 顺序拼接（规格已统一，-c copy）
        list_file = tmpdir / "concat.txt"
        list_file.write_text(build_concat_file_list(normalized), encoding="utf-8")
        concat_out = tmpdir / "concat.mp4"
        await asyncio.to_thread(
            _run_ffmpeg,
            ["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(concat_out)],
        )
        await progress(75)

        # 3) 混音 + 可选字幕烧录（libass 不可用时退化为不烧录）
        uid = uuid.uuid4().hex[:8]
        output_name = f"episode_{episode_id}" if episode_id else f"timeline_{uid}"
        final_path = VIDEOS_DIR / f"{output_name}_{uid}.mp4"
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

        srt_text = build_timeline_srt(timeline.spec) if timeline else build_srt(list(zip(shots, offsets)))
        srt_rel = f"videos/{output_name}_sub.srt"
        srt_path = STORAGE_DIR / srt_rel
        burn_path = tmpdir / "burn.srt"
        subtitles_burned = False

        base_args: list[str] = ["-i", str(concat_out)]
        for path, spec_item in zip(audio_files, audio_specs):
            base_args += (["-stream_loop", "-1"] if spec_item.get("loop") else []) + ["-i", str(path)]
        if not audio_specs:
            base_args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]

        # 先测一遍整体响度，再用固定增益归一到 -14 LUFS（动态模式会把被压低的 BGM 段重新拉高）
        loudnorm = LOUDNORM
        if audio_specs:
            measure_fc, measure_out = build_audio_mix_filter(
                audio_specs, bgm_volume_db, total_ms, loudnorm=f"{LOUDNORM}:print_format=json"
            )
            stats = await asyncio.to_thread(
                _measure_loudness,
                [*base_args, "-filter_complex", measure_fc, "-map", measure_out, "-vn", "-f", "null", "-"],
            )
            if stats:
                loudnorm = build_linear_loudnorm(stats)

        aigc_meta = build_aigc_metadata(job_id or final_path.stem, aigc_reserved_code) if aigc_label else None
        if aigc_label and not AIGC_BADGE_PATH.is_file():
            raise RuntimeError(f"AIGC 角标资源缺失: {AIGC_BADGE_PATH}")

        def _encode_args(burn: bool) -> list[str]:
            args = list(base_args)
            filters: list[str] = []
            maps: list[str] = []
            if audio_specs:
                fc, aout = build_audio_mix_filter(audio_specs, bgm_volume_db, total_ms, loudnorm=loudnorm)
                filters.append(fc)
                maps += ["-map", aout]
            else:
                maps += ["-map", "1:a"]
            video_src = "[0:v]"
            if burn:
                filters.append(f"[0:v]subtitles={_filter_path(burn_path)}[vsub]")
                video_src = "[vsub]"
            if aigc_label:
                # 角标是最后一个输入（0 是 concat，其后是各音频，没有音频时多一个 anullsrc）
                badge_index = 1 + len(audio_files) + (0 if audio_specs else 1)
                args += ["-i", str(AIGC_BADGE_PATH)]
                filters.append(build_badge_filter(video_src, badge_index, width, height))
                maps += ["-map", "[vout]"]
            elif burn:
                filters[-1] = filters[-1].replace("[vsub]", "[vout]")
                maps += ["-map", "[vout]"]
            else:
                maps += ["-map", "0:v"]
            if filters:
                args += ["-filter_complex", ";".join(filters)]
            args += maps + ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
            args += ["-c:a", "aac", "-shortest"]
            if aigc_meta is not None:
                # 隐式标识：comment 与自定义 aigc 标签各写一份 JSON（use_metadata_tags 才会保留自定义标签）
                payload = json.dumps(aigc_meta, ensure_ascii=False, separators=(",", ":"))
                args += ["-metadata", f"comment={payload}", "-metadata", f"aigc={payload}"]
                args += ["-movflags", "use_metadata_tags+faststart"]
            else:
                args += ["-movflags", "+faststart"]
            args.append(str(final_path))
            return args

        # 画布 EDL 没有审批流程里的导出档位，字幕烧录只看调用方参数
        profile = (
            timeline.spec.get("export_profiles", {}).get(export_profile, {})
            if timeline and source != "edl"
            else {}
        )
        burn_subtitles = bool(profile.get("burn_subtitles", burn_subtitles))
        want_burn = burn_subtitles and bool(srt_text.strip())
        if want_burn:
            burn_path.write_text(srt_text, encoding="utf-8")
            try:
                await asyncio.to_thread(_run_ffmpeg, _encode_args(burn=True))
                subtitles_burned = True
            except Exception as e:
                # ffmpeg 未编译 libass 等情况：退化为不烧录，SRT 单独交付
                logger.warning(f"字幕烧录失败，退化为不烧录: {e}")
        if not subtitles_burned:
            await asyncio.to_thread(_run_ffmpeg, _encode_args(burn=False))
        await progress(95)

    # 4) SRT 与 manifest 建档
    extra_meta = dict(artifact_meta or {})
    srt_artifact_id: Optional[str] = None
    # 烧录失败退化时 SRT 是字幕的唯一交付形式，不受 export_srt 开关影响
    if srt_text.strip() and (export_srt or (want_burn and not subtitles_burned)):
        srt_path.write_text(srt_text, encoding="utf-8")
        srt_artifact = await asset_service.create_artifact(
            session,
            project_id=project_id,
            type="subtitle",
            storage_key=srt_rel,
            mime_type="application/x-subrip",
            meta={"episode_id": episode_id, "burned": subtitles_burned, **extra_meta},
        )
        srt_artifact_id = srt_artifact.id

    # final 模式前面已校验齐全；is_publishable 仅 final 且无占位镜头时为 True
    is_publishable = render_mode == "final" and not placeholder_shots
    video_artifact = await asset_service.create_artifact(
        session,
        project_id=project_id,
        job_id=job_id,
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
            "aigc_label": bool(aigc_meta),
            **extra_meta,
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
                # 图片素材在成片里是静帧（出片检查据此排除冻帧误报）
                "still": bool(shot_videos[i] is not None and shot_videos[i].type == "image"),
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
            "srt_storage_key": srt_rel if srt_artifact_id else None,
            "subtitles_burned": subtitles_burned,
        },
        "bgm": {"storage_key": bgm_storage_key if bgm_used else None, "volume_db": bgm_volume_db},
        "audio_tracks": timeline.spec.get("audio_tracks", []) if timeline else [],
        "subtitle_tracks": timeline.spec.get("subtitle_tracks", []) if timeline else [],
        "overlay_tracks": timeline.spec.get("overlay_tracks", []) if timeline else [],
        "export": {"codec": "h264/aac", "container": "mp4", "renderer": "ffmpeg"},
        "aigc": {"badge": bool(aigc_meta), "metadata": aigc_meta},
    }
    if source:
        manifest["source"] = source
    manifest_rel = f"videos/{output_name}_manifest.json"
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
        "srt_artifact_id": srt_artifact_id,
        "storage_key": video_artifact.storage_key,
        "manifest": manifest,
        "duration_ms": total_ms,
        "subtitles_burned": subtitles_burned,
        "aigc": manifest["aigc"],
    }
