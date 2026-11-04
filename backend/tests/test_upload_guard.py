"""聊天侧上传接口：登录、内容深度校验（解码 / 重编码）、大小限制、服务端文件名、按用户分目录。"""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from app.services import upload_guard


@pytest.fixture
def storage(tmp_path, monkeypatch):
    from app.routers import chat

    monkeypatch.setattr(chat, "_STORAGE_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def client():
    return TestClient(app, headers={"Authorization": "Bearer test"})  # 中间件要求带 Authorization 头


def _saved(storage, body):
    return storage / body["url"].removeprefix("/storage/")


@pytest.mark.parametrize("key,ext", [("png", "png"), ("jpg", "jpg"), ("webp", "webp"), ("gif", "gif")])
def test_upload_real_image_extension_from_content(client, storage, real_media, key, ext):
    response = client.post("/api/upload-image", files={"file": ("evil.php", real_media[key], "text/html")})
    assert response.status_code == 200
    body = response.json()
    assert body["url"] == f"/storage/uploads/1/images/{body['filename']}"
    assert body["filename"].endswith(f".{ext}") and "evil" not in body["filename"]
    with Image.open(_saved(storage, body)) as image:
        image.load()
        assert image.size == (16, 16)


def test_image_is_reencoded_and_trailing_data_stripped(client, storage, real_media):
    tail = b"<?php system($_GET[0]); ?>"
    response = client.post(
        "/api/upload-image", files={"file": ("a.png", real_media["png"] + tail, "image/png")}
    )
    assert response.status_code == 200
    assert tail not in _saved(storage, response.json()).read_bytes()


def test_image_metadata_is_stripped(client, storage):
    buffer = io.BytesIO()
    image = Image.new("RGB", (16, 16), (1, 2, 3))
    exif = Image.Exif()
    exif[270] = "secret-description"
    image.save(buffer, "JPEG", exif=exif)
    response = client.post("/api/upload-image", files={"file": ("a.jpg", buffer.getvalue(), "image/jpeg")})
    assert response.status_code == 200
    assert b"secret-description" not in _saved(storage, response.json()).read_bytes()


def test_animated_gif_keeps_frames(client, storage, real_media):
    response = client.post(
        "/api/upload-image", files={"file": ("a.gif", real_media["anim_gif"], "image/gif")}
    )
    assert response.status_code == 200
    with Image.open(_saved(storage, response.json())) as image:
        assert image.n_frames == 3


@pytest.mark.parametrize(
    "garbage",
    [
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,  # 魔数 + 零字节
        b"GIF89a" + b"not really a gif" * 4,
        b"\xff\xd8\xff\xe0" + b"garbage" * 8,
    ],
)
def test_magic_bytes_plus_garbage_is_rejected(client, storage, garbage):
    response = client.post("/api/upload-image", files={"file": ("a.png", garbage, "image/png")})
    assert response.status_code == 400
    assert list(storage.rglob("*.*")) == []  # 不留任何文件（含临时文件）


def test_image_dimension_limit(client, storage, monkeypatch):
    monkeypatch.setattr(upload_guard, "IMAGE_MAX_SIDE", 8)
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16)).save(buffer, "PNG")
    response = client.post("/api/upload-image", files={"file": ("a.png", buffer.getvalue(), "image/png")})
    assert response.status_code == 400
    assert list(storage.rglob("*.png")) == []


def test_upload_non_image_rejected(client, storage):
    response = client.post("/api/upload-image", files={"file": ("a.png", b"<svg onload=x>", "image/png")})
    assert response.status_code == 400


def test_path_traversal_filename_ignored(client, storage, real_media):
    response = client.post(
        "/api/upload-image", files={"file": ("../../etc/x.png", real_media["png"], "image/png")}
    )
    assert response.status_code == 200
    assert "/" not in response.json()["filename"] and ".." not in response.json()["filename"]
    assert {p.parent.name for p in storage.rglob("*.png")} == {"images"}


def test_size_limit_aborts_and_cleans_up(client, storage, monkeypatch, real_media):
    monkeypatch.setattr(upload_guard, "IMAGE_MAX_BYTES", 2 * 1024 * 1024)
    big = real_media["png"] + b"\x00" * (3 * 1024 * 1024)
    response = client.post("/api/upload-image", files={"file": ("big.png", big, "image/png")})
    assert response.status_code == 413
    assert list(storage.rglob("*.*")) == []


def test_empty_file(client, storage):
    assert client.post("/api/upload-image", files={"file": ("a.png", b"", "image/png")}).status_code == 400


