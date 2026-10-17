"""clone_voice 真实实现（P1-12 收尾）与选定产物消费（P1-2/P2-2）测试。

不连真 DB、不发真实 HTTP：provider 工具函数用 monkeypatch 替身，
session 用最小 stub（get/add/flush/commit/refresh/scalar）。
"""

import json
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.providers.base import ProviderResult


class _FakeTool:
    """langchain @tool 的最小替身（provider 侧 getattr(tool, "func", None) 取函数）。"""

    def __init__(self, func):
        self.func = func


class _FakeSession:
    def __init__(self, get_map=None):
        self.get_map = get_map or {}
        self.added = []
        self.commits = 0

    async def get(self, model, key):
        name = getattr(model, "__name__", str(model))
        return self.get_map.get((name, key))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        pass

    async def scalar(self, stmt):
        return None


class _CloneQuoteSession(_FakeSession):
    async def get(self, model, key, **kwargs):
        return await super().get(model, key)

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = str(uuid.uuid4())
        super().add(obj)
        self.get_map[(obj.__class__.__name__, obj.id)] = obj

    async def rollback(self):
        pass


# ---------- QwenTTSProvider.clone_voice ----------


async def test_qwen_clone_voice_success(monkeypatch, tmp_path):
    from app.providers import tts_base
    from app.providers.tts_base import QwenTTSProvider
    from app.tools import qwen_tts

    # provider 校验产物文件存在：造一个假样本音频
    monkeypatch.setattr(tts_base, "BASE_DIR", tmp_path)
    sample = tmp_path / "storage" / "audios" / "sample.wav"
    sample.parent.mkdir(parents=True)
    sample.write_bytes(b"RIFFfake")

    calls = {}

    def fake_cloning(reference_audio, text, language="zh"):
        calls.update({"reference_audio": reference_audio, "text": text})
        return json.dumps(
            {
                "audio_url": "/storage/audios/sample.wav",
                "voice_name": "vc-abc123",
                "provider": "qwen-tts-voice-cloning",
                "request_id": "req-1",
            }
        )

    monkeypatch.setattr(qwen_tts, "qwen_voice_cloning_tool", _FakeTool(fake_cloning))
    r = await QwenTTSProvider(api_key="k").clone_voice("/storage/audios/ref.wav", "角色音色")
    assert r.success and r.raw_metadata["voice_id"] == "vc-abc123"
    assert r.raw_metadata["voice_name"] == "角色音色"
    assert r.artifacts[0]["storage_key"] == "audios/sample.wav"
    assert calls["reference_audio"] == "/storage/audios/ref.wav"


async def test_qwen_clone_voice_error_retryable(monkeypatch):
    from app.providers.tts_base import QwenTTSProvider
    from app.tools import qwen_tts

    monkeypatch.setattr(
        qwen_tts,
        "qwen_voice_cloning_tool",
        _FakeTool(lambda ref, text, language="zh": "Error: 创建音色失败"),
    )
    r = await QwenTTSProvider(api_key="k").clone_voice("/storage/audios/ref.wav", "x")
    assert not r.success and r.error_code == "clone_failed" and r.retryable


async def test_qwen_clone_voice_no_key():
    from app.providers.tts_base import QwenTTSProvider

    r = await QwenTTSProvider(api_key="").clone_voice("/storage/audios/ref.wav", "x")
    assert not r.success and r.error_code == "no_provider"


