"""远端自托管 H3 Provider/Gateway 合同测试。"""

import hashlib

import httpx
import pytest

from app.providers.base import VideoGenerationRequest
from app.providers.local_h3 import LocalH3Provider
from app.providers.pricing import resolve_video_routing
from app.providers.video_router import VideoProviderRouter


def _enable(monkeypatch):
    monkeypatch.setenv("LOCAL_H3_ENABLED", "true")
    monkeypatch.setenv("LOCAL_H3_BASE_URL", "http://h3.test")
    monkeypatch.setenv("LOCAL_H3_API_KEY", "secret")
    monkeypatch.setenv("LOCAL_H3_COST_CNY_PER_SECOND", "0.12")


def _client(monkeypatch, handler):
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr("app.providers.local_h3.httpx.AsyncClient", factory)


def test_local_h3_capability_and_route_plan(monkeypatch):
    _enable(monkeypatch)
    provider = LocalH3Provider()
    request = VideoGenerationRequest(prompt="test", duration=4, quality="preview")

    assert provider.is_available()
    assert provider.supports(request, model="MiniMax-H3")
    assert not provider.supports(request.model_copy(update={"duration": 10}))

    decision = VideoProviderRouter(providers=[provider]).select(request)
    assert decision.provider.name == "local_h3"
    assert decision.model == "MiniMax-H3"
    assert decision.resolution == "preview"

    plan = resolve_video_routing(
        "explicit",
        "preview",
        duration=4,
        explicit_provider="local_h3",
    )
    assert plan["provider"] == "local_h3"
    assert plan["model"] == decision.model
    assert plan["protocol"] == "local_h3_v1"
    assert str(plan["estimated_total_cost"]) == "0.480000"


@pytest.mark.asyncio
async def test_local_h3_submit_uses_gateway_contract_and_idempotency(monkeypatch):
    _enable(monkeypatch)
    captured = {}

    def handler(request: httpx.Request):
        captured["path"] = request.url.path
        captured["key"] = request.headers.get("Idempotency-Key")
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"task_id": "h3-task-1", "status": "queued"})

    _client(monkeypatch, handler)
    provider = LocalH3Provider()
    request = VideoGenerationRequest(
        prompt="cat",
        duration=4,
        idempotency_key="job-123",
    )
    result = await provider.submit(request, model="MiniMax-H3")

    assert result.success
    assert result.external_request_id == "h3-task-1"
    assert result.raw_metadata["protocol"] == "local_h3_v1"
    assert captured == {
        "path": "/v1/generations",
        "key": "job-123",
        "auth": "Bearer secret",
    }


@pytest.mark.asyncio
async def test_local_h3_poll_download_and_hash(monkeypatch, tmp_path):
    _enable(monkeypatch)
    content = b"fake-mp4-content"
    digest = hashlib.sha256(content).hexdigest()

    def handler(request: httpx.Request):
        if request.url.path == "/v1/generations/h3-task-1":
            return httpx.Response(
                200,
                json={
                    "task_id": "h3-task-1",
                    "status": "succeeded",
                    "artifacts": [
                        {
                            "type": "video",
                            "url": "http://h3.test/files/video.mp4",
                            "sha256": digest,
                            "duration_ms": 4000,
                            "width": 672,
                            "height": 384,
                        }
                    ],
                    "usage": {"gpu_seconds": 120, "output_video_seconds": 4},
                },
            )
        if request.url.path == "/files/video.mp4":
            return httpx.Response(200, content=content, headers={"content-type": "video/mp4"})
        return httpx.Response(404)

    _client(monkeypatch, handler)
    monkeypatch.setattr("app.providers.local_h3.VIDEOS_DIR", tmp_path)
    provider = LocalH3Provider()

    status = await provider.get_status("h3-task-1", protocol="local_h3_v1")
    fetched = await provider.fetch_result("h3-task-1", protocol="local_h3_v1")

    assert status.success
    assert fetched.success
    assert fetched.artifacts[0]["sha256"] == digest
    assert (tmp_path / fetched.artifacts[0]["storage_key"].split("/")[-1]).read_bytes() == content


@pytest.mark.asyncio
async def test_local_h3_pending_oom_and_cancel(monkeypatch):
    _enable(monkeypatch)
    state = {"status": "running"}

    def handler(request: httpx.Request):
        if request.method == "DELETE":
            return httpx.Response(202)
        if state["status"] == "running":
            return httpx.Response(200, json={"status": "running", "progress": 25})
        return httpx.Response(
            200,
            json={
                "status": "failed",
                "error": {"code": "oom", "message": "CUDA OOM"},
            },
        )

    _client(monkeypatch, handler)
    provider = LocalH3Provider()
    pending = await provider.get_status("task")
    assert pending.error_code == "pending" and pending.retryable

    state["status"] = "failed"
    failed = await provider.get_status("task")
    assert failed.error_code == "oom" and not failed.retryable
    assert await provider.cancel("task")


def test_local_h3_disabled_is_not_routable(monkeypatch):
    monkeypatch.setenv("LOCAL_H3_ENABLED", "false")
    provider = LocalH3Provider(base_url="http://h3.test", api_key="secret")
    request = VideoGenerationRequest(prompt="test", duration=4)
    assert not provider.is_available()
    assert not provider.supports(request)


async def test_local_h3_submitting_is_in_progress_and_unknown_status_has_a_code(monkeypatch):
    _enable(monkeypatch)
    state = {"body": {"status": "submitting", "progress": 0}}
    _client(monkeypatch, lambda request: httpx.Response(200, json=state["body"]))
    provider = LocalH3Provider()

    submitting = await provider.get_status("task")
    assert submitting.error_code == "pending" and submitting.retryable  # 交给 ComfyUI 的瞬间不能判失败

    state["body"] = {"status": "weird", "error": None}
    unknown = await provider.get_status("task")
    assert unknown.error_code == "task_failed" and "weird" in unknown.error_message
