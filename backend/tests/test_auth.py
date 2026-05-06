"""认证基础测试：未登录 401、密码哈希正确性、token 编解码。"""
import os
import sys
from pathlib import Path

# 与 conftest.py 一致的 env 注入 + sys.path（独立运行时也安全）
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/fishstudio_test")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest
from fastapi.testclient import TestClient


def test_password_hash_roundtrip():
    from app.auth.security import hash_password, verify_password

    h = hash_password("hello-world")
    assert verify_password("hello-world", h) is True
    assert verify_password("wrong", h) is False


def test_token_roundtrip():
    from app.auth.security import create_access_token, decode_token

    tok = create_access_token("alice", extra={"is_admin": True})
    payload = decode_token(tok)
    assert payload is not None
    assert payload["sub"] == "alice"
    assert payload["is_admin"] is True


@pytest.mark.no_bypass_auth
def test_protected_endpoints_require_auth(monkeypatch):
    """不 override 依赖时，受保护接口应返回 401。"""
    async def _noop_init_db():
        return None

    monkeypatch.setattr("app.db.init_db.init_db", _noop_init_db, raising=False)
    monkeypatch.setattr("app.main.init_db", _noop_init_db, raising=False)

    from app.main import app

    with TestClient(app) as c:
        # /api/canvases 受保护
        resp = c.get("/api/canvases")
        assert resp.status_code == 401

        # /health 不受保护
        resp = c.get("/health")
        assert resp.status_code == 200
