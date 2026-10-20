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
    workflow = Path(__file__).parent.parent / "gateway" / "workflows" / "video_minimax_h3_t2v.json"
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
    payload = GenerationRequest(deployment=settings.deployment, prompt="cat", duration=4).model_dump()

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
    payload = GenerationRequest(deployment=settings.deployment, prompt="cat", duration=4).model_dump()
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
    assert "CUDA OOM" in _history_error(
        {"status": {"messages": [["execution_error", {"exception_message": "CUDA OOM"}]]}}
    )


# ── 图生视频（首帧 / 首尾帧） ─────────────────────────────────────────────

import asyncio  # noqa: E402
import base64  # noqa: E402

from gateway.h3_gateway import (  # noqa: E402
    GatewayService,
    decode_image_reference,
    detect_frame_inputs,
)

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG-frame").decode()


def _i2v_settings(monkeypatch, tmp_path, modes="text,first_frame,first_last_frame", **env):
    monkeypatch.setenv("H3_GATEWAY_MODES", modes)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return _settings(monkeypatch, tmp_path)


def _i2v_request(mode="first_frame", references=None):
    if references is None:
        references = [{"type": "image", "role": "first_frame", "url": PNG}]
        if mode == "first_last_frame":
            references.append({"type": "image", "role": "last_frame", "url": PNG})
    return GenerationRequest(
        deployment="h3-fl2va-int8-convrot",
        prompt="cat turns around",
        duration=4,
        mode=mode,
        aspect_ratio="9:16",
        references=references,
    )


def test_modes_are_opt_in_and_validated(monkeypatch, tmp_path):
    assert _settings(monkeypatch, tmp_path).modes == ("text",)
    monkeypatch.setenv("H3_GATEWAY_MODES", "text,reference")
    with pytest.raises(RuntimeError, match="未知模式"):
        Settings()


@pytest.mark.parametrize(
    ("mode", "references", "message"),
    [
        ("first_frame", [], "缺少参考图"),
        ("first_last_frame", [{"role": "first_frame", "url": PNG}], "last_frame"),
        ("first_frame", [{"role": "reference_image", "url": PNG}], "不接受"),
        ("text", [{"role": "first_frame", "url": PNG}], "不接受"),
        ("first_frame", [{"role": "first_frame", "url": "https://example.com/a.png"}], "data:image"),
        ("first_frame", [{"role": "first_frame", "url": "data:image/gif;base64,AA=="}], "PNG"),
        ("first_frame", [{"role": "first_frame", "url": "data:image/png;base64,!!!"}], "解码"),
        (
            "first_frame",
            [{"role": "first_frame", "url": PNG}, {"role": "first_frame", "url": PNG}],
            "重复",
        ),
    ],
)
def test_frame_reference_validation(monkeypatch, tmp_path, mode, references, message):
    builder = WorkflowBuilder(_i2v_settings(monkeypatch, tmp_path))
    with pytest.raises(ValueError, match=message):
        builder.build("task", _i2v_request(mode, references))


def test_image_size_limit(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="大小"):
        decode_image_reference(PNG, max_bytes=4)


def test_build_wires_uploaded_frames_into_h3_node(monkeypatch, tmp_path):
    builder = WorkflowBuilder(_i2v_settings(monkeypatch, tmp_path))

    workflow, width, height = builder.build(
        "task-9",
        _i2v_request("first_last_frame"),
        frame_inputs={"first_frame": "start_image", "last_frame": "end_image"},
        uploaded_frames={"first_frame": "fs/first.png", "last_frame": "fs/last.png"},
    )

    assert (width, height) == (288, 512)  # 默认 preview 档位
    h3 = workflow["140:131"]["inputs"]
    assert h3["start_image"] == ["fishstudio_frame_first_frame", 0]
    assert h3["end_image"] == ["fishstudio_frame_last_frame", 0]
    assert workflow["fishstudio_frame_first_frame"] == {
        "class_type": "LoadImage",
        "inputs": {"image": "fs/first.png"},
        "_meta": {"title": "FishStudio first_frame"},
    }
    # 模板本身不被修改
    assert "start_image" not in builder.template["140:131"]["inputs"]


def test_build_fails_when_node_has_no_image_input(monkeypatch, tmp_path):
    builder = WorkflowBuilder(_i2v_settings(monkeypatch, tmp_path))
    with pytest.raises(ValueError, match="图片输入"):
        builder.build("t", _i2v_request(), frame_inputs={}, uploaded_frames={"first_frame": "a.png"})


