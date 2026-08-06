"""Review 安全修复测试：P0-3 受保护下载、P0-4 审批跨项目、P0-5 资产绑 Shot 跨项目、
P0-6 Canvas owner 校验、P2-7 WS 4403/4404。

service 层测试用 stub session（模型是 postgresql.JSONB，不用真 DB）；
路由层测试沿用 test_series_domain 的 monkeypatch 风格验证状态码映射。
"""

from types import SimpleNamespace

import pytest

from app.db.models import Artifact, AssetCard, Canvas, Episode, Scene, SeriesProject, Shot, User
from app.services import approval_service, asset_service, series_service


class StubSession:
    """按类型分发 get() 的最小 AsyncSession stub。"""

    def __init__(self, get_map=None, execute_first=None):
        self._get_map = get_map or {}
        self._execute_first = execute_first
        self.added = []

    async def get(self, model, key):
        entry = self._get_map.get(model)
        if callable(entry):
            return entry(key)
        return entry

    async def execute(self, stmt):
        first = self._execute_first
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: first, all=lambda: []))

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def refresh(self, row):
        pass


# ─────────────────────────────────────────
# P0-4 审批跨项目写入
# ─────────────────────────────────────────


class TestApprovalCrossProject:
    async def test_storyboard_target_other_project_rejected(self):
        revision = SimpleNamespace(episode_id="ep-B", revision=1)
        session = StubSession(
            get_map={Episode: SimpleNamespace(project_id="project-B")},
            execute_first=revision,
        )
        with pytest.raises(PermissionError):
            await approval_service._validate_target(
                session, "storyboard", "ep-B", 1, "project-A"
            )

    async def test_asset_target_other_project_rejected(self):
        session = StubSession(
            get_map={AssetCard: SimpleNamespace(version=2, project_id="project-B")}
        )
        with pytest.raises(PermissionError):
            await approval_service._validate_target(
                session, "asset", "asset-1", 2, "project-A"
            )

    async def test_same_project_target_allowed(self):
        asset = SimpleNamespace(version=1, project_id="project-A")
        session = StubSession(get_map={AssetCard: asset})
        row = await approval_service._validate_target(session, "asset", "asset-1", 1, "project-A")
        assert row is asset

    def test_router_maps_permission_error_to_403(self, client, monkeypatch):
        """伪造 project_id（自己的）+ 他人 target_id：service 拒绝 → 403。"""

        async def fake_get_project(session, project_id, user_id=None, is_admin=False):
            return SeriesProject(id=project_id, user_id=1)

        async def fake_approve(session, **kw):
            raise PermissionError("target belongs to another project")

        monkeypatch.setattr("app.routers.series.series_service.get_project", fake_get_project)
        monkeypatch.setattr("app.routers.series.approval_service.approve", fake_approve)

        resp = client.post(
            "/api/series/approvals",
            json={
                "project_id": "my-project",
                "target_type": "asset",
                "target_id": "other-project-asset",
                "target_revision": 1,
                "action": "approve",
            },
        )
        assert resp.status_code == 403


# ─────────────────────────────────────────
# P0-5 资产绑 Shot 跨项目
# ─────────────────────────────────────────


def _bind_session(asset_project, shot_project):
    return StubSession(
        get_map={
            AssetCard: SimpleNamespace(id="asset-1", project_id=asset_project),
            Shot: SimpleNamespace(id="shot-1", scene_id="scene-1"),
            Scene: SimpleNamespace(id="scene-1", episode_id="ep-1"),
            Episode: SimpleNamespace(id="ep-1", project_id=shot_project),
        }
    )


