from datetime import datetime, timezone
from types import SimpleNamespace


def _row(**updates):
    now = datetime.now(timezone.utc)
    values = {
        "id": "deployment-1",
        "name": "Local H3",
        "slug": "local-h3",
        "driver": "local_h3_gateway",
        "modality": "video",
        "model": "MiniMax-H3",
        "base_url": "http://192.168.10.60:8088",
        "credential_ref": "env:LOCAL_H3_API_KEY",
        "enabled": True,
        "priority": 100,
        "config": {},
        "capabilities": {},
        "pricing": {},
        "revision": 1,
        "config_hash": "a" * 64,
        "created_at": now,
        "updated_at": now,
    }
    values.update(updates)
    return SimpleNamespace(**values)


def test_deployment_list_uses_redacted_dto(client, monkeypatch):
    from app.services import model_deployment_service as service

    monkeypatch.setenv("LOCAL_H3_API_KEY", "secret-value")

    async def fake_list(_session):
        return [_row()]

    monkeypatch.setattr(service, "list_deployments", fake_list)
    response = client.get("/api/model-deployments")

    assert response.status_code == 200
    body = response.json()[0]
    assert "credential_ref" not in body
    assert body["credential_configured"] is True
    assert "secret-value" not in response.text


def test_deployment_validation_rejects_plaintext_secret(client):
    response = client.post(
        "/api/model-deployments/validate",
        json={
            "name": "Unsafe",
            "slug": "unsafe-model",
            "driver": "openai_compatible",
            "modality": "chat",
            "model": "chat-model",
            "base_url": "https://example.test/v1",
            "credential_ref": "env:CHAT_API_KEY",
            "config": {"api_key": "secret-value"},
        },
    )

    assert response.status_code == 422
    assert "secret-value" not in response.text
    assert "禁止包含密钥" in response.text


def test_deployment_detail_not_found(client, monkeypatch):
    from app.services import model_deployment_service as service

    async def fake_get(_session, _slug):
        return None

    monkeypatch.setattr(service, "get_deployment", fake_get)
    response = client.get("/api/model-deployments/missing")
    assert response.status_code == 404


def test_driver_catalog_exposes_typed_schemas(client):
    response = client.get("/api/model-deployments/drivers")
    assert response.status_code == 200
    drivers = {item["driver"]: item for item in response.json()}
    assert {"local_h3_gateway", "openai_compatible", "volcano_image"} <= drivers.keys()
    assert "max_concurrency" in drivers["local_h3_gateway"]["config_schema"]["properties"]
