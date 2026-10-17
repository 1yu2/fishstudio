from types import SimpleNamespace

import pytest

from app.services.model_deployment_bootstrap import (
    BOOTSTRAP_SETTING_KEY,
    bootstrap_legacy_deployments,
    legacy_deployment_specs,
)


def _clear_model_env(monkeypatch):
    for name in (
        "OPENAI_API_KEY", "VOLCANO_API_KEY", "MINIMAX_API_KEY",
        "LOCAL_H3_ENABLED", "LOCAL_H3_BASE_URL", "LOCAL_H3_API_KEY",
        "DASHSCOPE_API_KEY", "TENCENT_AI3D_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_legacy_local_h3_mapping_is_complete(monkeypatch):
    _clear_model_env(monkeypatch)
    monkeypatch.setenv("LOCAL_H3_ENABLED", "true")
    monkeypatch.setenv("LOCAL_H3_BASE_URL", "http://192.168.10.60:8088")
    monkeypatch.setenv("LOCAL_H3_API_KEY", "must-not-appear")
    monkeypatch.setenv("LOCAL_H3_DEPLOYMENT", "h3-fl2va-int8-convrot")
    monkeypatch.setenv("LOCAL_H3_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("LOCAL_H3_MIN_DURATION", "4")
    monkeypatch.setenv("LOCAL_H3_MAX_DURATION", "6")
    monkeypatch.setenv("LOCAL_H3_COST_CNY_PER_SECOND", "0.12")

    spec = next(
        item for item in legacy_deployment_specs()
        if item.payload.driver == "local_h3_gateway"
    )
    payload = spec.payload
    assert payload.slug == "h3-fl2va-int8-convrot"
    assert payload.driver == "local_h3_gateway"
    assert payload.credential_ref == "env:LOCAL_H3_API_KEY"
    assert payload.config["max_concurrency"] == 1
    assert payload.config["min_duration"] == 4
    assert payload.config["max_duration"] == 6
    assert payload.pricing["output_per_second"] == "0.12"
    assert "must-not-appear" not in repr(payload.model_dump())


class _ScalarResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


class FakeSession:
    def __init__(self, slugs=()):
        self.slugs = list(slugs)
        self.added = []
        self.marker = None
        self.commits = 0

    async def execute(self, _statement):
        return _ScalarResult(self.slugs)

    async def get(self, _model, key):
        assert key == BOOTSTRAP_SETTING_KEY
        return self.marker

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent_and_does_not_overwrite(monkeypatch):
    _clear_model_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-stored")
    session = FakeSession(slugs=["siliconflow-default", "volcano-video"])
    session.marker = SimpleNamespace(value=1)

    inserted = await bootstrap_legacy_deployments(session)

    assert inserted == 0
    assert session.added == []
    assert session.commits == 1


@pytest.mark.asyncio
async def test_bootstrap_inserts_only_missing_slugs(monkeypatch):
    _clear_model_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-stored")
    session = FakeSession()

    inserted = await bootstrap_legacy_deployments(session)

    assert inserted == 2
    deployments = [row for row in session.added if hasattr(row, "slug")]
    chat = next(row for row in deployments if row.slug == "siliconflow-default")
    assert chat.credential_ref == "env:OPENAI_API_KEY"
    assert "must-not-be-stored" not in repr(chat.__dict__)
