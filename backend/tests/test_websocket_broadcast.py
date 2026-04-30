import json

from fastapi.testclient import TestClient

from app.main import app


def test_websocket_receives_external_chat_events(monkeypatch):
    async def fake_process_chat_stream(messages, session_id=None):
        yield 'data: {"type":"delta","content":"你好"}\n\n'
        yield 'data: [DONE]\n\n'

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
