"""L1 媒体探测与非阻断 metadata 写入。"""

import asyncio
import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services import media_probe

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg unavailable")


@needs_ffmpeg
async def test_real_video(tmp_path):
    path = tmp_path / "video.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=720x1280:rate=24",
            "-t",
            "2",
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        timeout=30,
    )
    probe = await media_probe.probe_media(path)
    assert probe["type"] == "video"
    assert (probe["width"], probe["height"]) == (720, 1280)
    assert probe["duration"] == pytest.approx(2, abs=0.1)
    assert probe["frame_rate"] == 24
    assert probe["file_size"] == path.stat().st_size
    assert media_probe.check_media(probe, {"duration": 2, "aspect_ratio": "9:16"})["ok"] is True
    result = media_probe.check_media(probe, {"duration": 5})
    assert next(c for c in result["checks"] if c["name"] == "duration")["ok"] is False


@needs_ffmpeg
async def test_real_image(tmp_path):
    path = tmp_path / "image.png"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=720x1280",
            "-frames:v",
            "1",
            str(path),
        ],
        check=True,
        timeout=30,
    )
    probe = await media_probe.probe_media(path)
    assert probe["type"] == "image"
    assert probe["duration"] is None
    assert media_probe.check_media(probe, {"aspect_ratio": "9:16"})["ok"] is True


async def test_non_media(tmp_path):
    path = tmp_path / "text.txt"
    path.write_text("not media")
    probe = await media_probe.probe_media(path)
    assert probe["ok"] is None
    assert probe["error"]


async def test_missing_ffprobe(monkeypatch, tmp_path):
    monkeypatch.setattr(
        media_probe.asyncio, "create_subprocess_exec", AsyncMock(side_effect=FileNotFoundError("ffprobe"))
    )
    probe = await media_probe.probe_media(tmp_path / "missing.mp4")
    assert probe["ok"] is None
    assert "FileNotFoundError" in probe["error"]


@pytest.mark.parametrize(
    "kind,format_name,codec",
    [("video", "mov,mp4", "h264"), ("image", "png_pipe", "png"), ("audio", "wav", "pcm_s16le")],
)
async def test_probe_fields(monkeypatch, tmp_path, kind, format_name, codec):
    path = tmp_path / "media"
    path.write_bytes(b"media")
    stream = {
        "codec_type": "audio" if kind == "audio" else "video",
        "codec_name": codec,
        "width": 720,
        "height": 1280,
        "avg_frame_rate": "24000/1001",
    }
    data = {"streams": [stream], "format": {"format_name": format_name, "duration": "2"}}
    process = SimpleNamespace(
        returncode=0, communicate=AsyncMock(return_value=(json.dumps(data).encode(), b""))
    )
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(media_probe.asyncio, "create_subprocess_exec", launch)
    probe = await media_probe.probe_media(path)
    assert probe["type"] == kind
    assert probe["has_audio"] is (kind == "audio")
    assert probe["file_size"] == 5
    assert probe["duration"] == (None if kind == "image" else 2)
    assert probe["audio_codec" if kind == "audio" else "video_codec"] == codec
    if kind != "audio":
        assert probe["frame_rate"] == pytest.approx(24000 / 1001)
    assert launch.call_args.args == (
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    )


@pytest.mark.parametrize("returncode,stdout", [(1, b""), (0, b"bad json"), (0, b'{"streams": []}')])
async def test_invalid_probe(monkeypatch, tmp_path, returncode, stdout):
    process = SimpleNamespace(
        returncode=returncode, communicate=AsyncMock(return_value=(stdout, b"invalid media"))
    )
    monkeypatch.setattr(media_probe.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    assert (await media_probe.probe_media(tmp_path / "bad"))["ok"] is None


async def test_timeout_reaps_process(monkeypatch, tmp_path):
    process = SimpleNamespace(
        returncode=None, communicate=AsyncMock(side_effect=[asyncio.TimeoutError(), (b"", b"")]), kill=Mock()
    )
    monkeypatch.setattr(media_probe.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    result = await media_probe.probe_media(tmp_path / "video")
    assert result["ok"] is None
    assert "TimeoutError" in result["error"]
    process.kill.assert_called_once()
    assert process.communicate.await_count == 2


def test_checks():
    probe = {"ok": True, "type": "video", "duration": 2, "width": 720, "height": 1280, "video_codec": "h264"}
    assert media_probe.check_media(probe, {"duration_ms": 3000, "aspect_ratio": "9:16"})["ok"] is True
    assert {c["name"] for c in media_probe.check_media(probe, {})["checks"]} == {
        "parseable",
        "resolution",
        "video_codec",
    }
    result = media_probe.check_media(
        {**probe, "width": 320, "video_codec": None}, {"aspect_ratio": "9:16", "duration": 5}
    )
    assert result["ok"] is False
    assert all(not c["ok"] for c in result["checks"] if c["name"] != "parseable")


@pytest.mark.parametrize(
    "probe",
    [
        {"ok": True, "type": "image", "width": 720, "height": 1280},
        {"ok": True, "type": "image", "width": 100, "height": 100},
        {"ok": None, "error": "ffprobe unavailable"},
    ],
)
async def test_attach_merges_metadata(monkeypatch, tmp_path, probe, caplog):
    from app.db.models import Artifact

    original = {"prompt": "keep me", "provider": "mock"}
    artifact = Artifact(type="image", storage_key="image.png", artifact_metadata=original)
    monkeypatch.setattr(media_probe, "probe_media", AsyncMock(return_value=probe))
    with caplog.at_level("INFO", logger=media_probe.__name__):
        await media_probe.attach_l1_check(artifact, tmp_path / "image.png", {})
    assert artifact.artifact_metadata is not original
    assert artifact.artifact_metadata["prompt"] == "keep me"
    assert "l1_check" not in original
    assert artifact.artifact_metadata["l1_check"]["probe"] == probe
    expected_ok = None if probe["ok"] is None else probe["width"] >= 480
    assert artifact.artifact_metadata["l1_check"]["ok"] is expected_ok
    assert "Artifact L1 check" in caplog.text
