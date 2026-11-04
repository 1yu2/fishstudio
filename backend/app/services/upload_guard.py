"""上传安全：边读边限大小、按文件内容嗅探类型、服务端生成文件名。

不信任 Content-Type 与用户文件名：扩展名由嗅探结果决定，落盘文件名完全由服务端生成，
用户文件名只可能出现在返回给前端的展示字段里（由调用方自行截断）。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from fastapi import HTTPException

IMAGE_MAX_BYTES = 15 * 1024 * 1024
AUDIO_MAX_BYTES = 20 * 1024 * 1024
VIDEO_MAX_BYTES = 200 * 1024 * 1024
_CHUNK = 1024 * 1024
_SNIFF_BYTES = 64  # EBML DocType 在文件头前几十字节内
IMAGE_MAX_SIDE = 8192  # 单边像素上限，防解压炸弹
IMAGE_MAX_FRAME_PIXELS = 40_000_000  # 单帧像素上限
IMAGE_MAX_TOTAL_PIXELS = 100_000_000  # 宽 × 高 × 帧数 的总预算，解码前就检查
_GIF_MAX_FRAMES = 500

Sniffed = Optional[tuple[str, str]]  # (扩展名, mime)


def sniff_image(head: bytes) -> Sniffed:
    """只认 png / jpeg / webp / gif。"""
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", "image/png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpg", "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp", "image/webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", "image/gif"
    return None


def sniff_audio(head: bytes) -> Sniffed:
    """复用画布 BGM 上传的嗅探（wav/m4a/mp3/aac），再补充 ogg / flac。"""
    from app.services.canvas_render_service import sniff_audio as _sniff_bgm

    found = _sniff_bgm(head)
    if found is not None:
        return found
    if head[:4] == b"OggS":
        return "ogg", "audio/ogg"
    if head[:4] == b"fLaC":
        return "flac", "audio/flac"
    return None


def sniff_video(head: bytes) -> Sniffed:
    """mp4 / mov / m4v（ISO BMFF ftyp）与 webm / mkv（EBML）；avi 为 RIFF....AVI。"""
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand == b"qt  ":
            return "mov", "video/quicktime"
        return "mp4", "video/mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        # EBML 头里的 DocType 区分 webm 与 matroska；两者都不是则不认
        if b"matroska" in head:
            return "mkv", "video/x-matroska"
        if b"webm" in head:
            return "webm", "video/webm"
        return None
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi", "video/x-msvideo"
    return None


def new_filename(prefix: str, ext: str) -> str:
    """服务端生成文件名：prefix_时间戳_随机串.ext，不含任何用户输入。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}.{ext}"


async def save_upload(
    file: Any,
    dest_dir: Path,
    *,
    sniff: Callable[[bytes], Sniffed],
    max_bytes: int,
    prefix: str = "upload",
    kind_label: str = "文件",
    filename: Optional[Callable[[str], str]] = None,
    validate: Optional[Callable[[Path, str], Awaitable[None]]] = None,
) -> tuple[Path, str, int]:
    """把上传流落盘并返回 (路径, mime, 字节数)。

    - 分块读取，累计超过 max_bytes 立即中断并删除半成品（413），不会先整体读进内存；
    - validate：落盘后的深度校验（完整解码 / 重编码 / ffmpeg 探测），抛 HTTPException 时删除文件；
    - 第一块做内容嗅探，不认识的类型直接 400，此时还没有写任何文件；
    - 文件名由嗅探出的扩展名决定（filename 回调可定制前缀，参数为扩展名）。
    """
    first = await file.read(_CHUNK)
    if not first:
        raise HTTPException(status_code=400, detail=f"{kind_label}不能为空")
    if len(first) > max_bytes:
        raise HTTPException(status_code=413, detail=f"{kind_label}不能超过 {max_bytes // (1024 * 1024)}MB")
    found = sniff(first[:_SNIFF_BYTES])
    if found is None:
        raise HTTPException(status_code=400, detail=f"不支持的{kind_label}格式")
    ext, mime = found
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / (filename(ext) if filename else new_filename(prefix, ext))
    total = 0
    try:
        with path.open("wb") as output:
            chunk = first
            while chunk:
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=413, detail=f"{kind_label}不能超过 {max_bytes // (1024 * 1024)}MB"
                    )
                output.write(chunk)
                chunk = await file.read(_CHUNK)
        if validate is not None:
            await validate(path, ext)
            total = path.stat().st_size
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path, mime, total


# ── 深度校验：只看魔数不够（魔数 + 任意内容的 polyglot 也能过），必须真正解码 ──


