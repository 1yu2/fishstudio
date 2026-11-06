from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, headers={"Authorization": "Bearer test"})


def test_upload_video_saves_supported_file(real_media, tmp_path, monkeypatch):
    from app.routers import chat

    monkeypatch.setattr(chat, "_STORAGE_DIR", tmp_path)

    async def _fake_create_artifact(session, **kwargs):
        return SimpleNamespace(id="art-test")  # 上传会建 Artifact；这里 session 是 None，替掉落库

    monkeypatch.setattr(chat.asset_service, "create_artifact", _fake_create_artifact)
    response = client.post(
        "/api/upload-video",
        files={"file": ("sample.mp4", real_media["mp4"], "video/mp4")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["url"].startswith("/storage/uploads/1/videos/upload_")
    assert body["url"].endswith(".mp4")
    saved_path = tmp_path / body["url"].removeprefix("/storage/")
    # 上传后会被重新封装（丢弃尾随数据与元数据），字节不要求与原文件一致
    assert saved_path.read_bytes()[4:8] == b"ftyp"


def test_upload_video_rejects_unsupported_file():
    response = client.post(
        "/api/upload-video",
        files={"file": ("notes.txt", b"not a video", "text/plain")},
    )

    assert response.status_code == 400
    assert "不支持的视频格式" in response.json()["detail"]
