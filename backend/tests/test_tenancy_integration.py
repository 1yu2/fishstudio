"""真实 PostgreSQL 多租户隔离集成测试（review 文档 5.4 第 2 条）。

用 env `TEST_DATABASE_URL` 指向专用测试库（如
`postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/fishstudio_test_tenancy`），
未设置则整模块 skip。测试会 drop/create 该库，**禁止指向任何有数据的库**。

覆盖：
- 用户 B 访问用户 A 的项目详情 / jobs / storyboard / assets / artifact download /
  feedback 全部 403/404；
- 无主项目（user_id NULL）：普通用户拒绝、admin 可访问、claim 后 owner 可访问；
- feedback 跨项目 step_id 拒绝（review 0.3）。
"""

import asyncio
import os
from urllib.parse import urlparse

import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL 未设置，跳过真实 PG 多租户集成测试"),
    pytest.mark.no_bypass_auth,
]


def _admin_dsn() -> dict:
    parsed = urlparse(TEST_DATABASE_URL.replace("postgresql+asyncpg", "postgresql"))
    return {
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
    }


def _target_db() -> str:
    return urlparse(TEST_DATABASE_URL.replace("postgresql+asyncpg", "postgresql")).path.lstrip("/")


async def _recreate_database() -> None:
    import asyncpg

    conn = await asyncpg.connect(database="postgres", **_admin_dsn())
    try:
        db = _target_db()
        # 前面的测试（启动过 app lifespan 的 TestClient、worker 等）可能还留着连接，先断开再重建
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid <> pg_backend_pid()",
            db,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db}"')
        await conn.execute(f'CREATE DATABASE "{db}"')
    finally:
        await conn.close()


async def _drop_database() -> None:
    import asyncpg

    conn = await asyncpg.connect(database="postgres", **_admin_dsn())
    try:
        db = _target_db()
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid <> pg_backend_pid()",
            db,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db}"')
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def tenancy():
    """建专用库 + 迁移到 head + 造两个用户/项目，返回 TestClient 与数据句柄。"""
    asyncio.run(_recreate_database())

    mp = pytest.MonkeyPatch()
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.db.database as database
    import app.db.migrations as migrations

    # 1) schema 迁移到 head
    mig_engine = create_async_engine(TEST_DATABASE_URL)
    mp.setattr(migrations, "engine", mig_engine)
    settings = migrations.get_settings()
    mp.setattr(settings, "database_url", TEST_DATABASE_URL)
    asyncio.run(migrations.bootstrap_and_upgrade())

    # 2) app 的 engine/session 指向测试库
    # NullPool：fixture 的 seed 与 TestClient 运行在不同事件循环，
    # 连接不能跨循环复用（否则 asyncpg "another operation is in progress"）
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    mp.setattr(database, "engine", engine)
    mp.setattr(database, "SessionLocal", factory)

    # 3) 跳过 startup 的 init_db（schema 已迁移好）
    async def _noop_init_db():
        return None

    mp.setattr("app.db.init_db.init_db", _noop_init_db)
    mp.setattr("app.main.init_db", _noop_init_db)

    # 4) 造数据：用户 A/B + admin、各自项目、A 的 job/artifact、B 的 run/step、无主项目
    from app.auth.security import create_access_token, hash_password
    from app.db.models import (
        Artifact,
        AssetCard,
        Canvas,
        Episode,
        GenerationJob,
        SeriesProject,
        User,
        WorkflowRun,
        WorkflowStep,
    )

    async def _seed():
        async with factory() as s:
            a = User(username="user_a", password_hash=hash_password("pass-a"), is_admin=False)
            b = User(username="user_b", password_hash=hash_password("pass-b"), is_admin=False)
            admin = User(username="admin_t", password_hash=hash_password("pass-adm"), is_admin=True)
            s.add_all([a, b, admin])
            await s.flush()

            proj_a = SeriesProject(user_id=a.id, title="A 的项目")
            proj_b = SeriesProject(user_id=b.id, title="B 的项目")
            orphan = SeriesProject(user_id=None, title="历史无主项目")
            s.add_all([proj_a, proj_b, orphan])
            s.add_all(
                [
                    Canvas(id="tenant-canvas-a", owner_id=a.id, name="A 私有画布"),
                    Canvas(id="tenant-canvas-orphan", owner_id=None, name="历史无主画布"),
                    Canvas(id="tenant-canvas-orphan-delete", owner_id=None, name="管理员可删除"),
                    Canvas(id="tenant-canvas-orphan-claim", owner_id=None, name="管理员可认领"),
                ]
            )
            personal_a = AssetCard(scope="personal", user_id=a.id, type="character", name="A 私有角色")
            orphan_asset = AssetCard(scope="personal", user_id=None, type="character", name="历史无主角色")
            s.add_all([personal_a, orphan_asset])
            await s.flush()
            s.add_all(
                [
                    Episode(project_id=proj_a.id, episode_no=1),
                    Episode(project_id=proj_b.id, episode_no=1),
                    Episode(project_id=orphan.id, episode_no=1),
                ]
            )
            job_a = GenerationJob(
                project_id=proj_a.id,
                operation="generate_video",
                idempotency_key="video:rA:node:shA:attempt0",
                status="succeeded",
            )
            s.add(job_a)
            await s.flush()
            art_a = Artifact(
                project_id=proj_a.id,
                job_id=job_a.id,
                type="video",
                storage_key="videos/a.mp4",
            )
            run_b = WorkflowRun(project_id=proj_b.id)
            s.add_all([art_a, run_b])
            await s.flush()
            step_b = WorkflowStep(run_id=run_b.id, node_key="shot.video.generate", node_type="x")
            s.add(step_b)
            await s.commit()
            return Simple(
                a=a.id,
                b=b.id,
                admin=admin.id,
                proj_a=proj_a.id,
                proj_b=proj_b.id,
                orphan=orphan.id,
                art_a=art_a.id,
                step_b=step_b.id,
                personal_a=personal_a.id,
                orphan_asset=orphan_asset.id,
            )

    class Simple:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    ids = asyncio.run(_seed())

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield Simple(
            client=client,
            ids=ids,
            token_a=create_access_token("user_a"),
            token_b=create_access_token("user_b"),
            token_admin=create_access_token("admin_t"),
        )

    asyncio.run(engine.dispose())
    asyncio.run(mig_engine.dispose())
    mp.undo()
    asyncio.run(_drop_database())


