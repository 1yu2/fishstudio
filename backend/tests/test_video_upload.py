from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_upload_video_saves_supported_file():
    response = client.post(
        "/api/upload-video",
        files={"file": ("sample.mp4", b"fake video bytes", "video/mp4")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["url"].startswith("/storage/videos/upload_")
    assert payload["url"].endswith(".mp4")

    saved_path = Path(__file__).resolve().parents[1] / payload["url"].lstrip("/")
    assert saved_path.exists()
    assert saved_path.read_bytes() == b"fake video bytes"
    saved_path.unlink()


def test_upload_video_rejects_unsupported_file():
    response = client.post(
        "/api/upload-video",
        files={"file": ("notes.txt", b"not a video", "text/plain")},
    )

    assert response.status_code == 400
    assert "只支持视频文件" in response.json()["detail"]
