from types import SimpleNamespace

import pytest

from app.model_runtime.catalog import CatalogManager
from app.model_runtime.routes import (
    ModelRouteInput,
    fallback_candidate,
    resolve_route,
    validate_route,
)


def _deployment(slug, *, revision=1, enabled=True, capabilities=None):
    return SimpleNamespace(
        id=f"id-{slug}",
        name=slug,
        slug=slug,
        driver="openai_compatible",
        modality="chat",
        model=f"model-{slug}",
        base_url="https://chat.test/v1",
        credential_ref="env:CHAT_KEY",
        enabled=enabled,
        priority=100,
        config={},
        capabilities=capabilities or {},
        pricing={"currency": "CNY", "input_per_token": "0.01"},
        revision=revision,
        config_hash=(slug[0] * 64),
    )


def _route(**updates):
    values = {
        "purpose": "chat",
        "modality": "chat",
        "primary_deployment": "primary",
        "fallback_deployments": ["fallback"],
        "enabled": True,
        "revision": 3,
    }
    values.update(updates)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_route_snapshot_locks_all_revisions(monkeypatch):
    monkeypatch.setenv("CHAT_KEY", "key")
    manager = CatalogManager()
    catalog = await manager.reload_rows([
        _deployment("primary", revision=4),
        _deployment("fallback", revision=7),
    ])

    snapshot = resolve_route(_route(), catalog)

    assert snapshot.route_revision == 3
    assert snapshot.catalog_revision == catalog.catalog_revision
    assert snapshot.selected.slug == "primary"
    assert snapshot.selected.deployment_revision == 4
    assert snapshot.candidates[1].deployment_revision == 7


@pytest.mark.asyncio
async def test_route_validation_rejects_disabled_or_missing_capability(monkeypatch):
    monkeypatch.setenv("CHAT_KEY", "key")
    manager = CatalogManager()
    catalog = await manager.reload_rows([
        _deployment("primary", enabled=False),
        _deployment("fallback"),
    ])
    with pytest.raises(ValueError, match="已停用"):
        validate_route(ModelRouteInput.model_validate(_route().__dict__), catalog)

    catalog = await manager.reload_rows([
        _deployment("primary", revision=2, capabilities={"vision": False}),
        _deployment("fallback", revision=2, capabilities={"vision": False}),
    ])
    reviewer = ModelRouteInput.model_validate({
        **_route().__dict__, "purpose": "reviewer", "fallback_deployments": []
    })
    with pytest.raises(ValueError, match="缺少用途"):
        validate_route(reviewer, catalog)


@pytest.mark.asyncio
async def test_explicit_route_never_falls_back(monkeypatch):
    monkeypatch.setenv("CHAT_KEY", "key")
    catalog = await CatalogManager().reload_rows([
        _deployment("primary"), _deployment("fallback")
    ])
    snapshot = resolve_route(_route(), catalog, explicit_deployment="fallback")
    assert [item.slug for item in snapshot.candidates] == ["fallback"]
    with pytest.raises(RuntimeError, match="显式选择"):
        fallback_candidate(snapshot, 1)


@pytest.mark.asyncio
async def test_async_submission_forbids_provider_fallback(monkeypatch):
    monkeypatch.setenv("CHAT_KEY", "key")
    catalog = await CatalogManager().reload_rows([
        _deployment("primary"), _deployment("fallback")
    ])
    snapshot = resolve_route(_route(), catalog)
    assert fallback_candidate(snapshot, 1).slug == "fallback"
    with pytest.raises(RuntimeError, match="已提交"):
        fallback_candidate(snapshot, 1, submitted=True)
