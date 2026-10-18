import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.model_runtime import (
    ChatDeployment,
    ChatModelRequest,
    ModelCapability,
    ModelPolicy,
    ModelRequestContext,
    ModelRuntime,
)
from app.model_runtime.media import MediaDeployment, MediaRuntime


def _runtime() -> ModelRuntime:
    runtime = ModelRuntime()
    runtime.register_chat(
        ChatDeployment(
            name="cheap",
            provider="fake",
            model="cheap-v1",
            factory=lambda: FakeListChatModel(responses=["ok"]),
            capabilities=ModelCapability(),
            cost_score=1,
            quality_score=0.2,
            latency_score=0.4,
        )
    )
    runtime.register_chat(
        ChatDeployment(
            name="quality",
            provider="fake",
            model="quality-v1",
            factory=lambda: FakeListChatModel(responses=["ok"]),
            capabilities=ModelCapability(supports_tools=True, supports_json_schema=True),
            cost_score=0.2,
            quality_score=1,
            latency_score=0.8,
        )
    )
    return runtime


def test_resolver_uses_policy_not_provider_branch():
    selected = _runtime().resolve_chat(
        ModelPolicy(quality_tier="quality", required_capabilities=frozenset({"tools"}))
    )
    assert selected.name == "quality"


def test_resolver_supports_explicit_deployment():
    assert _runtime().resolve_chat(ModelPolicy(explicit_deployment="cheap")).provider == "fake"


def test_resolver_reports_capability_failure():
    with pytest.raises(RuntimeError, match="no chat deployment"):
        _runtime().resolve_chat(ModelPolicy(required_capabilities=frozenset({"vision"})))


def test_duplicate_deployment_is_rejected():
    runtime = _runtime()
    with pytest.raises(ValueError, match="already registered"):
        runtime.register_chat(runtime.list_chat()[0])


@pytest.mark.asyncio
async def test_invoke_chat_returns_routing_snapshot_and_context():
    result = await _runtime().invoke_chat(
        [{"role": "user", "content": "hello"}],
        ChatModelRequest(ModelPolicy(explicit_deployment="cheap")),
        context=ModelRequestContext(creative_role="episode_writer", prompt_version="p1"),
    )
    assert result.snapshot.deployment_name == "cheap"
    assert result.snapshot.provider == "fake"
    assert result.response.content == "ok"


def test_media_registry_resolves_registered_adapter():
    runtime = MediaRuntime()
    adapter = object()
    runtime.register(MediaDeployment("fake-image", "image", adapter))
    assert runtime.get("image", "fake-image").provider is adapter
    assert runtime.list("video") == []


@pytest.mark.asyncio
async def test_persisted_invocation_reaches_success_terminal(monkeypatch):
    calls = []

    async def fake_begin(**fields):
        calls.append(("begin", fields))
        return "inv-1"

    async def fake_finish(invocation_id, **fields):
        calls.append(("finish", invocation_id, fields))

    monkeypatch.setattr("app.services.model_invocation_service.begin_managed", fake_begin)
    monkeypatch.setattr("app.services.model_invocation_service.finish_managed", fake_finish)
    runtime = _runtime()
    runtime.persist_invocations = True
    result = await runtime.invoke_chat(
        [{"role": "user", "content": "hello"}],
        ChatModelRequest(ModelPolicy(explicit_deployment="cheap")),
    )
    assert result.invocation_id == "inv-1"
    assert calls[0][0] == "begin"
    assert calls[1][2]["status"] == "succeeded"


# ── 对话模型遵循「默认模型」（用途路由） ──────────────────────────────────


def _with_routes(monkeypatch, routes):
    from datetime import datetime, timezone
    from types import MappingProxyType, SimpleNamespace

    from app.model_runtime.catalog import CatalogSnapshot

    snapshot = CatalogSnapshot(
        MappingProxyType({}), "rev", datetime.now(timezone.utc), MappingProxyType(routes)
    )
    monkeypatch.setattr("app.model_runtime.catalog.get_catalog", lambda: SimpleNamespace(snapshot=snapshot))


def test_chat_resolution_follows_purpose_route(monkeypatch):
    _with_routes(monkeypatch, {("planner", "final"): ("cheap",), ("chat", "final"): ("quality",)})
    runtime = _runtime()

    # 默认打分会选 quality；设置了用途路由后按设置走
    assert runtime.resolve_chat(ModelPolicy(purpose="planner")).name == "cheap"
    # agent 映射到「对话 / Copilot」
    assert runtime.resolve_chat(ModelPolicy(purpose="agent")).name == "quality"
    # 没有单独设置的用途回退到对话默认模型
    assert runtime.resolve_chat(ModelPolicy(purpose="director")).name == "quality"
    # 显式指定优先于默认模型
    assert (
        runtime.resolve_chat(ModelPolicy(purpose="planner", explicit_deployment="quality")).name == "quality"
    )


def test_route_choice_skips_models_missing_capabilities(monkeypatch):
    _with_routes(monkeypatch, {("planner", "final"): ("cheap", "quality")})

    selected = _runtime().resolve_chat(
        ModelPolicy(purpose="planner", required_capabilities=frozenset({"tools"}))
    )

    assert selected.name == "quality"


def test_unknown_routes_fall_back_to_scoring(monkeypatch):
    _with_routes(monkeypatch, {("chat", "final"): ("deleted-model",)})

    assert _runtime().resolve_chat(ModelPolicy(purpose="chat", quality_tier="quality")).name == "quality"


@pytest.mark.asyncio
async def test_catalog_snapshot_carries_routes_and_revision_changes():
    from app.model_runtime.catalog import CatalogManager

    manager = CatalogManager()
    first = await manager.reload_rows([], routes={("chat", "final"): ("a",)})
    same = await manager.reload_rows([], routes={("chat", "final"): ("a",)})
    changed = await manager.reload_rows([], routes={("chat", "final"): ("b", "a")})

    assert first.route_candidates("chat") == ("a",)
    assert same is first
    assert changed.catalog_revision != first.catalog_revision
    assert changed.route_candidates("chat", "preview") == ("b", "a")
