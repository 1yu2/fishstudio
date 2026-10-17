from types import MappingProxyType, SimpleNamespace

import pytest

from app.model_runtime.catalog import CatalogDeployment, CatalogSnapshot
from app.model_runtime.image import ImageResolver
from app.providers.base import ProviderResult
from app.providers.image_base import ImageGenerationRequest, ImageProvider


class FakeSession:
    def __init__(self, routes):
        self.routes = routes

    async def get(self, model, key):
        return self.routes.get(key)


class FakeImageProvider(ImageProvider):
    name = "fake_image"

    def __init__(self, model, *, available=True):
        self.model_name = model
        self.available = available
        self.calls = []

    def is_available(self):
        return self.available

    async def generate(self, req):
        self.calls.append(req)
        return ProviderResult(
            success=True,
            provider=self.name,
            model=self.model_name,
            artifacts=[{"storage_key": f"images/{self.model_name}.png", "type": "image"}],
        )


def _deployment(slug, provider, *, operations):
    return CatalogDeployment(
        id=f"id-{slug}",
        slug=slug,
        name=slug,
        driver="volcano_image",
        modality="image",
        model=provider.model_name,
        base_url="https://image.test/v1",
        enabled=True,
        priority=100,
        capabilities=MappingProxyType({"operations": operations}),
        pricing=MappingProxyType({}),
        revision=1,
        config_hash=slug[0] * 64,
        instance=provider,
    )


def _catalog(*deployments):
    snapshot = CatalogSnapshot(
        deployments=MappingProxyType({item.slug: item for item in deployments}),
        catalog_revision="catalog-1",
        loaded_at=CatalogSnapshot.empty().loaded_at,
    )
    return SimpleNamespace(snapshot=snapshot)


def _route(purpose, primary, fallbacks=None, revision=1):
    return SimpleNamespace(
        purpose=purpose,
        modality="image",
        primary_deployment=primary,
        fallback_deployments=fallbacks or [],
        enabled=True,
        revision=revision,
    )


@pytest.mark.asyncio
async def test_two_image_deployments_are_instance_isolated(monkeypatch):
    import app.providers.volcano_image as module
    import app.tools.volcano_image_generation as legacy_tool
    from app.providers.volcano_image import VolcanoImageProvider

    calls = []

    class Response:
        status_code = 200
        text = ""

        def __init__(self, model):
            self.model = model

        def json(self):
            return {"data": [{"url": f"https://cdn.test/{self.model}.png"}]}

    def fake_post(url, *, json, headers, timeout):
        calls.append((url, json["model"], headers["Authorization"], timeout))
        return Response(json["model"])

    monkeypatch.setattr(module.requests, "post", fake_post)
    monkeypatch.setattr(
        module,
        "download_and_save_image",
        lambda url, prompt="": f"/storage/images/{url.rsplit('/', 1)[-1]}",
    )
    original_globals = (
        legacy_tool.VOLCANO_API_KEY,
        legacy_tool.VOLCANO_BASE_URL,
        legacy_tool.VOLCANO_IMAGE_MODEL,
    )
    first = VolcanoImageProvider(
        api_key="key-one", base_url="https://one.test/v1", model_name="model-one"
    )
    second = VolcanoImageProvider(
        api_key="key-two", base_url="https://two.test/v1", model_name="model-two"
    )

    first_result = await first.generate(ImageGenerationRequest(prompt="one"))
    second_result = await second.generate(ImageGenerationRequest(prompt="two"))

    assert first_result.model == "model-one"
    assert second_result.model == "model-two"
    assert calls == [
        ("https://one.test/v1/images/generations", "model-one", "Bearer key-one", 120),
        ("https://two.test/v1/images/generations", "model-two", "Bearer key-two", 120),
    ]
    assert (
        legacy_tool.VOLCANO_API_KEY,
        legacy_tool.VOLCANO_BASE_URL,
        legacy_tool.VOLCANO_IMAGE_MODEL,
    ) == original_globals


@pytest.mark.asyncio
async def test_route_primary_change_is_used_on_next_request_without_reload():
    first = FakeImageProvider("first-model")
    second = FakeImageProvider("second-model")
    resolver = ImageResolver(
        _catalog(
            _deployment("first", first, operations=["generate"]),
            _deployment("second", second, operations=["generate"]),
        ),
        mock_enabled=lambda: False,
    )
    routes = {"image_generation": _route("image_generation", "first", ["second"])}
    session = FakeSession(routes)

    before = await resolver.execute(session, ImageGenerationRequest(prompt="before"))
    routes["image_generation"] = _route("image_generation", "second", ["first"], revision=2)
    after = await resolver.execute(session, ImageGenerationRequest(prompt="after"))

    assert before.deployment_name == "first"
    assert after.deployment_name == "second"
    assert [call.prompt for call in first.calls] == ["before"]
    assert [call.prompt for call in second.calls] == ["after"]