def _h(token):
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────
# 跨用户隔离
# ─────────────────────────────────────────


class TestCrossUserIsolation:
    def test_owner_can_read_own_project(self, tenancy):
        r = tenancy.client.get(f"/api/series/projects/{tenancy.ids.proj_a}", headers=_h(tenancy.token_a))
        assert r.status_code == 200

    def test_project_detail_403(self, tenancy):
        r = tenancy.client.get(f"/api/series/projects/{tenancy.ids.proj_a}", headers=_h(tenancy.token_b))
        assert r.status_code == 403

    def test_jobs_404(self, tenancy):
        r = tenancy.client.get(f"/api/jobs?project_id={tenancy.ids.proj_a}", headers=_h(tenancy.token_b))
        assert r.status_code == 404

    def test_storyboard_403(self, tenancy):
        r = tenancy.client.get(
            f"/api/series/projects/{tenancy.ids.proj_a}/storyboard", headers=_h(tenancy.token_b)
        )
        assert r.status_code == 403

    def test_assets_403(self, tenancy):
        r = tenancy.client.get(
            f"/api/series/projects/{tenancy.ids.proj_a}/assets", headers=_h(tenancy.token_b)
        )
        assert r.status_code == 403

    def test_artifact_download_404(self, tenancy):
        r = tenancy.client.get(f"/api/artifacts/{tenancy.ids.art_a}/download", headers=_h(tenancy.token_b))
        assert r.status_code == 404

    def test_feedback_on_others_artifact_403(self, tenancy):
        r = tenancy.client.post(
            f"/api/series/artifacts/{tenancy.ids.art_a}/feedback",
            json={"action": "rated", "score": 5},
            headers=_h(tenancy.token_b),
        )
        assert r.status_code == 403

    def test_no_token_401(self, tenancy):
        r = tenancy.client.get(f"/api/series/projects/{tenancy.ids.proj_a}")
        assert r.status_code == 401


