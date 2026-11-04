"""产物 L1 确定性检查：只记录检查结果，不阻断生成。"""

import asyncio
import json
import logging
import math
from fractions import Fraction
from pathlib import Path

logger = logging.getLogger(__name__)
PROBE_TIMEOUT_SECONDS = 20


def _number(value):
    try:
        number = float(Fraction(str(value)))
        return number if math.isfinite(number) else None
    except (ValueError, TypeError, ZeroDivisionError, OverflowError):
        return None


async def probe_media(path: Path) -> dict:
    """异步读取媒体信息；工具缺失、超时和损坏文件均降级为未知。"""
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=PROBE_TIMEOUT_SECONDS)
        if process.returncode:
            return {"ok": None, "error": stderr.decode(errors="replace").strip() or "ffprobe failed"}
        data = json.loads(stdout)
        streams = data.get("streams", [])
        video = next(
            (
                s
                for s in streams
                if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
            ),
            None,
        )
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        if not video and not audio:
            return {"ok": None, "error": "No media streams"}
        fmt = data.get("format", {})
        format_names = set(fmt.get("format_name", "").split(","))
        # ffprobe 将静态图片也报告为 video stream，按图片解复用器区分。
        is_image = bool(
            video
            and not audio
            and any(
                name in {"image2", "image2pipe", "ico"} or name.endswith("_pipe") for name in format_names
            )
        )
        kind = "image" if is_image else "video" if video else "audio"
        stream = video or audio
        return {
            "ok": True,
            "type": kind,
            "width": int(video.get("width", 0)) if video else None,
            "height": int(video.get("height", 0)) if video else None,
            "duration": None
            if is_image
            else (_number(fmt.get("duration")) or _number(stream.get("duration"))),
            "frame_rate": (_number(video.get("avg_frame_rate")) or _number(video.get("r_frame_rate")))
            if video
            else None,
            "video_codec": video.get("codec_name") if video else None,
            "audio_codec": audio.get("codec_name") if audio else None,
            "has_audio": audio is not None,
            "file_size": path.stat().st_size,
        }
    except Exception as error:
        return {"ok": None, "error": f"{type(error).__name__}: {error}"}
    finally:
        if process is not None and process.returncode is None:
            # 超时或任务取消时回收子进程，避免后台残留。
            try:
                process.kill()
                await process.communicate()
            except Exception:
                pass


def check_media(probe: dict, expected: dict) -> dict:
    """未知期望不参与判定；无法探测时保留三态 ok=None。"""
    try:
        if probe.get("ok") is not True:
            return {
                "ok": None,
                "error": probe.get("error", "Media probe unavailable"),
                "checks": [{"name": "parseable", "ok": None, "detail": probe.get("error", "Unknown")}],
            }
        checks = [{"name": "parseable", "ok": True, "detail": "Media streams parsed"}]

        def add(name, ok, detail):
            checks.append({"name": name, "ok": bool(ok), "detail": detail})

        duration = _number(expected.get("duration"))
        if duration is None and expected.get("duration_ms") is not None:
            milliseconds = _number(expected["duration_ms"])
            duration = milliseconds / 1000 if milliseconds is not None else None
        if probe.get("type") == "video" and duration is not None:
            actual = _number(probe.get("duration"))
            add(
                "duration",
                actual is not None and abs(actual - duration) <= 1,
                f"actual={actual}s, expected={duration}s, tolerance=1s",
            )
        if probe.get("type") in {"video", "image"}:
            width, height = _number(probe.get("width")), _number(probe.get("height"))
            ratio = _number(str(expected.get("aspect_ratio", "")).replace(":", "/"))
            if ratio is not None and ratio > 0:
                actual = width / height if width and height else None
                add(
                    "aspect_ratio",
                    actual is not None and abs(actual / ratio - 1) <= 0.02 + 1e-12,
                    f"actual={actual}, expected={ratio}, tolerance=2%",
                )
            add(
                "resolution",
                width is not None and height is not None and min(width, height) >= 480,
                f"{width}x{height}, minimum short edge=480",
            )
        if probe.get("type") == "video":
            add("video_codec", bool(probe.get("video_codec")), f"codec={probe.get('video_codec')}")
        return {"ok": all(item["ok"] for item in checks), "checks": checks}
    except Exception as error:
        return {"ok": None, "error": f"{type(error).__name__}: {error}"}


async def attach_l1_check(artifact, path: Path, expected: dict) -> None:
    """合并并重新赋值 JSONB，交由执行器后续事务持久化。"""
    try:
        probe = await probe_media(path)
        result = {**check_media(probe, expected), "probe": probe}
    except Exception as error:
        result = {"ok": None, "error": f"{type(error).__name__}: {error}"}
    artifact.artifact_metadata = {**(artifact.artifact_metadata or {}), "l1_check": result}
    logger.info("Artifact L1 check: path=%s ok=%s", path, result["ok"])