async def test_qwen_synthesize_reuses_persistent_voice_id(monkeypatch, tmp_path):
    from app.providers import tts_base
    from app.providers.tts_base import QwenTTSProvider, TTSRequest
    from app.tools import qwen_tts

    monkeypatch.setattr(tts_base, "BASE_DIR", tmp_path)
    sample = tmp_path / "storage" / "audios" / "dialogue.wav"
    sample.parent.mkdir(parents=True)
    sample.write_bytes(b"RIFFfake")

    calls = {}

    def fake_synthesis(voice_id, text, language="zh", target_model=""):
        calls.update(
            {
                "voice_id": voice_id,
                "text": text,
                "language": language,
                "target_model": target_model,
            }
        )
        return json.dumps(
            {
                "audio_url": "/storage/audios/dialogue.wav",
                "voice_id": voice_id,
                "target_model": target_model,
                "provider": target_model,
                "request_id": "req-synth-1",
            }
        )

    def unexpected(*args, **kwargs):
        raise AssertionError("已有 voice_id 时不应重新设计或复刻音色")

    monkeypatch.setattr(qwen_tts, "qwen_voice_synthesis_tool", _FakeTool(fake_synthesis))
    monkeypatch.setattr(qwen_tts, "qwen_voice_cloning_tool", _FakeTool(unexpected))
    monkeypatch.setattr(qwen_tts, "qwen_voice_design_tool", _FakeTool(unexpected))

    result = await QwenTTSProvider(api_key="k").synthesize(
        TTSRequest(
            text="你好，欢迎回来。",
            voice_id="vc-persistent-1",
            voice_model="qwen3-tts-vc-2026-01-22",
            voice_reference="/storage/audios/should-not-be-used.wav",
        )
    )

    assert result.success
    assert result.artifacts[0]["storage_key"] == "audios/dialogue.wav"
    assert calls == {
        "voice_id": "vc-persistent-1",
        "text": "你好，欢迎回来。",
        "language": "zh",
        "target_model": "qwen3-tts-vc-2026-01-22",
    }


async def test_mock_clone_voice(monkeypatch, tmp_path):
    from app.providers.tts_base import MockTTSProvider

    monkeypatch.setattr("app.providers.tts_base.AUDIOS_DIR", tmp_path)
    r = await MockTTSProvider().clone_voice("/storage/audios/ref.wav", "x")
    assert r.success and r.raw_metadata["mocked"] is True
    assert r.raw_metadata["voice_id"].startswith("mock_voice_")


async def test_clone_voice_quote_confirm_is_persistent_and_one_time(monkeypatch, tmp_path):
    from app.db.models import CloneVoiceQuoteRecord, GenerationJob, UsageEvent, WorkflowRun, WorkflowStep
    from app.providers.tts_base import QwenTTSProvider
    from app.routers import series as series_router
    from app.services import asset_service, cost_service

    storage_root = tmp_path / "storage"
    reference_path = storage_root / "audios" / "ref.wav"
    reference_path.parent.mkdir(parents=True)
    reference_path.write_bytes(b"RIFFfake")
    monkeypatch.setattr(asset_service, "STORAGE_DIR", storage_root)
    monkeypatch.setattr(QwenTTSProvider, "is_available", lambda self: True)

    project = SimpleNamespace(id="p1", settings={})

    async def owned_project(session, project_id, user):
        return project

    monkeypatch.setattr(series_router, "_owned_project", owned_project)
    asset = SimpleNamespace(id="a1", project_id="p1", type="voice", version=2, name="角色音色")
    artifact = SimpleNamespace(id="ref1", project_id="p1", type="audio", storage_key="audios/ref.wav")
    session = _CloneQuoteSession(
        {
            ("AssetCard", "a1"): asset,
            ("Artifact", "ref1"): artifact,
        }
    )
    user = SimpleNamespace(id=7, is_admin=False)

    async def reserve_budget(fake_session, run_id, amount):
        run = fake_session.get_map[("WorkflowRun", run_id)]
        run.estimated_cost = amount
        return True

    monkeypatch.setattr(cost_service, "reserve_budget", reserve_budget)

    issued = await series_router.get_clone_voice_quote("p1", "a1", "ref1", session=session, user=user)
    result = await series_router.create_clone_voice_job(
        "p1",
        "a1",
        series_router.CloneVoiceRequest(
            reference_audio_artifact_id="ref1",
            voice_name="角色音色",
            confirmed_quote_id=issued["quote_id"],
        ),
        session=session,
        user=user,
    )

    quote_id = series_router._parse_clone_quote_token(issued["quote_id"])
    quote = session.get_map[("CloneVoiceQuoteRecord", quote_id)]
    jobs = [obj for obj in session.added if isinstance(obj, GenerationJob)]
    runs = [obj for obj in session.added if isinstance(obj, WorkflowRun)]
    steps = [obj for obj in session.added if isinstance(obj, WorkflowStep)]
    usage = [obj for obj in session.added if isinstance(obj, UsageEvent)]
    assert isinstance(quote, CloneVoiceQuoteRecord)
    assert quote.consumed_at is not None and quote.consumed_job_id == result["job_id"]
    assert len(jobs) == len(runs) == len(steps) == len(usage) == 1
    assert Decimal(jobs[0].params["quote_snapshot"]["price"]) == Decimal("2")
    assert runs[0].estimated_cost == Decimal(jobs[0].params["estimated_cost"]) == Decimal("2")
    assert usage[0].estimated_cost == Decimal(jobs[0].params["estimated_cost"])

    replay = await series_router.create_clone_voice_job(
        "p1",
        "a1",
        series_router.CloneVoiceRequest(
            reference_audio_artifact_id="ref1",
            confirmed_quote_id=issued["quote_id"],
        ),
        session=session,
        user=user,
    )
    assert replay == {"job_id": result["job_id"], "status": "exists"}
    assert len([obj for obj in session.added if isinstance(obj, GenerationJob)]) == 1


