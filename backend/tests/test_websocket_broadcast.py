import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app


def test_websocket_receives_external_chat_events(monkeypatch):
    async def fake_process_chat_stream(messages, session_id=None):
        yield 'data: {"type":"delta","content":"你好"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr("app.routers.chat.process_chat_stream", fake_process_chat_stream)

    client = TestClient(app)
    with client.websocket_connect("/ws/canvas-test") as websocket:
        with client.stream(
            "POST",
            "/api/chat",
            json={"message": "外部请求", "canvas_id": "canvas-test"},
        ) as response:
            assert response.status_code == 200
            assert any("你好" in line for line in response.iter_text())

        first = json.loads(websocket.receive_text())
        second = json.loads(websocket.receive_text())

        assert first["type"] == "user_message"
        assert first["content"] == "外部请求"
        assert second["type"] == "delta"
        assert second["content"] == "你好"


def test_chat_request_accepts_canvas_id():
    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "ping", "canvas_id": "canvas-test"})

    assert response.status_code == 200


def test_ws_rejects_missing_or_invalid_token(monkeypatch):
    """开启鉴权（WS_AUTH_DISABLED=false，生产默认值）时，无 token / 无效 token 必须 4401。"""
    monkeypatch.setenv("WS_AUTH_DISABLED", "false")

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/canvas-test"):
            pass
    assert exc_info.value.code == 4401

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/canvas-test?token=not-a-jwt"):
            pass
    assert exc_info.value.code == 4401