def _reencode_image(path: Path, ext: str) -> None:
    """完整解码并按嗅探出的格式重新编码，丢弃尾随数据与元数据（EXIF / ICC / 注释等）。

    解码前先按「宽 × 高 × 帧数」检查像素预算（Image.open 是惰性的，此时还没解码任何像素）；
    动图交给 Pillow 的 save_all 逐帧处理，不会把所有帧同时留在内存里。
    """
    from PIL import Image, ImageSequence

    try:
        with Image.open(path) as probe:
            probe.verify()  # 结构校验；verify 之后对象不可再用
        with Image.open(path) as image:
            width, height = image.size
            frames_count = getattr(image, "n_frames", 1)
            if width <= 0 or height <= 0 or max(width, height) > IMAGE_MAX_SIDE:
                raise ValueError("图片尺寸过大")
            if width * height > IMAGE_MAX_FRAME_PIXELS:
                raise ValueError("单帧像素过多")
            if frames_count > _GIF_MAX_FRAMES or width * height * frames_count > IMAGE_MAX_TOTAL_PIXELS:
                raise ValueError("图片总像素（含帧数）超出预算")
            fmt = {"png": "PNG", "jpg": "JPEG", "webp": "WEBP", "gif": "GIF"}[ext]
            temp = path.with_name(path.name + ".tmp")
            if frames_count > 1 and ext in ("gif", "webp"):
                # 第一遍只 seek 读各帧时长（逐帧解码、不保留）；第二遍由 save_all 逐帧编码写出
                durations = []
                for index in range(frames_count):
                    image.seek(index)
                    durations.append(image.info.get("duration", 100))
                loop = image.info.get("loop", 0)
                image.seek(0)
                extra = {"disposal": 2} if fmt == "GIF" else {}
                image.save(temp, fmt, save_all=True, loop=loop, duration=durations, **extra)
            else:
                image.load()
                converted = image
                if fmt == "JPEG" and image.mode not in ("RGB", "L", "CMYK"):
                    converted = image.convert("RGB")
                elif fmt == "PNG" and image.mode == "P" and "transparency" in image.info:
                    converted = image.convert("RGBA")
                if fmt in ("JPEG", "WEBP"):
                    converted.save(temp, fmt, quality=95)
                else:
                    converted.save(temp, fmt)
        os.replace(temp, path)
    except Exception as error:  # noqa: BLE001 - Pillow 对坏文件抛各种异常类型
        path.with_name(path.name + ".tmp").unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="图片已损坏、尺寸过大或无法解析") from error


async def validate_image(path: Path, ext: str) -> None:
    await asyncio.to_thread(_reencode_image, path, ext)


def _decode_timeout(path: Path) -> float:
    """完整解码的超时：随文件大小增长（约每 MB 2 秒），下限 10 秒，上限 120 秒。"""
    return min(120.0, max(10.0, path.stat().st_size / (1024 * 1024) * 2))


def _run_ffmpeg(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    from app.services.timeline_renderer import _ffmpeg_exe

    return subprocess.run(
        [_ffmpeg_exe(), "-hide_banner", "-nostdin", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# 容器格式（按扩展名）→ ffmpeg -f 名称；remux 时显式指定，不依赖临时文件名
_MUX_FORMAT = {
    "wav": "wav",
    "mp3": "mp3",
    "m4a": "ipod",
    "aac": "adts",
    "ogg": "ogg",
    "flac": "flac",
    "mp4": "mp4",
    "mov": "mov",
    "webm": "webm",
    "mkv": "matroska",
    "avi": "avi",
}


def _validate_and_remux_media(path: Path, ext: str, kind: str) -> None:
    """完整解码整个文件（任何错误输出即拒绝），再 -c copy 重新封装，丢弃尾随数据与元数据。

    kind=audio 要求有音频流；kind=video 要求有视频流（音频可选）。
    """
    try:
        decoded = _run_ffmpeg(
            ["-v", "error", "-i", str(path), "-f", "null", "-"], timeout=_decode_timeout(path)
        )
    except (OSError, subprocess.SubprocessError) as error:  # 含超时
        raise HTTPException(status_code=400, detail="媒体文件无法在限定时间内完整解码") from error
    if decoded.returncode != 0 or (decoded.stderr or "").strip():
        raise HTTPException(status_code=400, detail="媒体文件已损坏或无法完整解码")

    maps = ["-map", "0:a", "-vn"] if kind == "audio" else ["-map", "0:v", "-map", "0:a?"]
    temp = path.with_name(path.name + ".remux")
    try:
        remuxed = _run_ffmpeg(
            ["-v", "error", "-i", str(path), *maps, "-c", "copy", "-map_metadata", "-1"]
            + ["-f", _MUX_FORMAT.get(ext, ext), "-y", str(temp)],
            timeout=_decode_timeout(path),
        )
        if remuxed.returncode != 0 or not temp.exists() or temp.stat().st_size == 0:
            raise HTTPException(status_code=400, detail="媒体文件缺少有效的音/视频流")
        os.replace(temp, path)
    except (OSError, subprocess.SubprocessError) as error:
        raise HTTPException(status_code=400, detail="媒体文件处理失败") from error
    finally:
        temp.unlink(missing_ok=True)


async def validate_audio(path: Path, ext: str) -> None:
    await asyncio.to_thread(_validate_and_remux_media, path, ext, "audio")


async def validate_video(path: Path, ext: str) -> None:
    await asyncio.to_thread(_validate_and_remux_media, path, ext, "video")