class TestBindAssetToShot:
    async def test_cross_project_bind_rejected(self):
        session = _bind_session("project-A", "project-B")
        with pytest.raises(PermissionError):
            await asset_service.bind_asset_to_shot(session, "shot-1", "asset-1")

    async def test_same_project_bind_ok(self):
        session = _bind_session("project-A", "project-A")
        ref = await asset_service.bind_asset_to_shot(session, "shot-1", "asset-1")
        assert ref.shot_id == "shot-1" and ref.asset_id == "asset-1"

    async def test_missing_shot_rejected(self):
        session = _bind_session("project-A", "project-A")
        session._get_map[Shot] = None
        with pytest.raises(ValueError):
            await asset_service.bind_asset_to_shot(session, "shot-x", "asset-1")

    def test_router_maps_permission_error_to_403(self, client, monkeypatch):
        async def fake_get_asset(session, asset_id, user_id):
            return SimpleNamespace(id=asset_id, project_id="project-A")

        async def fake_bind(session, shot_id, asset_id, role="character"):
            raise PermissionError("shot and asset belong to different projects")

        monkeypatch.setattr("app.routers.series.asset_service.get_asset_for_user", fake_get_asset)
        monkeypatch.setattr("app.routers.series.asset_service.bind_asset_to_shot", fake_bind)

        resp = client.post(
            "/api/series/assets/asset-1/bind-shot",
            json={"shot_id": "other-project-shot", "role": "character"},
        )
        assert resp.status_code == 403


# ─────────────────────────────────────────
# P0-6 创建项目校验 Canvas owner
# ─────────────────────────────────────────


class TestCreateProjectCanvasOwner:
    async def test_canvas_not_found(self):
        session = StubSession(get_map={Canvas: None})
        with pytest.raises(ValueError, match="不存在"):
            await series_service.create_project(session, 1, canvas_id="no-such-canvas")

    async def test_canvas_owned_by_other_user(self):
        session = StubSession(get_map={Canvas: SimpleNamespace(id="c1", owner_id=999)})
        with pytest.raises(PermissionError):
            await series_service.create_project(session, 1, canvas_id="c1")

    async def test_orphan_canvas_rejected(self):
        session = StubSession(get_map={Canvas: SimpleNamespace(id="c1", owner_id=None)})
        with pytest.raises(PermissionError, match="认领"):
            await series_service.create_project(session, 1, canvas_id="c1")

    def test_router_canvas_not_found_is_404(self, client, monkeypatch):
        async def fake_create(session, user_id, **kw):
            raise ValueError("canvas c-x 不存在")

        monkeypatch.setattr("app.routers.series.series_service.create_project", fake_create)
        resp = client.post("/api/series/projects", json={"title": "t", "canvas_id": "c-x"})
        assert resp.status_code == 404

    def test_router_foreign_canvas_is_403(self, client, monkeypatch):
        async def fake_create(session, user_id, **kw):
            raise PermissionError("canvas belongs to another user")

        monkeypatch.setattr("app.routers.series.series_service.create_project", fake_create)
        resp = client.post("/api/series/projects", json={"title": "t", "canvas_id": "c-x"})
        assert resp.status_code == 403


# ─────────────────────────────────────────
# P0-3 Artifact 受保护下载
# ─────────────────────────────────────────