def test_upload_audio_real_files(client, storage, real_media):
    wav = client.post(
        "/api/upload-audio", files={"file": ("x.exe", real_media["wav"], "application/octet-stream")}
    )
    assert wav.status_code == 200 and wav.json()["filename"].endswith(".wav")
    mp3 = client.post("/api/upload-audio", files={"file": ("x.wav", real_media["mp3"], "audio/wav")})
    assert mp3.status_code == 200 and mp3.json()["filename"].endswith(".mp3")


def test_audio_magic_plus_garbage_rejected(client, storage):
    for garbage in (b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 64, b"ID3\x03\x00" + b"garbage" * 10):
        response = client.post("/api/upload-audio", files={"file": ("a.wav", garbage, "audio/wav")})
        assert response.status_code == 400
    assert list(storage.rglob("*.*")) == []
    bad = client.post("/api/upload-audio", files={"file": ("a.mp3", b"not audio at all", "audio/mpeg")})
    assert bad.status_code == 400


def test_video_magic_plus_garbage_rejected(client, storage):
    response = client.post(
        "/api/upload-video",
        files={"file": ("a.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64, "video/mp4")},
    )
    assert response.status_code == 400
    assert list(storage.rglob("*.mp4")) == []


def test_limits_constants():
    assert upload_guard.AUDIO_MAX_BYTES == 20 * 1024 * 1024
    assert upload_guard.IMAGE_MAX_BYTES == 15 * 1024 * 1024


@pytest.mark.no_bypass_auth
def test_uploads_require_login(real_media):
    app.dependency_overrides.clear()
    client = TestClient(app)
    for path in ("/api/upload-image", "/api/upload-audio", "/api/upload-video"):
        assert client.post(path, files={"file": ("a.png", real_media["png"], "image/png")}).status_code == 401


# ── 第二轮复审：像素预算 / 全量解码 / 重新封装 / mkv 与 webm ──


def _gif(frames, size=(8, 8)):
    images = []
    for index in range(frames):
        image = Image.new("RGB", size, (index * 30 % 255, 0, 0))
        image.putpixel((0, 0), (255, index, 0))  # 让每帧都不同，避免被合并
        images.append(image)
    buffer = io.BytesIO()
    images[0].save(buffer, "GIF", save_all=True, append_images=images[1:], duration=50, loop=0)
    return buffer.getvalue()


def test_gif_over_total_pixel_budget_is_rejected_before_decoding(client, storage, monkeypatch):
    # 磁盘上很小，但「宽 × 高 × 帧数」超过预算（预算在测试里调小，避免真的造 1 亿像素）
    data = _gif(frames=10)
    assert len(data) < 5000
    monkeypatch.setattr(upload_guard, "IMAGE_MAX_TOTAL_PIXELS", 8 * 8 * 5)
    response = client.post("/api/upload-image", files={"file": ("a.gif", data, "image/gif")})
    assert response.status_code == 400
    assert list(storage.rglob("*.gif")) == []


def test_gif_over_frame_pixel_limit_is_rejected(client, storage, monkeypatch):
    monkeypatch.setattr(upload_guard, "IMAGE_MAX_FRAME_PIXELS", 8 * 8 - 1)
    response = client.post("/api/upload-image", files={"file": ("a.gif", _gif(2), "image/gif")})
    assert response.status_code == 400


def test_budget_checked_before_any_pixel_is_decoded(tmp_path, monkeypatch):
    """预算检查发生在 load / seek 之前：超预算时 Image.load 不应被调用。"""
    from PIL import ImageFile

    path = tmp_path / "a.gif"
    path.write_bytes(_gif(10))
    monkeypatch.setattr(upload_guard, "IMAGE_MAX_TOTAL_PIXELS", 10)
    called = []
    monkeypatch.setattr(ImageFile.ImageFile, "load", lambda self: called.append(1))
    with pytest.raises(Exception, match="400|图片"):
        upload_guard._reencode_image(path, "gif")
    assert called == []


def test_normal_animation_within_budget_passes(client, storage):
    response = client.post("/api/upload-image", files={"file": ("a.gif", _gif(10), "image/gif")})
    assert response.status_code == 200
    with Image.open(_saved(storage, response.json())) as image:
        assert image.n_frames == 10


def _ffmpeg(*args):
    import subprocess

    from app.services.timeline_renderer import _ffmpeg_exe

    return subprocess.run([_ffmpeg_exe(), "-y", *args], capture_output=True)


@pytest.fixture(scope="module")
def long_media(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("long")
    _ffmpeg("-f", "lavfi", "-i", "testsrc=s=128x128:d=6:r=25", "-pix_fmt", "yuv420p", str(tmp / "v.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=6", str(tmp / "a.mp3"))
    return {"mp4": (tmp / "v.mp4").read_bytes(), "mp3": (tmp / "a.mp3").read_bytes(), "tmp": tmp}


def _corrupt(data: bytes, start_ratio: float) -> bytes:
    import random

    buffer = bytearray(data)
    start = int(len(buffer) * start_ratio)
    rng = random.Random(7)
    for index in range(start, start + 400):
        buffer[index] = rng.randrange(256)
    return bytes(buffer)


def test_video_trailing_junk_is_stripped_by_remux(client, storage, long_media):
    """合法 mp4 后面拼接垃圾：重新封装后垃圾被丢弃（落盘大小等于原始大小）。"""
    original = long_media["mp4"]
    response = client.post(
        "/api/upload-video", files={"file": ("a.mp4", original + b"<?php evil ?>" * 40, "video/mp4")}
    )
    assert response.status_code == 200
    saved = _saved(storage, response.json()).read_bytes()
    assert b"<?php" not in saved and len(saved) <= len(original) + 64


def test_mp3_with_trailing_junk_is_rejected(client, storage, long_media):
    """mp3 尾随垃圾在完整解码时会报错 → 直接拒绝（不是剥离）。"""
    response = client.post(
        "/api/upload-audio", files={"file": ("a.mp3", long_media["mp3"] + b"junk" * 100, "audio/mpeg")}
    )
    assert response.status_code == 400
    assert list(storage.rglob("*.mp3")) == []


def test_media_corrupt_after_first_seconds_is_rejected(client, storage, long_media):
    # 前 3 秒完好、之后损坏：旧的“只探测前 3 秒”会放过，全量解码必须拒绝
    bad_video = _corrupt(long_media["mp4"], 0.6)
    assert (
        client.post("/api/upload-video", files={"file": ("a.mp4", bad_video, "video/mp4")}).status_code == 400
    )
    bad_audio = _corrupt(long_media["mp3"], 0.7)
    assert (
        client.post("/api/upload-audio", files={"file": ("a.mp3", bad_audio, "audio/mpeg")}).status_code
        == 400
    )
    assert list(storage.rglob("*.*")) == []


def test_normal_media_passes_and_is_remuxed(client, storage, real_media, long_media):
    assert (
        client.post(
            "/api/upload-video", files={"file": ("a.mp4", long_media["mp4"], "video/mp4")}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/upload-audio", files={"file": ("a.wav", real_media["wav"], "audio/wav")}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/upload-audio", files={"file": ("a.mp3", long_media["mp3"], "audio/mpeg")}
        ).status_code
        == 200
    )


def test_audio_without_audio_stream_is_rejected(client, storage, long_media):
    """纯视频文件按音频上传：没有音频流 → 400。"""
    response = client.post("/api/upload-audio", files={"file": ("a.mp4", long_media["mp4"], "audio/mp4")})
    assert response.status_code == 400


def test_sniff_distinguishes_mkv_from_webm():
    ebml = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81\x04\x42\xf3\x81\x08\x42\x82"
    assert upload_guard.sniff_video(ebml + b"\x88matroska" + b"\x00" * 20) == ("mkv", "video/x-matroska")
    assert upload_guard.sniff_video(ebml + b"\x84webm" + b"\x00" * 20) == ("webm", "video/webm")
    assert upload_guard.sniff_video(ebml + b"\x00" * 40) is None


def test_real_mkv_and_webm_get_matching_extension(client, storage, long_media):
    tmp = long_media["tmp"]
    mkv = _ffmpeg("-f", "lavfi", "-i", "testsrc=s=64x64:d=1", "-c:v", "mpeg4", str(tmp / "t.mkv"))
    assert mkv.returncode == 0
    response = client.post(
        "/api/upload-video", files={"file": ("x.webm", (tmp / "t.mkv").read_bytes(), "video/webm")}
    )
    assert response.status_code == 200 and response.json()["filename"].endswith(".mkv")
    webm = _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=s=64x64:d=1", "-c:v", "libvpx", "-b:v", "200k", str(tmp / "t.webm")
    )
    if webm.returncode != 0:
        pytest.skip("本机 ffmpeg 没有 libvpx，跳过真实 webm 样本")
    response = client.post(
        "/api/upload-video", files={"file": ("x.mkv", (tmp / "t.webm").read_bytes(), "video/x-matroska")}
    )
    assert response.status_code == 200 and response.json()["filename"].endswith(".webm")
