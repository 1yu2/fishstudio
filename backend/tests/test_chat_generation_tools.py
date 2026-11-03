"""聊天 Agent 的生图 / 生视频工具改走 GenerationJob：建 job、走预算、复用归属项目、异步等待结果。"""

import asyncio
import inspect
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.db.database as database
from app.db.models import Artifact
from app.services import budget_service, job_service, series_service
from app.services import chat_generation_service as chat_jobs
from app.services.execution_context import ExecutionContext, reset_execution_context, set_execution_context
from app.tools import chat_job_support, routed_image_generation, routed_video_generation

PLAN = {
    "provider": "volcano",
    "model": "seedance-test",
    "resolution": "768P",
    "billable_duration": 5,
    "estimated_total_cost": "3.5",
    "deployment_name": "seedance-test",
}


class FakeSession:
    def __init__(self, state):
        self.state = state

    async def execute(self, *_a, **_k):
        project = self.state.get("project")
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: project))

    async def get(self, model, key):
        if model is Artifact:
            return self.state.get("artifact")
        return SimpleNamespace(owner_id=1)  # Canvas

    async def scalar(self, *_a, **_k):
        return 1

    async def commit(self):
        self.state["commits"] = self.state.get("commits", 0) + 1

    async def rollback(self):
        self.state["rollbacks"] = self.state.get("rollbacks", 0) + 1


class FakeFactory:
    def __init__(self, state):
        self.state = state

    def __call__(self):
        return self

    async def __aenter__(self):
        return FakeSession(self.state)

    async def __aexit__(self, *_exc):
        return False


@pytest.fixture
def env(monkeypatch):
    state = {"created": [], "reserved": [], "projects_created": 0}
    job = SimpleNamespace(
        id="job-1", status="pending", result={}, error="", error_code="", project_id="proj-1"
    )
    state["job"] = job
    state["artifact"] = SimpleNamespace(id="art-1", storage_key="videos/out.mp4", project_id="proj-1")

    async def fake_create_project(session, user_id, **kwargs):
        state["projects_created"] += 1
        state["project_kwargs"] = kwargs
        state["project"] = SimpleNamespace(id="proj-1", user_id=user_id)
        return state["project"]

    async def fake_reserve(session, user_id, estimate):
        state["reserved"].append((user_id, estimate))
        if state.get("over_budget"):
            raise budget_service.BudgetExceeded("超出今日云端花费上限 ¥20")

    async def fake_create_job(session, **kwargs):
        state["created"].append(kwargs)
        return job

    async def fake_get_job(session, job_id):
        return job

    async def fake_resolve_plan(session, **kwargs):
        state["plan_kwargs"] = kwargs
        return dict(PLAN)

    async def noop(*_a, **_k):
        return None

    async def fake_artifact_by_key(session, key):
        return state["owned"].get(key)

    state["owned"] = {
        "images/first.png": SimpleNamespace(project_id="proj-1"),
        "images/src.png": SimpleNamespace(project_id="proj-1"),
    }
    monkeypatch.setattr(chat_jobs, "_artifact_by_key", fake_artifact_by_key)
    monkeypatch.setattr(database, "SessionLocal", FakeFactory(state))
    monkeypatch.setattr(series_service, "create_project", fake_create_project)
    monkeypatch.setattr(budget_service, "reserve", fake_reserve)
    monkeypatch.setattr(job_service, "create_job", fake_create_job)
    monkeypatch.setattr(job_service, "get_job", fake_get_job)
    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", fake_resolve_plan)
    monkeypatch.setattr(chat_jobs.job_event_service, "notify_ready", noop)
    monkeypatch.setattr(chat_jobs, "POLL_INTERVAL_SECONDS", 0.01)
    token = set_execution_context(ExecutionContext(user_id="1", canvas_id="canvas-1"))
    yield state
    reset_execution_context(token)


def _run(tool, **args):
    return asyncio.run(tool.ainvoke(args))


def _succeed(state):
    state["job"].status = "succeeded"
    state["job"].result = {"artifact_id": "art-1", "provider": "volcano", "model": "seedance-test"}


