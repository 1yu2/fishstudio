"""基于 FFmpeg 的音频拼接、BGM 选择与混音工具。"""

import json
import logging
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent
ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

STORAGE_DIR = BASE_DIR / "storage"
AUDIOS_DIR = STORAGE_DIR / "audios"
BGM_DIR = STORAGE_DIR / "bgm"
PODCASTS_DIR = STORAGE_DIR / "podcasts"

for directory in (AUDIOS_DIR, BGM_DIR, PODCASTS_DIR):
    directory.mkdir(parents=True, exist_ok=True)

try:
    import imageio_ffmpeg

    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_PATH = ""

FFMPEG_AVAILABLE = bool(FFMPEG_PATH and Path(FFMPEG_PATH).is_file())
_DURATION_PATTERN = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def _require_ffmpeg() -> None:
    if not FFMPEG_AVAILABLE:
        raise RuntimeError("imageio-ffmpeg 不可用，无法处理音频")


def _resolve_audio_path(audio_path: str) -> Path:
    if audio_path.startswith("/storage/"):
        path = BASE_DIR / audio_path.lstrip("/")
    else:
        path = Path(audio_path)
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(audio_path)
    return path


def _run_ffmpeg(arguments: list[str], timeout: int = 180) -> None:
    _require_ffmpeg()
    result = subprocess.run(
        [FFMPEG_PATH, "-hide_banner", "-nostdin", "-y", *arguments],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "FFmpeg 执行失败").strip()
        raise RuntimeError(detail[-2000:])