# ─────────────────────────────────────────
# 无主项目（review 0.4）
# ─────────────────────────────────────────


class TestOrphanProject:
    def test_orphan_normal_user_403(self, tenancy):
        r = tenancy.client.get(f"/api/series/projects/{tenancy.ids.orphan}", headers=_h(tenancy.token_a))
        assert r.status_code == 403

    def test_orphan_admin_200(self, tenancy):
        r = tenancy.client.get(f"/api/series/projects/{tenancy.ids.orphan}", headers=_h(tenancy.token_admin))
        assert r.status_code == 200

    def test_claim_then_owner_access(self, tenancy):
        # admin 把无主项目认领给用户 B
        r = tenancy.client.post(
            f"/api/series/projects/{tenancy.ids.orphan}/claim",
            json={"user_id": tenancy.ids.b},
            headers=_h(tenancy.token_admin),
        )
        assert r.status_code == 200, r.text
        assert r.json()["user_id"] == tenancy.ids.b

        # 认领后 owner（B）可访问
        r = tenancy.client.get(f"/api/series/projects/{tenancy.ids.orphan}", headers=_h(tenancy.token_b))
        assert r.status_code == 200

        # 已认领的项目不能重复 claim
        r = tenancy.client.post(
            f"/api/series/projects/{tenancy.ids.orphan}/claim",
            json={"user_id": tenancy.ids.a},
            headers=_h(tenancy.token_admin),
        )
        assert r.status_code == 409

    def test_claim_requires_admin(self, tenancy):
        r = tenancy.client.post(
            f"/api/series/projects/{tenancy.ids.orphan}/claim",
            json={"user_id": tenancy.ids.a},
            headers=_h(tenancy.token_a),
        )
        assert r.status_code == 403


# ─────────────────────────────────────────
# feedback 跨项目 step_id（review 0.3）
# ─────────────────────────────────────────


class TestFeedbackCrossStep:
    def test_cross_project_step_rejected(self, tenancy):
        """A 的 artifact + B 项目的 step_id → 403。"""
        r = tenancy.client.post(
            f"/api/series/artifacts/{tenancy.ids.art_a}/feedback",
            json={"action": "selected", "step_id": tenancy.ids.step_b},
            headers=_h(tenancy.token_a),
        )
        assert r.status_code == 403

    def test_fake_step_rejected(self, tenancy):
        r = tenancy.client.post(
            f"/api/series/artifacts/{tenancy.ids.art_a}/feedback",
            json={"action": "selected", "step_id": "00000000-0000-0000-0000-000000000000"},
            headers=_h(tenancy.token_a),
        )
        assert r.status_code == 400  # step 不存在 → ValueError → 400

    def test_rated_without_step_ok(self, tenancy):
        r = tenancy.client.post(
            f"/api/series/artifacts/{tenancy.ids.art_a}/feedback",
            json={"action": "rated", "score": 4},
            headers=_h(tenancy.token_a),
        )
        assert r.status_code == 200, r.text


@pytest.fixture
def private_workspace(monkeypatch, tmp_path):
    from app.services import workspace_service

    monkeypatch.setattr(workspace_service, "get_workspace_dir", lambda: tmp_path)
    return tmp_path