@pytest.mark.asyncio
async def test_generation_and_edit_routes_can_use_different_deployments():
    generator = FakeImageProvider("generation-model")
    editor = FakeImageProvider("edit-model")
    resolver = ImageResolver(
        _catalog(
            _deployment("generator", generator, operations=["generate"]),
            _deployment("editor", editor, operations=["edit"]),
        ),
        mock_enabled=lambda: False,
    )
    session = FakeSession(
        {
            "image_generation": _route("image_generation", "generator"),
            "image_edit": _route("image_edit", "editor"),
        }
    )

    generated = await resolver.execute(session, ImageGenerationRequest(prompt="new"))
    edited = await resolver.execute(
        session,
        ImageGenerationRequest(prompt="edit", source_image="/storage/images/ref.png"),
    )

    assert generated.deployment_name == "generator"
    assert generated.model == "generation-model"
    assert edited.deployment_name == "editor"
    assert edited.model == "edit-model"


@pytest.mark.asyncio
async def test_unavailable_primary_uses_configured_fallback_order():
    primary = FakeImageProvider("primary-model", available=False)
    fallback = FakeImageProvider("fallback-model")
    resolver = ImageResolver(
        _catalog(
            _deployment("primary", primary, operations=["generate"]),
            _deployment("fallback", fallback, operations=["generate"]),
        ),
        mock_enabled=lambda: False,
    )
    session = FakeSession(
        {"image_generation": _route("image_generation", "primary", ["fallback"])}
    )

    execution = await resolver.execute(session, ImageGenerationRequest(prompt="fallback"))

    assert execution.deployment_name == "fallback"
    assert execution.routing_snapshot["fallback_used"] is True
    assert execution.routing_snapshot["unavailable"] == [
        {"deployment": "primary", "reason": "unavailable"}
    ]
    assert primary.calls == []
    assert len(fallback.calls) == 1


@pytest.mark.asyncio
async def test_legacy_env_is_not_used_when_db_image_config_exists(monkeypatch):
    configured = FakeImageProvider("configured-model")
    resolver = ImageResolver(
        _catalog(_deployment("configured", configured, operations=["generate"])),
        mock_enabled=lambda: False,
    )
    legacy_called = False

    def legacy_provider(_purpose):
        nonlocal legacy_called
        legacy_called = True
        return FakeImageProvider("legacy-model")

    monkeypatch.setattr(resolver, "_legacy_provider", legacy_provider)

    with pytest.raises(RuntimeError, match="未配置用途 Route"):
        await resolver.execute(FakeSession({}), ImageGenerationRequest(prompt="x"))
    assert legacy_called is False


@pytest.mark.asyncio
async def test_legacy_mock_mode_is_kept_only_without_db_config(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("MOCK_IMAGE_PATH", "/storage/images/legacy.png")
    resolver = ImageResolver(_catalog(), mock_enabled=lambda: False)

    execution = await resolver.execute(
        FakeSession({}), ImageGenerationRequest(prompt="legacy mock")
    )

    assert execution.mocked is True
    assert execution.deployment_name == "legacy-mock-image"
    assert execution.result.artifacts[0]["storage_key"] == "images/legacy.png"


@pytest.mark.asyncio
async def test_agent_tool_and_worker_call_the_same_resolver(monkeypatch):
    import app.model_runtime.image as image_runtime
    from app.tools.routed_image_generation import _resolve_tool_image
    from app.worker.generation_worker import _resolve_worker_image

    sentinel = object()
    calls = []

    async def fake_resolve(session, req, *, purpose=None):
        calls.append((session, req.prompt, purpose))
        return sentinel

    monkeypatch.setattr(image_runtime, "resolve_and_generate_image", fake_resolve)
    session = object()

    agent_result = await _resolve_tool_image(
        session, ImageGenerationRequest(prompt="agent"), "image_generation"
    )
    worker_result = await _resolve_worker_image(
        session, ImageGenerationRequest(prompt="worker"), "image_generation"
    )

    assert agent_result is sentinel
    assert worker_result is sentinel
    assert calls == [
        (session, "agent", "image_generation"),
        (session, "worker", "image_generation"),
    ]
