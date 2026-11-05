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