class TestWorkspaceIsolation:
    def test_agent_memory_is_private(self, tenancy, private_workspace):
        from app.services import workspace_service
        from app.services.execution_context import (
            ExecutionContext,
            reset_execution_context,
            set_execution_context,
        )
        from app.tools.workspace_tools import write_memory

        token = set_execution_context(ExecutionContext(user_id=str(tenancy.ids.a)))
        try:
            result = write_memory.invoke({"section": "用户偏好记录", "content": "A 的私密创作偏好"})
        finally:
            reset_execution_context(token)
        assert result.startswith("✅")
        assert "A 的私密创作偏好" in workspace_service.get_workspace_context(tenancy.ids.a)
        assert "A 的私密创作偏好" not in workspace_service.get_workspace_context(tenancy.ids.b)
        assert "A 的私密创作偏好" not in workspace_service.get_workspace_context(None)
        response = tenancy.client.get("/api/settings/workspace/MEMORY.md", headers=_h(tenancy.token_b))
        assert response.status_code == 200
        assert "A 的私密创作偏好" not in response.json()["content"]

    @pytest.mark.parametrize("filename", ["USER.md", "MEMORY.md"])
    def test_personal_files_api(self, tenancy, private_workspace, filename):
        url = f"/api/settings/workspace/{filename}"
        for token, content in [(tenancy.token_a, "A 私密内容"), (tenancy.token_b, "B 私密内容")]:
            assert tenancy.client.put(url, headers=_h(token), json={"content": content}).status_code == 200
            assert tenancy.client.get(url, headers=_h(token)).json()["content"] == content
        targeted = f"{url}?user_id={tenancy.ids.a}"
        assert tenancy.client.get(targeted, headers=_h(tenancy.token_b)).status_code == 403
        assert (
            tenancy.client.put(targeted, headers=_h(tenancy.token_b), json={"content": "越权"}).status_code
            == 403
        )
        listed = tenancy.client.get("/api/settings/workspace", headers=_h(tenancy.token_b))
        assert listed.status_code == 200
        assert "A 私密内容" not in listed.text
        assert "A 私密内容" == tenancy.client.get(url, headers=_h(tenancy.token_a)).json()["content"]

    @pytest.mark.parametrize("filename", ["AGENTS.md", "TOOLS.md", "IDENTITY.md", "SOUL.md"])
    def test_global_files_admin_write_only(self, tenancy, private_workspace, filename):
        url = f"/api/settings/workspace/{filename}"
        assert (
            tenancy.client.put(url, headers=_h(tenancy.token_a), json={"content": "规则"}).status_code == 403
        )
        assert (
            tenancy.client.put(url, headers=_h(tenancy.token_admin), json={"content": "共享规则"}).status_code
            == 200
        )
        assert tenancy.client.get(url, headers=_h(tenancy.token_b)).json()["content"] == "共享规则"

    def test_legacy_global_user_files_never_injected(self, tenancy, private_workspace):
        from app.services import workspace_service

        for filename in ("USER.md", "MEMORY.md"):
            (private_workspace / filename).write_text("历史私密内容", encoding="utf-8")
        for user_id in (tenancy.ids.a, tenancy.ids.b, tenancy.ids.admin, None):
            assert "历史私密内容" not in workspace_service.get_workspace_context(user_id)
        assert (
            "历史私密内容"
            not in tenancy.client.get("/api/settings/workspace", headers=_h(tenancy.token_admin)).text
        )

    def test_memory_tool_requires_identity(self, private_workspace):
        from app.services.execution_context import (
            ExecutionContext,
            reset_execution_context,
            set_execution_context,
        )
        from app.tools.workspace_tools import write_memory

        token = set_execution_context(ExecutionContext())
        try:
            assert write_memory.invoke({"section": "角色资产", "content": "不得写入"}).startswith("❌")
        finally:
            reset_execution_context(token)
        assert not list(private_workspace.rglob("MEMORY.md"))

    @pytest.mark.parametrize("user_id", ["../1", "/tmp/1", "0", "-1", "abc"])
    def test_workspace_rejects_invalid_user_id(self, private_workspace, user_id):
        from app.services import workspace_service

        with pytest.raises(ValueError):
            workspace_service.save_workspace_file("MEMORY.md", "不得写入", user_id)


