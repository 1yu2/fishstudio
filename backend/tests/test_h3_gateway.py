import json
from pathlib import Path

import pytest

from gateway.h3_gateway import (
    GatewayStore,
    GenerationRequest,
    Settings,
    WorkflowBuilder,
    _find_video,
    _history_error,
)


def _settings(monkeypatch, tmp_path: Path) -> Settings:
    workflow = (
        Path(__file__).parent.parent / "gateway" / "workflows" / "video_minimax_h3_t2v.json"
    )
    monkeypatch.setenv("H3_GATEWAY_API_KEY", "secret")
    monkeypatch.setenv("H3_GATEWAY_WORKFLOW", str(workflow))
    monkeypatch.setenv("H3_GATEWAY_STATE_DIR", str(tmp_path))
    return Settings()


def test_h3_workflow_builder_injects_runtime_values(monkeypatch, tmp_path):
    settings = _settings(monkeypatch, tmp_path)
    builder = WorkflowBuilder(settings)
    request = GenerationRequest(
        deployment="h3-fl2va-int8-convrot",
        prompt="orange cat",
        duration=4,
        aspect_ratio="16:9",
        resolution="preview",
        seed=123,
    )

    workflow, width, height = builder.build("task-1", request)

    assert (width, height) == (512, 288)
    assert workflow["140:131"]["inputs"]["prompt"] == "orange cat"
    assert workflow["140:131"]["inputs"]["width"] == 512
    assert workflow["140:131"]["inputs"]["height"] == 288
    assert workflow["140:133"]["inputs"]["value"] == 4
    assert workflow["140:129"]["inputs"]["noise_seed"] == 123
    assert workflow["140:128"]["inputs"]["device"] == "cpu"
    assert workflow["140:139"]["inputs"]["value"] is True
    assert workflow["140:138"]["inputs"]["value"] == 8
    assert "task-1" in workflow["92"]["inputs"]["filename_prefix"]


def test_h3_workflow_builder_rejects_unverified_modes(monkeypatch, tmp_path):
    builder = WorkflowBuilder(_settings(monkeypatch, tmp_path))
    request = GenerationRequest(
        deployment="h3-fl2va-int8-convrot",
        prompt="cat",
        duration=4,
        mode="first_frame",
        references=[{"type": "image", "url": "data:image/png;base64,AA=="}],
    )
    with pytest.raises(ValueError, match="仅开放"):
        builder.build("task", request)


@pytest.mark.asyncio
async def test_gateway_store_idempotency_and_recovery(monkeypatch, tmp_path):
    settings = _settings(monkeypatch, tmp_path)
    store = GatewayStore(settings.db_path)
    payload = GenerationRequest(
        deployment=settings.deployment, prompt="cat", duration=4
    ).model_dump()

    first = await store.create("job-1", payload)
    second = await store.create("job-1", payload)
    assert first["task_id"] == second["task_id"]
    assert (await store.next_active())["status"] == "queued"

    await store.update(first["task_id"], status="running", prompt_id="prompt-1")
    recovered = GatewayStore(settings.db_path)
    active = await recovered.next_active()
    assert active["prompt_id"] == "prompt-1"


@pytest.mark.asyncio
async def test_gateway_does_not_replay_uncertain_submission(monkeypatch, tmp_path):
    settings = _settings(monkeypatch, tmp_path)
    store = GatewayStore(settings.db_path)
    payload = GenerationRequest(
        deployment=settings.deployment, prompt="cat", duration=4
    ).model_dump()
    task = await store.create("job-uncertain", payload)
    await store.update(task["task_id"], status="submitting")

    recovered = GatewayStore(settings.db_path)
    failed = await recovered.get(task["task_id"])
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "submission_uncertain"
    assert await recovered.next_active() is None


def test_comfy_history_helpers():
    descriptor = {"filename": "fishstudio/task.mp4", "subfolder": "", "type": "output"}
    assert _find_video({"92": {"video": [descriptor]}}) == descriptor
    assert "CUDA OOM" in _history_error({
        "status": {
            "messages": [["execution_error", {"exception_message": "CUDA OOM"}]]
        }
    })
