"""/storage 路径规范化与按用户授权；上传体积的 ASGI 级限制。"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.main import STORAGE_DIR, app

pytestmark = pytest.mark.no_bypass_auth


@pytest.fixture
def client():
    app.dependency_overrides.clear()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def private_file():
    folder = STORAGE_DIR / "images"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "u9-private-secret.png"
    path.write_bytes(b"PRIVATE")
    (STORAGE_DIR / "public").mkdir(parents=True, exist_ok=True)
    public = STORAGE_DIR / "public" / "u9-ok.txt"
    public.write_bytes(b"PUBLIC")
    yield path
    path.unlink(missing_ok=True)
    public.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "url",
    [
        "/storage/public/%2e%2e/images/u9-private-secret.png",
        "/storage/public/..%2fimages/u9-private-secret.png",
        "/storage/public/%252e%252e/images/u9-private-secret.png",
        "/storage/public/%252e%252e%252fimages/u9-private-secret.png",
        "/storage/public/..%5cimages/u9-private-secret.png",
        "/storage/public/%2e%2e%5cimages%5cu9-private-secret.png",
        "/storage/public/./../images/u9-private-secret.png",
        "/storage/public//../images/u9-private-secret.png",
        "/storage/public/%00/../images/u9-private-secret.png",
    ],
)
def test_public_prefix_traversal_never_returns_private_file(client, private_file, url):
    response = client.get(url)
    assert response.status_code in (400, 401, 404)  # 401：客户端已把 ./.. 折叠成私有路径，仍需 token
    assert b"PRIVATE" not in response.content


def test_public_still_served_and_private_needs_token(client, private_file):
    assert client.get("/storage/public/u9-ok.txt").content == b"PUBLIC"
    assert client.get("/storage/images/u9-private-secret.png").status_code == 401


def test_normalize_helper_rejects_dangerous_forms():
    import app.main as main

    norm = main._normalize_storage_path
    assert norm("a/b/c.png") == ["a", "b", "c.png"]
    for bad in ("a/../b", "a/./b", "a//b", "a/", "a\\b", "a%00b", "a%2f..%2fb", "%252e%252e/x"):
        assert norm(bad) is None, bad


def _token(username):
    return create_access_token(username)


def test_uploaded_file_readable_by_owner_only(client, monkeypatch, tmp_path, real_media):
    """上传 → 本人可读；别的用户 404；匿名 401。"""
    users = {
        "alice": SimpleNamespace(id=7, username="alice", is_admin=False),
        "bob": SimpleNamespace(id=8, username="bob", is_admin=False),
        "root": SimpleNamespace(id=1, username="root", is_admin=True),
    }
    from app.routers import chat

    monkeypatch.setattr(chat, "_STORAGE_DIR", STORAGE_DIR)

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt):
            name = stmt.compile().params.get("username_1")

            class R:
                def scalar_one_or_none(self_inner):
                    return users.get(name)

            return R()

    monkeypatch.setattr("app.db.database.SessionLocal", lambda: FakeSession())

    from app.auth.dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: users["alice"]
    up = client.post(
        "/api/upload-image",
        files={"file": ("a.png", real_media["png"], "image/png")},
        headers={"Authorization": "Bearer x"},
    )
    app.dependency_overrides.clear()
    assert up.status_code == 200
    url = up.json()["url"]
    assert url.startswith("/storage/uploads/7/images/")
    try:
        assert client.get(url).status_code == 401
        assert client.get(url, headers={"Authorization": f"Bearer {_token('alice')}"}).status_code == 200
        assert client.get(url, headers={"Authorization": f"Bearer {_token('bob')}"}).status_code == 404
        assert client.get(url, headers={"Authorization": f"Bearer {_token('root')}"}).status_code == 200
    finally:
        (STORAGE_DIR / url.removeprefix("/storage/")).unlink(missing_ok=True)


# ── 上传体积限制（ASGI 中间件） ──


@pytest.fixture
def parser_counter(monkeypatch):
    from starlette.formparsers import MultiPartParser

    calls = {"n": 0}
    original = MultiPartParser.parse

    async def counting(self):
        calls["n"] += 1
        return await original(self)

    monkeypatch.setattr(MultiPartParser, "parse", counting)
    return calls


AUTH = {"Authorization": "Bearer whatever"}


def test_oversize_content_length_rejected_before_parsing(client, parser_counter):
    body = b"\x00" * (17 * 1024 * 1024)
    response = client.post("/api/upload-image", files={"file": ("a.png", body, "image/png")}, headers=AUTH)
    assert response.status_code == 413
    assert parser_counter["n"] == 0


def test_oversize_chunked_body_aborted_while_receiving(client, parser_counter):
    sent = {"bytes": 0}

    def gen():
        chunk = b"x" * (1024 * 1024)
        for _ in range(40):  # 40MB，远超 16MB 上限
            sent["bytes"] += len(chunk)
            yield chunk

    response = client.post(
        "/api/upload-image",
        content=gen(),
        headers={**AUTH, "Content-Type": "multipart/form-data; boundary=xx"},
    )
    assert response.status_code == 413


def test_anonymous_upload_gets_401_without_reading_body(client, parser_counter):
    consumed = {"n": 0}

    def gen():
        for _ in range(5):
            consumed["n"] += 1
            yield b"x" * 1024

    response = client.post(
        "/api/upload-audio", content=gen(), headers={"Content-Type": "multipart/form-data; boundary=xx"}
    )
    assert response.status_code == 401
    assert parser_counter["n"] == 0


def test_limits_cover_all_upload_routes():
    from app.upload_limit import limit_for

    for path in (
        "/api/upload-image",
        "/api/upload-audio",
        "/api/upload-video",
        "/api/series/projects/p1/artifacts/upload-audio",
        "/api/series/projects/p1/artifacts/upload-reference-video",
        "/api/node-canvases/c1/uploads",
        "/api/node-canvases/c1/bgm",
        "/api/library/characters",
    ):
        assert limit_for(path) is not None, path
    assert limit_for("/api/chat") is None


@pytest.mark.asyncio
async def test_middleware_stops_pulling_body_once_limit_exceeded():
    """直接在 ASGI 层验证：没有 Content-Length 时，超限后不再继续消费请求体，也不调用应用写盘。"""
    from app.upload_limit import UploadLimitMiddleware

    pulled = {"n": 0}
    app_saw = {"bytes": 0}

    async def inner(scope, receive, send):
        # 模拟 multipart 解析：循环读 body，直到断开
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            app_saw["bytes"] += len(message["body"])
        await send({"type": "http.response.start", "status": 400, "headers": []})
        await send({"type": "http.response.body", "body": b"bad"})

    async def receive():
        pulled["n"] += 1
        return {"type": "http.request", "body": b"x" * (1024 * 1024), "more_body": True}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/upload-image",
        "headers": [(b"authorization", b"Bearer t")],
    }
    await UploadLimitMiddleware(inner)(scope, receive, send)
    assert sent[0]["status"] == 413
    assert pulled["n"] <= 17  # 15MB + 1MB 余量，超过即停
    assert app_saw["bytes"] <= 16 * 1024 * 1024


def test_legacy_read_flag_does_not_expose_other_users_uploads(client, monkeypatch):
    """STORAGE_LEGACY_AUTHENTICATED_READ=true 时，uploads/<uid>/ 仍只有本人与 admin 能读。"""
    monkeypatch.setenv("STORAGE_LEGACY_AUTHENTICATED_READ", "true")
    users = {
        "alice": SimpleNamespace(id=7, username="alice", is_admin=False),
        "bob": SimpleNamespace(id=8, username="bob", is_admin=False),
    }

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt):
            name = stmt.compile().params.get("username_1")

            class R:
                def scalar_one_or_none(self_inner):
                    return users.get(name)

            return R()

    monkeypatch.setattr("app.db.database.SessionLocal", lambda: FakeSession())
    folder = STORAGE_DIR / "uploads" / "7" / "images"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "u9-legacy.png"
    target.write_bytes(b"ALICE")
    try:
        url = "/storage/uploads/7/images/u9-legacy.png"
        assert client.get(url, headers={"Authorization": f"Bearer {_token('bob')}"}).status_code == 404
        assert client.get(url, headers={"Authorization": f"Bearer {_token('alice')}"}).content == b"ALICE"
    finally:
        target.unlink(missing_ok=True)
