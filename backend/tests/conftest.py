"""Pytest fixtures：在导入 app 之前注入测试用 env，并 override 认证依赖。"""
import os
import sys
from pathlib import Path

# 1) 在任何 app.* 导入之前设好必需的 env
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/fishstudio_test")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")

# 2) 把 backend/ 加到 sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest


def _install_app_overrides(monkeypatch):
    """加载 app.main 并 override 认证 + DB 依赖。返回 app（或 None 表示加载失败）。"""
    async def _noop_init_db():
        return None

    monkeypatch.setattr("app.db.init_db.init_db", _noop_init_db, raising=False)
    monkeypatch.setattr("app.main.init_db", _noop_init_db, raising=False)

    from app.auth.dependencies import get_current_user, require_admin
    from app.db.database import get_session
    from app.db.models import User
    from app.main import app

    fake_user = User(id=1, username="admin", password_hash="x", is_admin=True)

    async def _fake_get_user():
        return fake_user

    async def _fake_get_session():
        yield None

    app.dependency_overrides[get_current_user] = _fake_get_user
    app.dependency_overrides[require_admin] = _fake_get_user
    app.dependency_overrides[get_session] = _fake_get_session
    return app


@pytest.fixture
def client(monkeypatch):
    """显式 fixture：旧测试若想用 TestClient(app) + 自动认证绕过，请改成接收 client。"""
    from fastapi.testclient import TestClient

    app = _install_app_overrides(monkeypatch)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _auto_app_overrides_for_existing_tests(monkeypatch, request):
    """
    对那些直接 `client = TestClient(app)` 的旧测试，仍然在 import app 后注入依赖
    override，使它们继续通过。失败时静默跳过 — 不影响纯函数单元测试。
    """
    if "no_bypass_auth" in request.keywords:
        yield
        return
    try:
        app = _install_app_overrides(monkeypatch)
    except Exception:
        # 旧测试可能依赖未装的可选库（如 pydub），单元测试不需要 app
        yield
        return
    yield
    try:
        app.dependency_overrides.clear()
    except Exception:
        pass


def pytest_configure(config):
    config.addinivalue_line("markers", "no_bypass_auth: skip the autouse auth bypass for this test")