async def test_project_audio_upload_creates_protected_artifact(monkeypatch, tmp_path):
    from app.routers import series as series_router
    from app.services import asset_service

    monkeypatch.setattr(asset_service, "STORAGE_DIR", tmp_path / "storage")
    project = SimpleNamespace(id="p1")

    async def owned_project(session, project_id, user):
        assert project_id == "p1"
        return project

    created = {}

    async def fake_create_artifact(session, **kwargs):
        created.update(kwargs)
        assert (asset_service.STORAGE_DIR / kwargs["storage_key"]).read_bytes() == b"RIFFfake"
        return SimpleNamespace(id="audio-artifact-1", **kwargs)

    class FakeUpload:
        filename = "../reference.wav"
        content_type = "audio/wav"

        def __init__(self):
            self._chunks = [b"RIFFfake", b""]
            self.closed = False

        async def read(self, size=-1):
            return self._chunks.pop(0)

        async def close(self):
            self.closed = True

    upload = FakeUpload()
    monkeypatch.setattr(series_router, "_owned_project", owned_project)
    monkeypatch.setattr(asset_service, "create_artifact", fake_create_artifact)

    result = await series_router.upload_project_audio(
        "p1",
        file=upload,
        session=_FakeSession(),
        user=SimpleNamespace(id=7, is_admin=False),
    )

    assert result["artifact_id"] == "audio-artifact-1"
    assert result["url"] == "/api/artifacts/audio-artifact-1/download"
    assert created["project_id"] == "p1"
    assert created["storage_key"].startswith("audios/reference_")
    assert ".." not in created["storage_key"]
    assert upload.closed is True


def test_clone_voice_quote_token_rejects_tampering():
    from fastapi import HTTPException

    from app.routers import series as series_router

    token = series_router._clone_quote_token("quote-1")
    assert series_router._parse_clone_quote_token(token) == "quote-1"
    with pytest.raises(HTTPException):
        series_router._parse_clone_quote_token(f"{token[:-1]}0")


# ---------- worker execute_clone_voice ----------


def _clone_job(**params):
    return SimpleNamespace(
        id="j1",
        project_id="p1",
        shot_id=None,
        workflow_run_id=None,
        workflow_step_id=None,
        operation="clone_voice",
        provider="",
        model="",
        params=params,
        status="pending",
        progress=0,
        attempt=1,
        max_attempts=3,
        error_code="",
        error="",
        external_task_id="",
        result={},
        finished_at=None,
    )


async def test_execute_clone_voice_updates_asset_and_binds(monkeypatch):
    from app.providers.tts_base import QwenTTSProvider
    from app.worker import generation_worker as gw

    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    asset = SimpleNamespace(id="asset1", type="voice", name="角色音色", status="draft", provider_meta={})
    session = _FakeSession(get_map={("AssetCard", "asset1"): asset})

    async def fake_clone(self, reference_audio_path, voice_name):
        return ProviderResult(
            success=True,
            provider="qwen_tts",
            model="qwen-tts-voice-cloning",
            external_request_id="req-1",
            artifacts=[{"storage_key": "audios/sample.wav", "type": "audio"}],
            raw_metadata={"voice_id": "vc-abc123", "voice_name": voice_name},
        )

    monkeypatch.setattr(QwenTTSProvider, "clone_voice", fake_clone)

    created = {}

    async def fake_create_artifact(session, **kw):
        created.update(kw)
        return SimpleNamespace(id="art1", **kw)

    bound = {}

    async def fake_bind(session, asset_id, artifact_id, role="reference"):
        bound.update({"asset_id": asset_id, "artifact_id": artifact_id, "role": role})

    monkeypatch.setattr(gw.asset_service, "create_artifact", fake_create_artifact)
    monkeypatch.setattr(gw.asset_service, "bind_artifact", fake_bind)

    job = _clone_job(
        asset_id="asset1",
        reference_audio_storage_key="audios/ref.wav",
        voice_name="角色音色",
    )
    await gw.execute_clone_voice(session, job)

    # asset provider_meta 回写 voice_id 等 + 状态 preview_ready
    assert asset.provider_meta["voice_id"] == "vc-abc123"
    assert asset.provider_meta["reference_audio"] == "/storage/audios/ref.wav"
    assert asset.provider_meta["mocked"] is False
    assert asset.status == "preview_ready"
    # 试听样本建档 + 绑定 preview
    assert created["type"] == "audio" and created["storage_key"] == "audios/sample.wav"
    assert bound == {"asset_id": "asset1", "artifact_id": "art1", "role": "preview"}
    assert job.status == "succeeded"
    assert job.result["voice_id"] == "vc-abc123"