@pytest.mark.parametrize(
    ("optional", "expected"),
    [
        (
            {"start_image": ["IMAGE"], "end_image": ["IMAGE"], "seed": ["INT", {}]},
            {"first_frame": "start_image", "last_frame": "end_image"},
        ),
        (
            {"first_frame": ["IMAGE"], "last_frame": ["IMAGE"]},
            {"first_frame": "first_frame", "last_frame": "last_frame"},
        ),
        ({"image": ["IMAGE"]}, {"first_frame": "image"}),
        ({"mask": ["MASK"]}, {}),
    ],
)
def test_detect_frame_inputs(optional, expected):
    info = {"input": {"required": {"prompt": ["STRING", {}]}, "optional": optional}}
    assert detect_frame_inputs(info) == expected


class FakeComfy:
    def __init__(self, info=None):
        self.info = (
            info
            if info is not None
            else {"input": {"optional": {"start_image": ["IMAGE"], "end_image": ["IMAGE"]}}}
        )
        self.uploads = []
        self.submitted = []
        self.object_info_calls = 0

    async def object_info(self, class_type):
        self.object_info_calls += 1
        assert class_type == "MiniMaxH3ImageToVideo"
        return self.info

    async def upload_image(self, data, filename):
        self.uploads.append((data, filename))
        return f"fishstudio/{filename}"

    async def submit(self, workflow, client_id):
        self.submitted.append(workflow)
        return "prompt-1"


def _service(settings, comfy):
    return GatewayService(settings, GatewayStore(settings.db_path), comfy)


def test_submit_uploads_frames_and_submits_wired_workflow(monkeypatch, tmp_path):
    settings = _i2v_settings(monkeypatch, tmp_path)
    comfy = FakeComfy()
    service = _service(settings, comfy)

    async def _run():
        task = await service.store.create("key-1", _i2v_request("first_last_frame").model_dump())
        await service._submit(task)
        await service._submit(await service.store.create("key-2", _i2v_request().model_dump()))
        return await service.store.get(task["task_id"])

    stored = asyncio.run(_run())

    assert stored["status"] == "running" and stored["prompt_id"] == "prompt-1"
    assert [data for data, _ in comfy.uploads[:2]] == [b"\x89PNG-frame", b"\x89PNG-frame"]
    assert comfy.uploads[0][1].endswith("_first_frame.png")
    h3 = comfy.submitted[0]["140:131"]["inputs"]
    assert h3["start_image"] == ["fishstudio_frame_first_frame", 0]
    assert h3["end_image"] == ["fishstudio_frame_last_frame", 0]
    assert comfy.object_info_calls == 1  # 识别结果缓存


def test_configured_input_names_skip_detection(monkeypatch, tmp_path):
    settings = _i2v_settings(
        monkeypatch,
        tmp_path,
        H3_GATEWAY_FIRST_FRAME_INPUT="image_first",
        H3_GATEWAY_LAST_FRAME_INPUT="image_last",
    )
    comfy = FakeComfy(info={})
    service = _service(settings, comfy)

    async def _run():
        task = await service.store.create("key", _i2v_request("first_last_frame").model_dump())
        await service._submit(task)

    asyncio.run(_run())

    assert comfy.object_info_calls == 0
    assert comfy.submitted[0]["140:131"]["inputs"]["image_first"] == ["fishstudio_frame_first_frame", 0]


def test_submit_fails_clearly_when_frame_input_unknown(monkeypatch, tmp_path):
    settings = _i2v_settings(monkeypatch, tmp_path)
    service = _service(settings, FakeComfy(info={"input": {"optional": {}}}))

    async def _run():
        task = await service.store.create("key", _i2v_request().model_dump())
        await service._submit(task)
        return await service.store.get(task["task_id"])

    stored = asyncio.run(_run())

    assert stored["status"] == "failed"
    assert stored["error"]["code"] == "invalid_request"
    assert "图片输入" in stored["error"]["message"]


def test_fishstudio_local_h3_payload_is_accepted_by_gateway(monkeypatch, tmp_path):
    """合同测试：FishStudio 侧 LocalH3Provider 构造的首帧请求能通过 Gateway 校验。"""
    from app.providers import local_h3
    from app.providers.base import VideoGenerationRequest

    storage = tmp_path / "storage" / "images"
    storage.mkdir(parents=True)
    (storage / "first.png").write_bytes(b"\x89PNG-frame")
    monkeypatch.setattr(local_h3, "BASE_DIR", tmp_path)
    provider = local_h3.LocalH3Provider(
        base_url="http://h3.test",
        api_key="secret",
        model_name="MiniMax-H3",
        deployment_name="h3-fl2va-int8-convrot",
        capabilities={"modes": ["text", "first_frame"]},
        enabled=True,
    )
    payload = provider._payload(
        VideoGenerationRequest(prompt="cat", duration=4, mode="first_frame", first_frame="images/first.png"),
        "MiniMax-H3",
    )

    builder = WorkflowBuilder(_i2v_settings(monkeypatch, tmp_path / "gw"))
    frames = builder.frame_references(GenerationRequest.model_validate(payload))

    assert decode_image_reference(frames["first_frame"], max_bytes=1024)[0] == b"\x89PNG-frame"