def test_video_tool_creates_generate_video_job_and_reserves_budget(env):
    _succeed(env)
    out = json.loads(
        _run(
            routed_video_generation.generate_volcano_video_tool,
            prompt="海边日落",
            duration=6,
            mode="image",
            image_url="/storage/images/first.png",
        )
    )

    assert len(env["created"]) == 1
    created = env["created"][0]
    assert created["operation"] == "generate_video"
    assert created["project_id"] == "proj-1"
    params = created["params"]
    assert params["prompt"] == "海边日落"
    assert params["duration_ms"] == 6000
    assert params["first_frame"] == "/storage/images/first.png"
    assert params["mode"] == "first_frame"
    assert params["canvas_id"] == "canvas-1" and params["source"] == "chat"
    assert env["reserved"] == [(1, Decimal("3.5"))]
    assert params[budget_service.ESTIMATE_PARAM] == "3.5"
    assert out["job_id"] == "job-1"


def test_over_budget_creates_no_job_and_returns_chinese_hint(env):
    env["over_budget"] = True
    out = json.loads(_run(routed_video_generation.generate_volcano_video_tool, prompt="x"))

    assert env["created"] == []
    assert "超出今日云端花费上限" in out["error"]
    assert env["rollbacks"] == 1


def test_same_canvas_reuses_one_project(env):
    _succeed(env)
    for _ in range(2):
        _run(routed_video_generation.generate_volcano_video_tool, prompt="镜头")

    assert env["projects_created"] == 1
    assert [c["project_id"] for c in env["created"]] == ["proj-1", "proj-1"]
    assert env["project_kwargs"]["project_type"] == "chat"
    assert env["project_kwargs"]["canvas_id"] == "canvas-1"


def test_success_returns_video_url_and_job_id(env):
    _succeed(env)
    out = json.loads(_run(routed_video_generation.generate_volcano_video_tool, prompt="镜头"))

    assert out["video_url"] == "/storage/videos/out.mp4"
    assert out["artifact_url"] == "/api/artifacts/art-1/download"
    assert out["local_path"] == "/storage/videos/out.mp4"
    assert out["job_id"] == "job-1"


def test_failed_job_returns_error_text(env):
    env["job"].status = "failed"
    env["job"].error = "厂商拒绝了请求"
    out = json.loads(_run(routed_video_generation.generate_volcano_video_tool, prompt="镜头"))

    assert "厂商拒绝了请求" in out["error"]
    assert out["job_id"] == "job-1"


def test_timeout_returns_running_message_not_exception(env, monkeypatch):
    monkeypatch.setattr(chat_jobs, "VIDEO_TIMEOUT_SECONDS", 0.05)
    out = json.loads(_run(routed_video_generation.generate_volcano_video_tool, prompt="镜头"))

    assert out["status"] == "running"
    assert "job_id=job-1" in out["message"]


def test_image_tool_creates_generate_image_job(env, monkeypatch):
    async def fake_default_model(session, edit):
        return ""

    monkeypatch.setattr("app.services.node_canvas_service._default_image_model", fake_default_model)
    env["job"].status = "succeeded"
    env["job"].result = {"artifact_ids": ["art-1"]}
    env["artifact"].storage_key = "images/a.png"

    out = json.loads(
        _run(
            routed_image_generation.edit_volcano_image_tool,
            prompt="改成夜景",
            image_url="/storage/images/src.png",
        )
    )

    created = env["created"][0]
    assert created["operation"] == "generate_image"
    assert created["params"]["source_image"] == "/storage/images/src.png"
    assert created["params"]["source"] == "chat"
    assert out["image_url"] == "/storage/images/a.png"
    assert out["artifact_url"] == "/api/artifacts/art-1/download"
    assert out["local_path"] == "/storage/images/a.png"
    assert out["job_id"] == "job-1"


def test_storage_path_traversal_is_rejected(env):
    out = json.loads(
        _run(
            routed_video_generation.generate_volcano_video_tool,
            prompt="x",
            mode="image",
            image_url="/storage/../../etc/passwd",
        )
    )
    assert "不合法" in out["error"] and env["created"] == []