async def test_execute_clone_voice_no_provider(monkeypatch):
    """无 qwen key 且 mock 不允许 → no_provider 终态失败。"""
    from app.worker import generation_worker as gw

    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("ALLOW_MOCK_PROVIDERS", "false")
    asset = SimpleNamespace(id="asset1", type="voice", name="x", status="draft", provider_meta={})
    session = _FakeSession(get_map={("AssetCard", "asset1"): asset})
    job = _clone_job(
        asset_id="asset1",
        reference_audio_storage_key="audios/ref.wav",
        voice_name="x",
    )
    await gw.execute_clone_voice(session, job)
    assert job.status == "failed" and job.error_code == "no_provider"


async def test_execute_clone_voice_mock_allowed(monkeypatch, tmp_path):
    """无 qwen key 但 mock 允许 → MockTTSProvider.clone_voice 假 voice_id（标 mocked）。"""
    from app.worker import generation_worker as gw

    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    monkeypatch.setenv("ALLOW_MOCK_PROVIDERS", "true")
    monkeypatch.setattr("app.providers.tts_base.AUDIOS_DIR", tmp_path)
    asset = SimpleNamespace(id="asset1", type="voice", name="x", status="draft", provider_meta={})
    session = _FakeSession(get_map={("AssetCard", "asset1"): asset})

    async def fake_create_artifact(session, **kw):
        return SimpleNamespace(id="art1", **kw)

    async def fake_bind(session, asset_id, artifact_id, role="reference"):
        pass

    monkeypatch.setattr(gw.asset_service, "create_artifact", fake_create_artifact)
    monkeypatch.setattr(gw.asset_service, "bind_artifact", fake_bind)
    job = _clone_job(
        asset_id="asset1",
        reference_audio_storage_key="audios/ref.wav",
        voice_name="x",
    )
    await gw.execute_clone_voice(session, job)
    assert job.status == "succeeded"
    assert asset.provider_meta["voice_id"].startswith("mock_voice_")
    assert asset.provider_meta["mocked"] is True


# ---------- 选定产物消费（P1-2/P2-2） ----------


def _shot(selected=None, selected_type="image"):
    return SimpleNamespace(
        id="sh1",
        selected_image_artifact_id=selected if selected_type == "image" else None,
        selected_video_artifact_id=selected if selected_type == "video" else None,
    )


def _artifact(aid, atype, job_id="j1", storage_key="images/a.png"):
    return SimpleNamespace(id=aid, type=atype, job_id=job_id, storage_key=storage_key)


def _job_for_shot(shot_id="sh1"):
    return SimpleNamespace(id="j1", shot_id=shot_id)


class _SelectSession(_FakeSession):
    """find_first_frame_storage_key 用：get 查 Shot/Artifact/GenerationJob，scalar 走 SQL 回退值。"""

    def __init__(self, get_map=None, fallback_key=None):
        super().__init__(get_map)
        self.fallback_key = fallback_key

    async def scalar(self, stmt):
        return self.fallback_key


async def test_first_frame_prefers_selected_artifact():
    from app.services.job_planner import find_first_frame_storage_key

    session = _SelectSession(
        get_map={
            ("Shot", "sh1"): _shot(selected="a1"),
            ("Artifact", "a1"): _artifact("a1", "image", storage_key="images/selected.png"),
            ("GenerationJob", "j1"): _job_for_shot("sh1"),
        },
        fallback_key="images/latest.png",
    )
    key = await find_first_frame_storage_key(session, "sh1")
    assert key == "images/selected.png"