def test_progress_tracker_maps_comfy_messages_to_overall_progress():
    from gateway.h3_gateway import ProgressTracker

    tracker = ProgressTracker()
    tracker.started("p1")
    assert tracker.get("p1") == 5
    tracker.handle({"type": "executing", "data": {"prompt_id": "p1", "node": "140:128"}})
    assert tracker.get("p1") == 10
    tracker.handle({"type": "progress", "data": {"prompt_id": "p1", "node": "140:125", "value": 4, "max": 8}})
    assert tracker.get("p1") == 53
    tracker.handle({"type": "progress", "data": {"prompt_id": "p1", "node": "140:999", "value": 1, "max": 1}})
    tracker.handle({"type": "executing", "data": {"prompt_id": "p1", "node": "140:128"}})
    assert tracker.get("p1") == 53  # 只增不减，其他节点的进度不算
    tracker.handle({"type": "executing", "data": {"prompt_id": "p1", "node": "140:122"}})
    assert tracker.get("p1") == 92
    tracker.handle({"type": "status", "data": {}})
    tracker.forget("p1")
    assert tracker.get("p1") == 0


def test_queue_position_and_progress_in_task_response(monkeypatch, tmp_path):
    from gateway.h3_gateway import _public_task

    settings = _i2v_settings(monkeypatch, tmp_path)
    service = _service(settings, FakeComfy())

    async def _run():
        first = await service.store.create("k1", _i2v_request().model_dump())
        second = await service.store.create("k2", _i2v_request().model_dump())
        third = await service.store.create("k3", _i2v_request().model_dump())
        await service._submit(first)
        return [
            await service.store.ahead_of(await service.store.get(t["task_id"]))
            for t in (first, second, third)
        ], await service.store.get(third["task_id"])

    ahead, third = asyncio.run(_run())

    assert ahead == [0, 1, 2]
    assert _public_task(third, queue_position=2)["queue_position"] == 2
    running = {**third, "status": "running"}
    assert _public_task(running, progress=40, queue_position=2)["queue_position"] == 0  # 只有排队中才有位置
    assert _public_task(running, progress=40)["progress"] == 40
    assert _public_task({**third, "status": "succeeded"}, progress=40)["progress"] == 100


def test_cleanup_removes_only_expired_fishstudio_files(monkeypatch, tmp_path):
    import os

    from gateway.h3_gateway import cleanup_old_files

    output, inputs = tmp_path / "comfy_out", tmp_path / "comfy_in"
    settings = _i2v_settings(
        monkeypatch, tmp_path, H3_COMFY_OUTPUT_DIR=str(output), H3_COMFY_INPUT_DIR=str(inputs)
    )
    (output / "fishstudio").mkdir(parents=True)
    (output / "other").mkdir()
    inputs.mkdir()
    old_artifact = settings.artifact_dir / "aaa.mp4"
    new_artifact = settings.artifact_dir / "bbb.mp4"
    old_output = output / "fishstudio" / "h3_x_00001_.mp4"
    foreign_output = output / "other" / "keep.mp4"
    old_frame = inputs / "fishstudio_h3_x_first_frame.png"
    foreign_input = inputs / "my_photo.png"
    for path in (old_artifact, new_artifact, old_output, foreign_output, old_frame, foreign_input):
        path.write_bytes(b"x")
    now = 1_000_000.0
    for path in (old_artifact, old_output, foreign_output, old_frame, foreign_input):
        os.utime(path, (now - 25 * 3600, now - 25 * 3600))
    os.utime(new_artifact, (now - 3600, now - 3600))

    assert cleanup_old_files(settings, now=now) == 3
    assert not old_artifact.exists() and not old_output.exists() and not old_frame.exists()
    assert new_artifact.exists() and foreign_output.exists() and foreign_input.exists()


def test_cleanup_disabled_with_zero_retention(monkeypatch, tmp_path):
    from gateway.h3_gateway import cleanup_old_files

    settings = _i2v_settings(monkeypatch, tmp_path, H3_GATEWAY_RETENTION_HOURS="0")
    (settings.artifact_dir / "old.mp4").write_bytes(b"x")
    assert cleanup_old_files(settings, now=10**12) == 0
