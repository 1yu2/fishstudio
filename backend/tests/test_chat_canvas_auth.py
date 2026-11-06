"""POST /api/chat 的 canvas 归属校验：他人画布必须返回 403，而不是被泛型 except 吞成 500。"""

from fastapi.testclient import TestClient

from app.db.database import get_session
from app.main import app


def test_chat_returns_403_when_canvas_belongs_to_other_user(monkeypatch):
    # conftest 默认把 get_session override 成 None（跳过归属校验）；
    # 这里给一个非 None 的 dummy session，让校验路径真实执行。
    async def fake_get_session():
        yield object()

    app.dependency_overrides[get_session] = fake_get_session

    async def fake_can_access_canvas(session, canvas_id, owner_id, *, is_admin=False):
        return False  # 即使管理员也不能访问他人的有主画布

    monkeypatch.setattr("app.routers.chat.history_service.can_access_canvas", fake_can_access_canvas)

    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "hi", "canvas_id": "someone-elses-canvas"})

    assert response.status_code == 403
