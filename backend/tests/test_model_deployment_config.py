from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.model_runtime.deployment_config import DeploymentInput, DeploymentPatch, config_hash
from app.services import model_deployment_service as service


def _local_h3(**updates):
    payload = {
        "name": "Local H3",
        "slug": "h3-fl2va-int8-convrot",
        "driver": "local_h3_gateway",
        "modality": "video",
        "model": "MiniMax-H3",
        "base_url": "http://192.168.10.60:8088/",
        "credential_ref": "env:LOCAL_H3_API_KEY",
        "config": {"max_concurrency": 1, "min_duration": 4, "max_duration": 6},
        "capabilities": {"modes": ["text"], "aspect_ratios": ["16:9"]},
        "pricing": {},
    }
    payload.update(updates)
    return DeploymentInput.model_validate(payload)


def test_deployment_normalizes_and_hashes_local_h3():
    payload = _local_h3()
    assert payload.base_url == "http://192.168.10.60:8088"
    assert payload.config["artifact_timeout_seconds"] == 900
    assert payload.capabilities["modes"] == ["text"]
    assert len(config_hash(payload)) == 64
    assert config_hash(payload) == config_hash(_local_h3())


@pytest.mark.parametrize(
    "updates,match",
    [
        ({"driver": "unknown"}, "未知 Driver"),
        ({"modality": "chat"}, "不支持模态"),
        ({"base_url": "file:///etc/passwd"}, "http/https"),
        ({"credential_ref": "plain-secret"}, "env:NAME"),
        ({"config": {"api_key": "leak"}}, "禁止包含密钥"),
        ({"config": {"max_concurrency": 0}}, "greater than or equal"),
        ({"config": {"min_duration": 7, "max_duration": 6}}, "min_duration"),
    ],
)
def test_deployment_rejects_invalid_config(updates, match):
    with pytest.raises(ValidationError, match=match):
        _local_h3(**updates)


def test_other_builtin_driver_shapes():
    openai = DeploymentInput(
        name="Chat",
        slug="chat-primary",
        driver="openai_compatible",
        modality="chat",
        model="gpt-compatible",
        base_url="https://llm.example/v1",
        credential_ref="env:OPENAI_API_KEY",
    )
    image = DeploymentInput(
        name="Volcano Image",
        slug="volcano-image",
        driver="volcano_image",
        modality="image",
        model="seedream",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        credential_ref="env:ARK_API_KEY",
    )
    assert openai.capabilities["tools"] is True
    assert image.capabilities["operations"] == ["generate"]


def test_dto_never_contains_credential_value(monkeypatch):
    monkeypatch.setenv("LOCAL_H3_API_KEY", "secret-value")
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(
        id="dep-1",
        name="Local H3",
        slug="local-h3",
        driver="local_h3_gateway",
        modality="video",
        model="MiniMax-H3",
        base_url="http://h3.test",
        credential_ref="env:LOCAL_H3_API_KEY",
        enabled=True,
        priority=100,
        config={},
        capabilities={},
        pricing={},
        revision=1,
        config_hash="a" * 64,
        created_at=now,
        updated_at=now,
    )
    data = service.to_dto(row).model_dump()
    assert data["credential_configured"] is True
    assert "credential_ref" not in data
    assert "api_key" not in data["config"]
    assert "api_key" not in data["capabilities"]
    assert "secret-value" not in str(data)


def test_patch_cannot_bypass_full_driver_validation():
    patch = DeploymentPatch(config={"max_concurrency": 0})
    assert patch.config == {"max_concurrency": 0}
    with pytest.raises(ValidationError):
        _local_h3(config=patch.config)