class TestCanvasIsolation:
    def test_orphan_canvas_hidden_and_protected(self, tenancy):
        for token in (tenancy.token_a, tenancy.token_b):
            headers = _h(token)
            rows = tenancy.client.get("/api/canvases", headers=headers).json()
            assert "tenant-canvas-orphan" not in {row["id"] for row in rows}
            assert (
                tenancy.client.delete("/api/canvases/tenant-canvas-orphan", headers=headers).status_code
                == 404
            )
            assert (
                tenancy.client.post(
                    "/api/canvases", headers=headers, json={"id": "tenant-canvas-orphan"}
                ).status_code
                == 403
            )
            assert (
                tenancy.client.post(
                    "/api/chat", headers=headers, json={"message": "hi", "canvas_id": "tenant-canvas-orphan"}
                ).status_code
                == 403
            )

    def test_canvas_cross_user_read_write_delete(self, tenancy):
        headers = _h(tenancy.token_b)
        rows = tenancy.client.get("/api/canvases", headers=headers).json()
        assert "tenant-canvas-a" not in {row["id"] for row in rows}
        assert (
            tenancy.client.post("/api/canvases", headers=headers, json={"id": "tenant-canvas-a"}).status_code
            == 403
        )
        assert tenancy.client.delete("/api/canvases/tenant-canvas-a", headers=headers).status_code == 404
        assert (
            tenancy.client.post(
                "/api/chat", headers=headers, json={"message": "hi", "canvas_id": "tenant-canvas-a"}
            ).status_code
            == 403
        )

    def test_admin_can_manage_orphan_canvas(self, tenancy):
        headers = _h(tenancy.token_admin)
        rows = tenancy.client.get("/api/canvases", headers=headers).json()
        assert "tenant-canvas-orphan" in {row["id"] for row in rows}
        assert "tenant-canvas-a" not in {row["id"] for row in rows}
        assert (
            tenancy.client.delete("/api/canvases/tenant-canvas-orphan-delete", headers=headers).status_code
            == 200
        )
        assert (
            tenancy.client.post(
                "/api/canvases", headers=headers, json={"id": "tenant-canvas-orphan-claim"}
            ).status_code
            == 200
        )
        assert (
            tenancy.client.post(
                "/api/canvases", headers=_h(tenancy.token_a), json={"id": "tenant-canvas-orphan-claim"}
            ).status_code
            == 403
        )

    def test_new_canvas_ignores_supplied_owner(self, tenancy):
        assert (
            tenancy.client.post(
                "/api/canvases",
                headers=_h(tenancy.token_a),
                json={"id": "tenant-new-canvas", "owner_id": tenancy.ids.b},
            ).status_code
            == 200
        )
        rows_a = tenancy.client.get("/api/canvases", headers=_h(tenancy.token_a)).json()
        rows_b = tenancy.client.get("/api/canvases", headers=_h(tenancy.token_b)).json()
        assert "tenant-new-canvas" in {row["id"] for row in rows_a}
        assert "tenant-new-canvas" not in {row["id"] for row in rows_b}
        assert (
            tenancy.client.delete("/api/canvases/tenant-new-canvas", headers=_h(tenancy.token_a)).status_code
            == 200
        )


class TestSharedDataAudit:
    @pytest.mark.parametrize("asset_key", ["personal_a", "orphan_asset"])
    def test_personal_and_orphan_assets_cannot_be_versioned_by_b(self, tenancy, asset_key):
        asset_id = getattr(tenancy.ids, asset_key)
        response = tenancy.client.post(
            f"/api/series/assets/{asset_id}/versions", headers=_h(tenancy.token_b), json={"updates": {}}
        )
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "tool_name,args",
        [
            ("init_skill_tool", {"skill_name": "tenant-test"}),
            ("write_skill_file_tool", {"path": "tenant-test/SKILL.md", "content": "私密内容"}),
            ("delete_skill_file_tool", {"path": "tenant-test/SKILL.md"}),
        ],
    )
    def test_shared_skill_tools_require_admin(self, tenancy, monkeypatch, tmp_path, tool_name, args):
        from app.services.execution_context import (
            ExecutionContext,
            reset_execution_context,
            set_execution_context,
        )
        from app.tools import skill_tools

        monkeypatch.setattr(skill_tools, "_CUSTOM_ROOT", tmp_path)
        tool = getattr(skill_tools, tool_name)
        for user_id in (None, str(tenancy.ids.a), str(tenancy.ids.b)):
            token = set_execution_context(ExecutionContext(user_id=user_id))
            try:
                assert "仅管理员" in asyncio.run(tool.ainvoke(args))
            finally:
                reset_execution_context(token)
        assert not list(tmp_path.iterdir())

    def test_admin_can_write_shared_skill(self, tenancy, monkeypatch, tmp_path):
        from app.services.execution_context import (
            ExecutionContext,
            reset_execution_context,
            set_execution_context,
        )
        from app.tools import skill_tools

        monkeypatch.setattr(skill_tools, "_CUSTOM_ROOT", tmp_path)
        token = set_execution_context(ExecutionContext(user_id=str(tenancy.ids.admin)))
        try:
            assert asyncio.run(
                skill_tools.write_skill_file_tool.ainvoke({"path": "admin/SKILL.md", "content": "共享技能"})
            ).startswith("✅")
            assert asyncio.run(
                skill_tools.delete_skill_file_tool.ainvoke({"path": "admin/SKILL.md"})
            ).startswith("✅")
        finally:
            reset_execution_context(token)

    @pytest.mark.parametrize(
        "url,payload",
        [
            ("/api/settings/skills", {"skills": []}),
            ("/api/settings/mcp", {"mcpServers": {}}),
        ],
    )
    def test_shared_settings_require_admin(self, tenancy, url, payload):
        assert tenancy.client.put(url, headers=_h(tenancy.token_b), json=payload).status_code == 403


