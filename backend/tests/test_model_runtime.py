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