def probe_audio_duration(file_path: Path | str) -> float:
    """不依赖 ffprobe，直接从 FFmpeg 输入元数据读取音频时长。"""
    _require_ffmpeg()
    path = Path(file_path).resolve()
    result = subprocess.run(
        [FFMPEG_PATH, "-hide_banner", "-nostdin", "-i", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    match = _DURATION_PATTERN.search(result.stderr or "")
    if not match:
        raise RuntimeError(f"无法读取音频时长: {path.name}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


class ConcatenateAudioInput(BaseModel):
    audio_files: List[str] = Field(
        description="要拼接的音频文件路径列表，按顺序拼接，如['/storage/audios/1.wav', '/storage/audios/2.wav']"
    )
    crossfade_duration: int = Field(
        default=200, description="交叉淡入淡出时长（毫秒），默认200ms，设为0则无过渡"
    )
    silence_duration: int = Field(
        default=1200, description="音频片段之间的静音时长（毫秒），默认1200ms"
    )


@tool("concatenate_audio", args_schema=ConcatenateAudioInput)
def concatenate_audio_tool(
    audio_files: List[str], crossfade_duration: int = 200, silence_duration: int = 1200
) -> str:
    """将多个音频按顺序拼接为 WAV，支持交叉淡化或静音间隔。"""
    try:
        if len(audio_files) < 2:
            return "Error: 至少需要2个音频文件才能拼接"
        if crossfade_duration < 0 or silence_duration < 0:
            return "Error: crossfade_duration 和 silence_duration 不能为负数"

        paths = [_resolve_audio_path(path) for path in audio_files]
        durations = [probe_audio_duration(path) for path in paths]
        input_args: list[str] = []
        filters: list[str] = []
        for index, path in enumerate(paths):
            input_args.extend(["-i", str(path)])
            filters.append(
                f"[{index}:a]aresample=48000,"
                "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
                f"[a{index}]"
            )

        if crossfade_duration > 0:
            current = "a0"
            current_duration = durations[0]
            for index in range(1, len(paths)):
                fade_seconds = min(
                    crossfade_duration / 1000.0,
                    max(0.01, current_duration - 0.01),
                    max(0.01, durations[index] - 0.01),
                )
                output_label = "out" if index == len(paths) - 1 else f"xf{index}"
                filters.append(
                    f"[{current}][a{index}]acrossfade=d={fade_seconds:.3f}:c1=tri:c2=tri"
                    f"[{output_label}]"
                )
                current = output_label
                current_duration += durations[index] - fade_seconds
        else:
            labels: list[str] = []
            for index in range(len(paths)):
                labels.append(f"[a{index}]")
                if silence_duration > 0 and index < len(paths) - 1:
                    silence_label = f"s{index}"
                    filters.append(
                        "anullsrc=channel_layout=stereo:sample_rate=48000:"
                        f"d={silence_duration / 1000.0:.3f}[{silence_label}]"
                    )
                    labels.append(f"[{silence_label}]")
            filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[out]")

        AUDIOS_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"concatenated_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.wav"
        output_path = AUDIOS_DIR / filename
        _run_ffmpeg(
            [
                *input_args,
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[out]",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ]
        )
        duration = probe_audio_duration(output_path)
        http_path = f"/storage/audios/{filename}"
        return json.dumps(
            {
                "audio_url": http_path,
                "local_path": http_path,
                "duration_seconds": duration,
                "file_count": len(audio_files),
                "crossfade_duration": crossfade_duration,
                "silence_duration": silence_duration,
                "message": f"成功拼接 {len(audio_files)} 个音频文件",
            },
            ensure_ascii=False,
        )
    except FileNotFoundError as error:
        return f"Error: 文件不存在: {error}"
    except Exception as error:
        logger.exception("音频拼接失败")
        return f"Error: {error}"


class SelectBGMInput(BaseModel):
    scene_description: str = Field(description="用于匹配背景音乐文件名的场景描述")
    duration_seconds: Optional[float] = Field(
        default=None, description="期望时长；指定后自动循环、裁剪并淡出"
    )


@tool("select_background_music", args_schema=SelectBGMInput)
def select_bgm_tool(scene_description: str, duration_seconds: Optional[float] = None) -> str:
    """从 BGM 目录选择文件，并可生成指定时长的循环裁剪版本。"""
    try:
        bgm_files = sorted(
            path
            for pattern in ("*.mp3", "*.wav", "*.m4a", "*.aac", "*.ogg", "*.flac")
            for path in BGM_DIR.glob(pattern)
        )
        if not bgm_files:
            return f"Error: BGM目录为空，请在 {BGM_DIR} 添加背景音乐文件"

        keywords = [word for word in scene_description.lower().split() if word]
        scored = [
            (sum(1 for word in keywords if word in path.stem.lower()), path)
            for path in bgm_files
        ]
        best_score, best_match = max(scored, key=lambda item: (item[0], item[1].name))
        original_duration = probe_audio_duration(best_match)
        adjusted_duration = original_duration

        if duration_seconds is not None:
            if duration_seconds <= 0:
                return "Error: duration_seconds 必须大于 0"
            AUDIOS_DIR.mkdir(parents=True, exist_ok=True)
            filename = f"bgm_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.mp3"
            output_path = AUDIOS_DIR / filename
            fade_duration = min(2.0, duration_seconds)
            fade_start = max(0.0, duration_seconds - fade_duration)
            _run_ffmpeg(
                [
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(best_match),
                    "-t",
                    f"{duration_seconds:.3f}",
                    "-af",
                    f"afade=t=out:st={fade_start:.3f}:d={fade_duration:.3f}",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "192k",
                    str(output_path),
                ]
            )
            adjusted_duration = probe_audio_duration(output_path)
            http_path = f"/storage/audios/{filename}"
        else:
            http_path = f"/storage/bgm/{best_match.name}"

        return json.dumps(
            {
                "bgm_path": http_path,
                "bgm_name": best_match.stem,
                "original_duration": original_duration,
                "adjusted_duration": adjusted_duration,
                "match_score": best_score,
                "scene_description": scene_description,
                "message": f"已选择BGM: {best_match.stem}",
            },
            ensure_ascii=False,
        )
    except Exception as error:
        logger.exception("BGM选择失败")
        return f"Error: {error}"


class MixAudioWithBGMInput(BaseModel):
    voice_audio: str = Field(description="主音频文件路径（人声/对话）")
    bgm_audio: str = Field(description="背景音乐文件路径")
    bgm_volume: float = Field(default=-26, description="对话开始后的 BGM 音量调整（dB）")
    intro_duration: float = Field(default=3.0, description="人声开始前的 BGM 开场时长（秒）")
    normalize: bool = Field(default=True, description="是否执行响度归一化")


@tool("mix_audio_with_bgm", args_schema=MixAudioWithBGMInput)
def mix_audio_with_bgm_tool(
    voice_audio: str,
    bgm_audio: str,
    bgm_volume: float = -26,
    intro_duration: float = 3.0,
    normalize: bool = True,
) -> str:
    """将人声延迟到开场之后，与循环 BGM 混合并输出 MP3。"""
    try:
        if intro_duration < 0:
            return "Error: intro_duration 不能为负数"
        voice_path = _resolve_audio_path(voice_audio)
        bgm_path = _resolve_audio_path(bgm_audio)
        voice_duration = probe_audio_duration(voice_path)
        total_duration = intro_duration + voice_duration
        fade_duration = min(2.0, max(0.0, total_duration - intro_duration))
        fade_out_duration = min(3.0, total_duration)
        fade_out_start = max(0.0, total_duration - fade_out_duration)
        delay_ms = int(round(intro_duration * 1000))

        if fade_duration > 0:
            volume_expression = (
                "pow(10\\,("
                f"{bgm_volume:.3f}*min(max(t-{intro_duration:.3f}\\,0)/{fade_duration:.3f}\\,1)"
                ")/20)"
            )
        else:
            volume_expression = f"pow(10\\,{bgm_volume:.3f}/20)"

        normalize_filter = ",loudnorm=I=-16:LRA=11:TP=-1.5" if normalize else ""
        filter_complex = (
            f"[1:a]atrim=0:{total_duration:.3f},asetpts=PTS-STARTPTS,"
            f"volume='{volume_expression}':eval=frame,"
            f"afade=t=out:st={fade_out_start:.3f}:d={fade_out_duration:.3f}[bgm];"
            f"[0:a]adelay={delay_ms}:all=1,apad,atrim=0:{total_duration:.3f}[voice];"
            "[bgm][voice]amix=inputs=2:duration=longest:dropout_transition=0"
            f"{normalize_filter}[out]"
        )

        PODCASTS_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"podcast_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.mp3"
        output_path = PODCASTS_DIR / filename
        _run_ffmpeg(
            [
                "-i",
                str(voice_path),
                "-stream_loop",
                "-1",
                "-i",
                str(bgm_path),
                "-filter_complex",
                filter_complex,
                "-map",
                "[out]",
                "-t",
                f"{total_duration:.3f}",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "192k",
                str(output_path),
            ]
        )
        duration = probe_audio_duration(output_path)
        http_path = f"/storage/podcasts/{filename}"
        return json.dumps(
            {
                "audio_url": http_path,
                "local_path": http_path,
                "duration_seconds": duration,
                "voice_audio": voice_audio,
                "bgm_audio": bgm_audio,
                "bgm_volume": bgm_volume,
                "normalized": normalize,
                "message": "混音完成，播客已生成",
            },
            ensure_ascii=False,
        )
    except FileNotFoundError as error:
        return f"Error: 文件不存在: {error}"
    except Exception as error:
        logger.exception("混音失败")
        return f"Error: {error}"