def test_tools_are_async_and_do_not_use_sync_requests():
    tools = [
        routed_video_generation.generate_volcano_video_tool,
        routed_image_generation.generate_volcano_image_tool,
        routed_image_generation.edit_volcano_image_tool,
    ]
    for tool in tools:
        assert inspect.iscoroutinefunction(tool.coroutine)
    for module in (routed_video_generation, routed_image_generation, chat_job_support, chat_jobs):
        source = inspect.getsource(module)
        assert "import requests" not in source and "requests." not in source


def test_concurrent_first_create_falls_back_to_existing_project(env, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    async def racing_create(session, user_id, **kwargs):
        # 另一请求已抢先建好项目，本次提交撞上 canvas_id 唯一约束
        env["project"] = SimpleNamespace(id="proj-winner", user_id=user_id)
        raise IntegrityError("insert", {}, Exception("duplicate canvas_id"))

    monkeypatch.setattr(series_service, "create_project", racing_create)

    async def go():
        async with database.SessionLocal() as session:
            return await chat_jobs.get_or_create_chat_project(session, 1, "canvas-1")

    project = asyncio.run(go())
    assert project.id == "proj-winner"
    assert env["rollbacks"] == 1


# ── 真实 PostgreSQL ──────────────────────────────────────────────────────

from test_node_canvas import _user, pg, requires_pg  # noqa: E402,F401


async def _fake_plan(session, **kwargs):
    return dict(PLAN)


@requires_pg
def test_pg_project_reuse_job_params_and_budget(pg, monkeypatch):
    from sqlalchemy import select

    from app.db.models import Canvas, GenerationJob, SeriesProject

    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _fake_plan)
    monkeypatch.setenv("DAILY_BUDGET_CNY", "20")

    async def go():
        async with pg() as s:
            user = await _user(s, "chat-user")
            s.add(Canvas(id="chat-canvas-1", name="c", owner_id=user.id))
            await s.commit()
            first = await chat_jobs.get_or_create_chat_project(s, user.id, "chat-canvas-1")
            second = await chat_jobs.get_or_create_chat_project(s, user.id, "chat-canvas-1")
            job = await chat_jobs.submit_video_job(
                s, user_id=user.id, canvas_id="chat-canvas-1", prompt="海边", duration=5
            )
            # 超出上限：真实 reserve 抛错，不新增 job
            monkeypatch.setenv("DAILY_BUDGET_CNY", "1")
            with pytest.raises(chat_jobs.ChatGenerationError, match="超出今日云端花费上限"):
                await chat_jobs.submit_video_job(
                    s, user_id=user.id, canvas_id="chat-canvas-1", prompt="再来一个"
                )
            jobs = (await s.execute(select(GenerationJob))).scalars().all()
            projects = (await s.execute(select(SeriesProject))).scalars().all()
            return first, second, job, jobs, projects

    first, second, job, jobs, projects = asyncio.run(go())

    assert first.id == second.id and first.canvas_id == "chat-canvas-1"
    assert first.project_type == "chat" and len(projects) == 1
    assert len(jobs) == 1 and jobs[0].id == job.id
    assert job.operation == "generate_video" and job.project_id == first.id
    assert job.params["source"] == "chat" and job.params["canvas_id"] == "chat-canvas-1"
    assert job.params[budget_service.ESTIMATE_PARAM] == "3.5"


@requires_pg
def test_pg_unbound_canvas_uses_settings_jsonb_filter(pg):
    async def go():
        async with pg() as s:
            user = await _user(s, "chat-user2")
            other = await _user(s, "chat-user3")
            await s.commit()
            a = await chat_jobs.get_or_create_chat_project(s, user.id, "ghost-canvas")
            b = await chat_jobs.get_or_create_chat_project(s, user.id, "ghost-canvas")
            c = await chat_jobs.get_or_create_chat_project(s, user.id, "another-ghost")
            d = await chat_jobs.get_or_create_chat_project(s, other.id, "ghost-canvas")
            return a, b, c, d

    a, b, c, d = asyncio.run(go())

    assert a.id == b.id and a.canvas_id is None
    assert a.settings["chat_canvas_id"] == "ghost-canvas"
    assert len({a.id, c.id, d.id}) == 3