@pytest.mark.parametrize("token_attr,allowed", [("token_a", False), ("token_admin", True)])
def test_orphan_canvas_websocket_access(tenancy, monkeypatch, token_attr, allowed):
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setenv("WS_AUTH_DISABLED", "false")
    url = f"/ws/tenant-canvas-orphan?token={getattr(tenancy, token_attr)}"
    if allowed:
        with tenancy.client.websocket_connect(url):
            pass
    else:
        with pytest.raises(WebSocketDisconnect) as exc:
            with tenancy.client.websocket_connect(url):
                pass
        assert exc.value.code == 4403


def test_chat_stream_preserves_memory_identity(tenancy, private_workspace, monkeypatch):
    from app.services import workspace_service
    from app.tools.workspace_tools import write_memory

    async def fake_stream(messages, session_id):
        result = await write_memory.ainvoke({"section": "角色资产", "content": "SSE 中 A 的私有角色"})
        assert result.startswith("✅")
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("app.routers.chat.process_chat_stream", fake_stream)
    response = tenancy.client.post("/api/chat", headers=_h(tenancy.token_a), json={"message": "记住角色"})
    assert response.status_code == 200
    assert "SSE 中 A 的私有角色" in workspace_service.get_workspace_context(tenancy.ids.a)
    assert "SSE 中 A 的私有角色" not in workspace_service.get_workspace_context(tenancy.ids.b)


def test_eval_results_and_stats_are_private(tenancy):
    headers_a = _h(tenancy.token_a)
    headers_b = _h(tenancy.token_b)
    dataset = tenancy.client.get("/api/eval/datasets", headers=headers_a).json()[0]
    created_ids = set()
    for project_id in (None, tenancy.ids.proj_a):
        response = tenancy.client.post(
            "/api/eval/runs",
            headers=headers_a,
            json={
                "dataset_id": dataset["id"],
                "project_id": project_id,
                "provider": "mock",
                "deployment": "A-private-eval",
                "model": "mock",
                "samples": [{} for _ in range(dataset["shot_count"])],
            },
        )
        assert response.status_code == 200, response.text
        created_ids.add(response.json()["id"])
    rows_a = tenancy.client.get("/api/eval/runs", headers=headers_a).json()
    rows_b = tenancy.client.get("/api/eval/runs", headers=headers_b).json()
    assert created_ids <= {row["id"] for row in rows_a}
    assert not created_ids & {row["id"] for row in rows_b}
    assert "A-private-eval" not in tenancy.client.get("/api/eval/routing-stats", headers=headers_b).text
    stats_a = tenancy.client.get("/api/eval/routing-stats", headers=headers_a).json()
    assert any(row["deployment"] == "A-private-eval" and row["sample_runs"] == 2 for row in stats_a)
    rows_admin = tenancy.client.get("/api/eval/runs", headers=_h(tenancy.token_admin)).json()
    assert created_ids <= {row["id"] for row in rows_admin}


