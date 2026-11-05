"""火山图片参考图：画布上传的 storage_key 能用；请求发出前就失败的不重试、不计费。"""

import asyncio
import uuid

import pytest

from app.providers import volcano_image
from app.providers.image_base import ImageGenerationRequest


def _provider():
    return volcano_image.VolcanoImageProvider(api_key="k", base_url="https://ark.example", model_name="m")


@pytest.fixture
def uploaded():
    storage = (volcano_image.BASE_DIR / "storage").resolve()
    path = storage / "uploads" / "canvas" / f"test-{uuid.uuid4().hex}" / "sheet.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    yield path.relative_to(storage).as_posix()
    path.unlink()
    path.parent.rmdir()


def test_storage_key_reference_is_inlined_as_base64(uploaded, monkeypatch):
    sent = {}

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"data": [{"url": "https://cdn.example/out.png"}]}

    def post(url, json, headers, timeout):
        sent.update(json)
        return Response()

    monkeypatch.setattr(volcano_image.requests, "post", post)
    monkeypatch.setattr(
        volcano_image, "download_and_save_image", lambda url, prompt: "/storage/images/out.png"
    )
    result = asyncio.run(_provider().generate(ImageGenerationRequest(prompt="p", source_image=uploaded)))
    assert result.success is True
    assert sent["image"].startswith("data:image/png;base64,")


@pytest.mark.parametrize("source", ["uploads/canvas/missing/none.png", "../../etc/passwd"])
def test_bad_reference_fails_before_any_request_and_is_not_retryable(source, monkeypatch):
    def post(*args, **kwargs):
        raise AssertionError("不应发请求")

    monkeypatch.setattr(volcano_image.requests, "post", post)
    result = asyncio.run(_provider().generate(ImageGenerationRequest(prompt="p", source_image=source)))
    assert result.success is False
    assert result.error_code == volcano_image.INVALID_INPUT
    assert result.retryable is False


class _OkResponse:
    status_code = 200
    text = ""

    @staticmethod
    def json():
        return {"data": [{"url": "https://tos.example/out.jpeg?X-Tos-Signature=s"}]}


@pytest.mark.parametrize("fail_times, ok", [(1, True), (3, False)])
def test_download_is_retried_alone_and_never_regenerates(fail_times, ok, monkeypatch):
    posts, downloads = [], []
    monkeypatch.setattr(volcano_image, "DOWNLOAD_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(volcano_image.requests, "post", lambda *a, **k: posts.append(1) or _OkResponse())

    def download(url, prompt):
        downloads.append(url)
        return url if len(downloads) <= fail_times else "/storage/images/out.jpeg"

    monkeypatch.setattr(volcano_image, "download_and_save_image", download)
    result = asyncio.run(_provider().generate(ImageGenerationRequest(prompt="p")))
    assert len(posts) == 1  # 只出一次图（只收一次费）
    assert result.success is ok
    if ok:
        assert len(downloads) == 2
    else:
        assert len(downloads) == volcano_image.DOWNLOAD_ATTEMPTS
        assert result.error_code == volcano_image.DOWNLOAD_FAILED
        assert result.retryable is False  # worker 不再整单重跑
        assert "X-Tos-Signature" not in result.error_message  # 不把签名链接写进错误