@pytest.mark.parametrize(
    "ref",
    [
        "../.env",
        "/etc/passwd",
        "images/first.png",  # 裸 storage_key 不接受
        "/storage/../.env",
        "/storage/images/../../.env",
        "http://localhost:8000/storage/../.env",
        "http://localhost:8000/etc/passwd",
        "data:image/png;base64,AAAA",
        "file:///etc/passwd",
    ],
)
def test_resolve_media_ref_rejects_unsafe_refs(env, ref):
    async def go():
        async with database.SessionLocal() as session:
            return await chat_jobs.resolve_media_ref(session, 1, ref)

    with pytest.raises(chat_jobs.ChatGenerationError):
        asyncio.run(go())


class _Ctx:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_exc):
        return False


def test_resolve_media_ref_accepts_allowed_forms_and_checks_owner(env, monkeypatch):
    async def go(ref, user_id=1):
        async with database.SessionLocal() as session:
            return await chat_jobs.resolve_media_ref(session, user_id, ref)

    assert asyncio.run(go("/storage/images/first.png")) == "/storage/images/first.png"
    assert asyncio.run(go("http://localhost:8000/storage/images/first.png")) == "/storage/images/first.png"
    assert asyncio.run(go("/storage/public/bgm/a.png")) == "/storage/public/bgm/a.png"
    assert asyncio.run(go("https://cdn.example.com/a.png")) == "https://cdn.example.com/a.png"
    assert asyncio.run(go("/api/artifacts/art-1/download")) == "/storage/videos/out.mp4"
    # 没有 Artifact 记录的本地文件拒绝
    with pytest.raises(chat_jobs.ChatGenerationError):
        asyncio.run(go("/storage/images/unknown.png"))
    # 与 GET /storage 一致：legacy 开关打开时放行无记录的老文件（历史聊天上传）
    monkeypatch.setenv("STORAGE_LEGACY_AUTHENTICATED_READ", "true")
    assert asyncio.run(go("/storage/images/unknown.png")) == "/storage/images/unknown.png"
    monkeypatch.delenv("STORAGE_LEGACY_AUTHENTICATED_READ")

    # 他人的文件：Artifact 所属项目的用户不是当前用户
    class OtherOwnerSession(FakeSession):
        async def scalar(self, *_a, **_k):
            return 999

    monkeypatch.setattr(database, "SessionLocal", lambda: _Ctx(OtherOwnerSession(env)))
    with pytest.raises(chat_jobs.ChatGenerationError):
        asyncio.run(go("/storage/images/first.png"))
    with pytest.raises(chat_jobs.ChatGenerationError):
        asyncio.run(go("/api/artifacts/art-1/download"))


def test_image_edit_job_input_is_readable_by_volcano_provider(env, tmp_path, monkeypatch):
    """edit 工具写入 job 的 source_image，经 VolcanoImageProvider 真实输入解析后得到 base64 data URI。"""
    import app.tools.volcano_image_generation as vig
    from app.providers.image_base import ImageGenerationRequest
    from app.providers.volcano_image import VolcanoImageProvider

    async def fake_default_model(session, edit):
        return ""

    monkeypatch.setattr("app.services.node_canvas_service._default_image_model", fake_default_model)
    (tmp_path / "storage" / "images").mkdir(parents=True)
    (tmp_path / "storage" / "images" / "src.png").write_bytes(b"\x89PNGfake")
    monkeypatch.setattr(vig, "BASE_DIR", tmp_path)
    env["job"].status = "succeeded"
    env["job"].result = {"artifact_ids": ["art-1"]}
    _run(routed_image_generation.edit_volcano_image_tool, prompt="夜景", image_url="/storage/images/src.png")
    source = env["created"][0]["params"]["source_image"]

    sent = {}

    class Resp:
        status_code = 500
        text = "stop"

    def fake_post(url, json=None, **_kw):
        sent.update(json)
        return Resp()

    monkeypatch.setattr("app.providers.volcano_image.requests.post", fake_post)
    provider = VolcanoImageProvider(api_key="k", base_url="http://x", model_name="m")
    with pytest.raises(RuntimeError):
        provider._generate_sync(ImageGenerationRequest(prompt="夜景", source_image=source))

    assert sent["image"].startswith("data:image/png;base64,")