def test_unsaved_chat_reserves_owner_before_save_and_websocket(tenancy, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    from app.db.database import SessionLocal
    from app.db.models import User
    from app.routers.chat import ChatRequest, chat

    monkeypatch.setenv("WS_AUTH_DISABLED", "false")

    async def fake_stream(messages, session_id):
        yield 'data: {"content":"A private memory"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("app.routers.chat.process_chat_stream", fake_stream)
    cid = "tenant-unsaved-chat-reservation"

    async def scenario():
        async with SessionLocal() as session:
            user = await session.get(User, tenancy.ids.a)
            response = await chat(ChatRequest(message="private", canvas_id=cid), session, user)
        # A 已开聊，生成器继续前 B 抢先保存并尝试订阅。
        stolen = await asyncio.to_thread(
            tenancy.client.post,
            "/api/canvases",
            headers=_h(tenancy.token_b),
            json={"id": cid, "name": "B stolen"},
        )

        def subscribe_b():
            with pytest.raises(WebSocketDisconnect) as exc:
                with tenancy.client.websocket_connect(f"/ws/{cid}?token={tenancy.token_b}"):
                    pass
            assert exc.value.code == 4403

        await asyncio.to_thread(subscribe_b)
        chunks = [chunk async for chunk in response.body_iterator]
        assert any("A private memory" in chunk for chunk in chunks)
        assert stolen.status_code == 403
        saved = await asyncio.to_thread(
            tenancy.client.post,
            "/api/canvases",
            headers=_h(tenancy.token_a),
            json={"id": cid, "name": "A saved"},
        )
        assert saved.status_code == 200
        assert saved.json()["name"] == "A saved"

    asyncio.run(scenario())


@pytest.mark.parametrize("rival", ["chat", "save"])
def test_concurrent_canvas_binding_has_one_owner(tenancy, rival):
    from app.db.database import SessionLocal
    from app.db.models import Canvas
    from app.services.history_service import history_service

    cid = f"tenant-concurrent-bind-{rival}"

    async def bind(user_id, save=False):
        async with SessionLocal() as session:
            if save:
                try:
                    await history_service.save_canvas(session, {"id": cid}, user_id)
                    return True
                except PermissionError:
                    return False
            return await history_service.can_access_canvas(session, cid, user_id)

    async def scenario():
        results = await asyncio.gather(bind(tenancy.ids.a), bind(tenancy.ids.b, rival == "save"))
        assert sorted(results) == [False, True]
        async with SessionLocal() as session:
            canvas = await session.get(Canvas, cid)
            assert canvas.owner_id == (tenancy.ids.a if results[0] else tenancy.ids.b)

    asyncio.run(scenario())


def test_stale_websocket_does_not_receive_new_owners_chat(tenancy, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    from app.db.database import SessionLocal
    from app.db.models import User
    from app.main import websocket_endpoint
    from app.routers.chat import ChatRequest, chat
    from app.services.history_service import history_service

    monkeypatch.setenv("WS_AUTH_DISABLED", "false")
    cid = "tenant-reused-canvas-channel"

    async def fake_stream(messages, session_id):
        yield 'data: {"content":"private A answer"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("app.routers.chat.process_chat_stream", fake_stream)

    class Socket:
        def __init__(self):
            from starlette.datastructures import State

            self.query_params = {"token": tenancy.token_b}
            self.state = State()
            self.messages = []

        async def accept(self):
            pass

        async def send_text(self, message):
            self.messages.append(message)

        async def receive_text(self):
            # B 订阅后删除画布，A 使用同一个 ID 开聊；旧 B 连接必须收不到任何事件。
            async with SessionLocal() as session:
                assert await history_service.delete_canvas(session, cid, tenancy.ids.b)
                user = await session.get(User, tenancy.ids.a)
                response = await chat(ChatRequest(message="private A question", canvas_id=cid), session, user)
                chunks = [chunk async for chunk in response.body_iterator]
                assert any("private A answer" in chunk for chunk in chunks)
            raise WebSocketDisconnect()

    async def scenario():
        async with SessionLocal() as session:
            await history_service.save_canvas(session, {"id": cid}, tenancy.ids.b)
        ws = Socket()
        await websocket_endpoint(ws, cid)
        assert ws.messages == []

    asyncio.run(scenario())