async def test_first_frame_fallback_latest_when_no_selection():
    from app.services.job_planner import find_first_frame_storage_key

    session = _SelectSession(
        get_map={("Shot", "sh1"): _shot(selected=None)},
        fallback_key="images/latest.png",
    )
    key = await find_first_frame_storage_key(session, "sh1")
    assert key == "images/latest.png"


async def test_first_frame_ignores_selected_from_other_shot():
    """选定的是别 shot 的产物：忽略并回退最新。"""
    from app.services.job_planner import find_first_frame_storage_key

    session = _SelectSession(
        get_map={
            ("Shot", "sh1"): _shot(selected="a1"),
            ("Artifact", "a1"): _artifact("a1", "image", storage_key="images/other.png"),
            ("GenerationJob", "j1"): _job_for_shot("sh2"),  # 属于别的 shot
        },
        fallback_key="images/latest.png",
    )
    key = await find_first_frame_storage_key(session, "sh1")
    assert key == "images/latest.png"


async def test_first_frame_ignores_selected_wrong_type():
    """选定产物不是 image（如视频）：忽略并回退。"""
    from app.services.job_planner import find_first_frame_storage_key

    session = _SelectSession(
        get_map={
            ("Shot", "sh1"): _shot(selected="a1"),
            ("Artifact", "a1"): _artifact("a1", "video", storage_key="videos/x.mp4"),
            ("GenerationJob", "j1"): _job_for_shot("sh1"),
        },
        fallback_key="images/latest.png",
    )
    key = await find_first_frame_storage_key(session, "sh1")
    assert key == "images/latest.png"


# renderer 侧选择（_pick_shot_video）：selected / latest / placeholder 三态
class _PickSession(_FakeSession):
    def __init__(self, get_map=None, latest=None):
        super().__init__(get_map)
        self.latest = latest

    async def execute(self, stmt):
        latest = self.latest
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: latest))


async def test_pick_shot_video_selected():
    from app.services.timeline_renderer import _pick_shot_video

    session = _PickSession(
        get_map={
            ("Shot", "sh1"): _shot(selected="a1", selected_type="video"),
            ("Artifact", "a1"): _artifact("a1", "video", storage_key="videos/sel.mp4"),
            ("GenerationJob", "j1"): _job_for_shot("sh1"),
        }
    )
    artifact, selection = await _pick_shot_video(session, "sh1")
    assert selection == "selected" and artifact.storage_key == "videos/sel.mp4"


async def test_pick_shot_video_latest_fallback():
    from app.services.timeline_renderer import _pick_shot_video

    latest = _artifact("a2", "video", storage_key="videos/latest.mp4")
    session = _PickSession(get_map={("Shot", "sh1"): _shot(selected=None)}, latest=latest)
    artifact, selection = await _pick_shot_video(session, "sh1")
    assert selection == "latest" and artifact.storage_key == "videos/latest.mp4"


async def test_pick_shot_video_other_shot_ignored_then_placeholder():
    from app.services.timeline_renderer import _pick_shot_video

    session = _PickSession(
        get_map={
            ("Shot", "sh1"): _shot(selected="a1", selected_type="video"),
            ("Artifact", "a1"): _artifact("a1", "video", storage_key="videos/other.mp4"),
            ("GenerationJob", "j1"): _job_for_shot("sh2"),  # 属于别的 shot
        },
        latest=None,
    )
    artifact, selection = await _pick_shot_video(session, "sh1")
    assert selection == "placeholder" and artifact is None


# ---------- plan_shot_audio_jobs 传 voice_id（纯逻辑锚定） ----------


def test_tts_job_params_include_voice_id():
    """planner 写入 params.voice_id 的约定（worker execute_tts 读取该键）。"""
    # 直接锚定 worker 读取键名，防止 planner/worker 键名漂移
    import inspect

    from app.worker import generation_worker as gw

    src = inspect.getsource(gw.execute_tts)
    assert 'params.get("voice_id")' in src
    planner_src = inspect.getsource(__import__("app.services.job_planner", fromlist=["x"]))
    assert '"voice_id": voice_id' in planner_src