class TestArtifactDownload:
    def _override(self, monkeypatch, tmp_path, artifact, project, user):
        from app.auth.dependencies import get_current_user
        from app.db.database import get_session
        from app.main import app
        from app.routers import artifacts as artifacts_router

        monkeypatch.setattr(artifacts_router, "_STORAGE_ROOT", tmp_path)

        async def fake_session():
            yield StubSession(get_map={Artifact: artifact, SeriesProject: project})

        async def fake_user():
            return user

        app.dependency_overrides[get_session] = fake_session
        app.dependency_overrides[get_current_user] = fake_user
        return app

    def _client(self, app):
        from fastapi.testclient import TestClient

        return TestClient(app)

    def test_owner_can_download(self, monkeypatch, tmp_path):
        (tmp_path / "videos").mkdir()
        (tmp_path / "videos" / "a.mp4").write_bytes(b"fake-video")
        artifact = SimpleNamespace(
            id="art-1", project_id="p1", storage_key="videos/a.mp4", mime_type="video/mp4"
        )
        project = SimpleNamespace(id="p1", user_id=1)
        user = SimpleNamespace(id=1, is_admin=False, username="u1")
        app = self._override(monkeypatch, tmp_path, artifact, project, user)

        resp = self._client(app).get("/api/artifacts/art-1/download")
        assert resp.status_code == 200
        assert resp.content == b"fake-video"

    def test_other_user_gets_404(self, monkeypatch, tmp_path):
        artifact = SimpleNamespace(
            id="art-1", project_id="p1", storage_key="videos/a.mp4", mime_type="video/mp4"
        )
        project = SimpleNamespace(id="p1", user_id=1)
        user = SimpleNamespace(id=2, is_admin=False, username="u2")
        app = self._override(monkeypatch, tmp_path, artifact, project, user)

        resp = self._client(app).get("/api/artifacts/art-1/download")
        assert resp.status_code == 404

    def test_admin_can_download_others(self, monkeypatch, tmp_path):
        (tmp_path / "videos").mkdir()
        (tmp_path / "videos" / "a.mp4").write_bytes(b"x")
        artifact = SimpleNamespace(
            id="art-1", project_id="p1", storage_key="videos/a.mp4", mime_type="video/mp4"
        )
        project = SimpleNamespace(id="p1", user_id=1)
        admin = SimpleNamespace(id=99, is_admin=True, username="admin")
        app = self._override(monkeypatch, tmp_path, artifact, project, admin)

        assert self._client(app).get("/api/artifacts/art-1/download").status_code == 200

    def test_null_project_artifact_admin_only(self, monkeypatch, tmp_path):
        artifact = SimpleNamespace(
            id="art-1", project_id=None, storage_key="videos/a.mp4", mime_type="video/mp4"
        )
        user = SimpleNamespace(id=2, is_admin=False, username="u2")
        app = self._override(monkeypatch, tmp_path, artifact, None, user)

        assert self._client(app).get("/api/artifacts/art-1/download").status_code == 404

    def test_missing_artifact_is_404(self, monkeypatch, tmp_path):
        user = SimpleNamespace(id=1, is_admin=True, username="admin")
        app = self._override(monkeypatch, tmp_path, None, None, user)

        assert self._client(app).get("/api/artifacts/nope/download").status_code == 404

    def test_path_traversal_rejected(self, monkeypatch, tmp_path):
        artifact = SimpleNamespace(
            id="art-1", project_id="p1", storage_key="../../etc/passwd", mime_type=""
        )
        project = SimpleNamespace(id="p1", user_id=1)
        user = SimpleNamespace(id=1, is_admin=False, username="u1")
        app = self._override(monkeypatch, tmp_path, artifact, project, user)

        assert self._client(app).get("/api/artifacts/art-1/download").status_code == 404


class _StorageSession:
    """/storage 路由 stub session：execute 按序返回结果（user, artifact），get 返回 project。"""

    def __init__(self, execute_results=(), get_result=None):
        self._exec = list(execute_results)
        self._get_result = get_result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        value = self._exec.pop(0) if self._exec else None
        return SimpleNamespace(scalar_one_or_none=lambda: value)

    async def get(self, model, key):
        return self._get_result


# ─────────────────────────────────────────
# P0-3 /storage 手工鉴权路由
# ─────────────────────────────────────────


