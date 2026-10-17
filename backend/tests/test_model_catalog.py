import asyncio
from types import SimpleNamespace

import pytest

from app.model_runtime.catalog import CatalogManager


def _row(**updates):
    values = {
        "id": "dep-1",
        "name": "Local H3",
        "slug": "local-h3",
        "driver": "local_h3_gateway",
        "modality": "video",
        "model": "MiniMax-H3",
        "base_url": "http://h3.test",
        "credential_ref": "env:LOCAL_H3_API_KEY",
        "enabled": True,
        "priority": 100,
        "config": {"max_concurrency": 1},
        "capabilities": {"modes": ["text"]},
        "pricing": {},
        "revision": 1,
        "config_hash": "a" * 64,
    }
    values.update(updates)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_catalog_builds_all_modalities_from_one_snapshot(monkeypatch):
    monkeypatch.setenv("LOCAL_H3_API_KEY", "test-key")
    monkeypatch.setenv("CHAT_KEY", "test-key")
    manager = CatalogManager()
    chat = _row(
        id="dep-2",
        name="Chat",
        slug="chat-primary",
        driver="openai_compatible",
        modality="chat",
        model="chat-model",
        base_url="https://chat.test/v1",
        credential_ref="env:CHAT_KEY",
        config={},
        capabilities={},
        revision=2,
        config_hash="b" * 64,
    )

    snapshot = await manager.reload_rows([_row(), chat])

    assert {item.modality for item in snapshot.list()} == {"chat", "video"}
    assert snapshot.get("local-h3").instance.max_concurrency == 1
    assert snapshot.get("chat-primary").instance.enabled is True
    with pytest.raises(TypeError):
        snapshot.deployments["new"] = object()


@pytest.mark.asyncio
async def test_failed_reload_keeps_previous_snapshot(monkeypatch):
    monkeypatch.setenv("LOCAL_H3_API_KEY", "test-key")
    manager = CatalogManager()
    first = await manager.reload_rows([_row()])

    with pytest.raises(ValueError, match="未知 Driver"):
        await manager.reload_rows([_row(driver="missing", revision=2, config_hash="b" * 64)])

    assert manager.snapshot is first
    assert manager.snapshot.get("local-h3") is not None
    assert manager.last_failure is not None
    assert "未知 Driver" in manager.last_failure.reason


@pytest.mark.asyncio
async def test_reload_is_atomic_for_concurrent_readers(monkeypatch):
    monkeypatch.setenv("LOCAL_H3_API_KEY", "test-key")
    manager = CatalogManager()
    await manager.reload_rows([_row()])
    observed = []

    async def reader():
        for _ in range(100):
            observed.append(tuple(manager.snapshot.deployments))
            await asyncio.sleep(0)

    updated = [
        _row(revision=2, config_hash="b" * 64),
        _row(
            id="dep-2",
            slug="local-h3-second",
            name="Local H3 second",
            revision=1,
            config_hash="c" * 64,
        ),
    ]
    await asyncio.gather(reader(), manager.reload_rows(updated), reader())

    assert set(observed) <= {
        ("local-h3",),
        ("local-h3", "local-h3-second"),
    }


@pytest.mark.asyncio
async def test_probe_failure_does_not_remove_current_catalog(monkeypatch):
    monkeypatch.setenv("LOCAL_H3_API_KEY", "test-key")
    manager = CatalogManager()
    first = await manager.reload_rows([_row()])

    async def failed_probe(_payload, _instance):
        raise RuntimeError("gateway offline")

    monkeypatch.setattr(manager.registry, "probe", failed_probe)
    with pytest.raises(RuntimeError, match="gateway offline"):
        await manager.reload_rows([_row(revision=2, config_hash="d" * 64)], probe=True)

    assert manager.snapshot is first