class TestStorageRoute:
    """main.py 在导入时按 STORAGE_PUBLIC_LEGACY 默认值（false）注册了自定义 /storage 路由。"""
    def test_storage_requires_auth(self, client):
        """非 public 路径无 token → 401。"""
        resp = client.get("/storage/images/whatever.png")
        assert resp.status_code == 401

    def test_storage_public_dir_open(self, client, tmp_path, monkeypatch):
        """public/ 前缀无需 token。"""
        import app.main as main_mod

        public_dir = tmp_path / "public"
        public_dir.mkdir()
        (public_dir / "hello.txt").write_text("hi")
        monkeypatch.setattr(main_mod, "_STORAGE_ROOT", tmp_path)

        resp = client.get("/storage/public/hello.txt")
        assert resp.status_code == 200
        assert resp.text == "hi"

    def test_storage_valid_token_serves_file(self, client, tmp_path, monkeypatch):
        """owner 访问自己项目 artifact 对应的 storage 路径 → 200。"""
        import app.main as main_mod
        from app.auth.security import create_access_token

        images = tmp_path / "images"
        images.mkdir()
        (images / "ok.png").write_bytes(b"png")

        monkeypatch.setattr(main_mod, "_STORAGE_ROOT", tmp_path)
        monkeypatch.setattr(
            "app.db.database.SessionLocal",
            lambda: _StorageSession(
                execute_results=[
                    SimpleNamespace(id=1, is_admin=False, username="u1"),  # user
                    SimpleNamespace(id="a1", project_id="p1", storage_key="images/ok.png"),
                ],
                get_result=SimpleNamespace(id="p1", user_id=1),
            ),
        )

        token = create_access_token("u1")
        resp = client.get(f"/storage/images/ok.png?token={token}")
        assert resp.status_code == 200

    def test_storage_other_users_artifact_404(self, client, tmp_path, monkeypatch):
        """他人项目 artifact 的 storage 路径 → 404（不泄露存在性）。"""
        import app.main as main_mod
        from app.auth.security import create_access_token

        images = tmp_path / "images"
        images.mkdir()
        (images / "ok.png").write_bytes(b"png")

        monkeypatch.setattr(main_mod, "_STORAGE_ROOT", tmp_path)
        monkeypatch.setattr(
            "app.db.database.SessionLocal",
            lambda: _StorageSession(
                execute_results=[
                    SimpleNamespace(id=2, is_admin=False, username="u2"),
                    SimpleNamespace(id="a1", project_id="p1", storage_key="images/ok.png"),
                ],
                get_result=SimpleNamespace(id="p1", user_id=1),
            ),
        )

        token = create_access_token("u2")
        assert client.get(f"/storage/images/ok.png?token={token}").status_code == 404

    def test_storage_orphan_project_admin_only(self, client, tmp_path, monkeypatch):
        """无主项目 artifact：普通用户 404，admin 200。"""
        import app.main as main_mod
        from app.auth.security import create_access_token

        images = tmp_path / "images"
        images.mkdir()
        (images / "ok.png").write_bytes(b"png")
        monkeypatch.setattr(main_mod, "_STORAGE_ROOT", tmp_path)

        artifact = SimpleNamespace(id="a1", project_id="p1", storage_key="images/ok.png")
        project = SimpleNamespace(id="p1", user_id=None)

        monkeypatch.setattr(
            "app.db.database.SessionLocal",
            lambda: _StorageSession(
                execute_results=[SimpleNamespace(id=2, is_admin=False, username="u2"), artifact],
                get_result=project,
            ),
        )
        token = create_access_token("u2")
        assert client.get(f"/storage/images/ok.png?token={token}").status_code == 404

        monkeypatch.setattr(
            "app.db.database.SessionLocal",
            lambda: _StorageSession(
                execute_results=[SimpleNamespace(id=1, is_admin=True, username="admin"), artifact],
                get_result=project,
            ),
        )
        token = create_access_token("admin")
        assert client.get(f"/storage/images/ok.png?token={token}").status_code == 200

    def test_storage_no_artifact_record_default_404(self, client, tmp_path, monkeypatch):
        """无 artifact 记录的老文件：默认 404；legacy 开关打开时登录即可读。"""
        import app.main as main_mod
        from app.auth.security import create_access_token

        images = tmp_path / "images"
        images.mkdir()
        (images / "old.png").write_bytes(b"png")
        monkeypatch.setattr(main_mod, "_STORAGE_ROOT", tmp_path)

        user = SimpleNamespace(id=1, is_admin=False, username="u1")
        monkeypatch.setattr(
            "app.db.database.SessionLocal",
            lambda: _StorageSession(execute_results=[user, None]),  # 无 artifact
        )
        token = create_access_token("u1")

        monkeypatch.delenv("STORAGE_LEGACY_AUTHENTICATED_READ", raising=False)
        assert client.get(f"/storage/images/old.png?token={token}").status_code == 404

        monkeypatch.setenv("STORAGE_LEGACY_AUTHENTICATED_READ", "true")
        assert client.get(f"/storage/images/old.png?token={token}").status_code == 200


# ─────────────────────────────────────────
# review 0.4：无主项目归属（ownership.check_project_access 收拢）
# ─────────────────────────────────────────


class TestOrphanProjectOwnership:
    async def test_orphan_project_normal_user_rejected(self):
        session = StubSession(
            get_map={SeriesProject: SimpleNamespace(id="p1", user_id=None)}
        )
        with pytest.raises(PermissionError):
            await series_service.get_project(session, "p1", 2)

    async def test_orphan_project_admin_allowed(self):
        project = SimpleNamespace(id="p1", user_id=None)
        session = StubSession(get_map={SeriesProject: project})
        assert await series_service.get_project(session, "p1", 1, is_admin=True) is project

    async def test_other_users_project_rejected(self):
        session = StubSession(
            get_map={SeriesProject: SimpleNamespace(id="p1", user_id=1)}
        )
        with pytest.raises(PermissionError):
            await series_service.get_project(session, "p1", 2)

    async def test_claim_endpoint_assigns_user(self):
        from app.routers import series as series_router

        project = SeriesProject(id="p1", user_id=None, title="t")
        target = User(id=7, username="u7", password_hash="x")
        session = StubSession(get_map={SeriesProject: project, User: target})
        resp = await series_router.claim_project(
            "p1",
            series_router.ClaimRequest(user_id=7),
            session=session,
            admin=SimpleNamespace(id=1, is_admin=True),
        )
        assert project.user_id == 7
        assert resp["user_id"] == 7

    async def test_claim_endpoint_rejects_owned_project(self):
        from fastapi import HTTPException

        from app.routers import series as series_router

        project = SeriesProject(id="p1", user_id=3, title="t")
        session = StubSession(get_map={SeriesProject: project})
        with pytest.raises(HTTPException) as exc:
            await series_router.claim_project(
                "p1",
                series_router.ClaimRequest(user_id=7),
                session=session,
                admin=SimpleNamespace(id=1, is_admin=True),
            )
        assert exc.value.status_code == 409


# ─────────────────────────────────────────
# P2-7 WebSocket 4403/4404
# ─────────────────────────────────────────


class TestWsCanvasAuth:
    def _patch_session(self, monkeypatch, user, canvas):
        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: user)

            async def get(self, model, key):
                return canvas

        monkeypatch.setattr("app.db.database.SessionLocal", lambda: _FakeSession())

    def _connect_code(self, client, canvas_id, token):
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/{canvas_id}?token={token}"):
                pass
        return exc_info.value.code

    def test_nonexistent_canvas_4404(self, client, monkeypatch):
        from app.auth.security import create_access_token

        monkeypatch.setenv("WS_AUTH_DISABLED", "false")
        self._patch_session(monkeypatch, SimpleNamespace(id=1, username="admin"), None)

        code = self._connect_code(client, "ghost-canvas", create_access_token("admin"))
        assert code == 4404

    def test_orphan_canvas_4403(self, client, monkeypatch):
        from app.auth.security import create_access_token

        monkeypatch.setenv("WS_AUTH_DISABLED", "false")
        canvas = SimpleNamespace(id="c1", owner_id=None)
        self._patch_session(monkeypatch, SimpleNamespace(id=1, username="admin"), canvas)

        code = self._connect_code(client, "c1", create_access_token("admin"))
        assert code == 4403

    def test_other_users_canvas_4403(self, client, monkeypatch):
        from app.auth.security import create_access_token

        monkeypatch.setenv("WS_AUTH_DISABLED", "false")
        canvas = SimpleNamespace(id="c1", owner_id=999)
        self._patch_session(monkeypatch, SimpleNamespace(id=1, username="admin"), canvas)

        code = self._connect_code(client, "c1", create_access_token("admin"))
        assert code == 4403

    def test_owner_connects(self, client, monkeypatch):
        from app.auth.security import create_access_token

        monkeypatch.setenv("WS_AUTH_DISABLED", "false")
        canvas = SimpleNamespace(id="c1", owner_id=1)
        self._patch_session(monkeypatch, SimpleNamespace(id=1, username="admin"), canvas)

        # 连接成功（不断言后续消息，立即关闭）
        with client.websocket_connect(f"/ws/c1?token={create_access_token('admin')}"):
            pass
