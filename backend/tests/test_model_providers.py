"""供应商：类型识别、连接检查、拉取模型列表，以及真实 PG 上的创建校验、归类、同步与保护。"""

import asyncio
import os
from types import SimpleNamespace
from urllib.parse import urlparse

import httpx
import pytest

from app.model_runtime.provider_types import PROVIDER_TYPES, default_env_name, infer_capability, type_for
from app.services import model_provider_service as service

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
requires_pg = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL 未设置，跳过真实 PG 供应商测试"
)


# ── 类型 ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("driver", "base_url", "provider_type"),
    [
        ("ark_video", "https://ark.cn-beijing.volces.com/api/v3", "volcengine-ark"),
        ("openai_compatible", "https://ark.cn-beijing.volces.com/api/v3", "volcengine-ark"),
        ("openai_compatible", "https://api.siliconflow.cn/v1", "openai"),
        ("openai_compatible", "https://dashscope.aliyuncs.com/compatible-mode/v1", "dashscope"),
        ("local_h3_gateway", "http://192.168.10.60:8088", "comfyui-gateway"),
        ("qwen_tts", "", "dashscope"),
        ("minimax_video", "https://api.minimaxi.com", "minimax"),
        ("volcano_video", "https://www.dmxapi.cn/v1", "dmxapi"),
    ],
)
def test_type_for_existing_models(driver, base_url, provider_type):
    assert type_for(driver, base_url).id == provider_type


def test_capabilities_and_inference():
    ark = PROVIDER_TYPES["volcengine-ark"]
    assert ark.capabilities == ("chat", "embedding", "image", "video")
    assert ark.driver_for("video") == "ark_video"
    enabled = ("chat", "image", "video")
    assert infer_capability(ark, "doubao-seedance-1-5-pro-251215", enabled) == "video"
    assert infer_capability(ark, "doubao-seedream-4-5", enabled) == "image"
    assert infer_capability(ark, "doubao-seed-1-6", enabled) == "chat"
    assert (
        infer_capability(ark, "doubao-embedding", enabled) == "chat"
    )  # embedding 未勾选时不推断为 embedding
    openai = PROVIDER_TYPES["openai"]
    assert infer_capability(openai, "BAAI/bge-m3", ("chat", "embedding")) == "embedding"
    assert infer_capability(openai, "BAAI/bge-reranker-v2-m3", ("chat", "rerank")) == "rerank"
    assert infer_capability(openai, "BAAI/bge-m3", ("rerank",)) is None
    assert default_env_name("jd-ds") == "JD_DS_API_KEY"


# ── 连接检查与拉取模型列表（MockTransport） ───────────────────────────────


def _client(monkeypatch, handler):
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr("app.services.model_provider_service.httpx.AsyncClient", factory)


def _provider(
    provider_type="openai", base_url="https://api.deepseek.com", endpoint="/models", capabilities=("chat",)
):
    return SimpleNamespace(
        id="p1",
        slug="deepseek",
        name="DeepSeek",
        provider_type=provider_type,
        base_url=base_url,
        credential_ref="env:FS_TEST_KEY",
        models_endpoint=endpoint,
        capabilities=list(capabilities),
    )


def test_check_without_key(monkeypatch):
    monkeypatch.delenv("FS_TEST_KEY", raising=False)
    result = asyncio.run(service.check_provider(_provider(), []))
    assert result == {"ok": False, "message": "环境变量 FS_TEST_KEY 里还没有 API Key"}


@pytest.mark.parametrize(
    ("provider_type", "base_url", "endpoint", "expected_path", "status", "ok", "message"),
    [
        ("openai", "https://api.deepseek.com", "/models", "/models", 200, True, "连接正常"),
        (
            "openai",
            "https://api.deepseek.com",
            "/v1/models",
            "/v1/models",
            401,
            False,
            "API Key 无效或没有权限",
        ),
        (
            "volcengine-ark",
            "https://ark.test/api/v3",
            "/models",
            "/api/v3/contents/generations/tasks",
            200,
            True,
            "连接正常",
        ),
        ("comfyui-gateway", "http://h3.test:8088", "", "/health", 404, False, "地址不正确（接口不存在）"),
    ],
)
def test_check_provider(monkeypatch, provider_type, base_url, endpoint, expected_path, status, ok, message):
    monkeypatch.setenv("FS_TEST_KEY", "secret")
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(status, json={})

    _client(monkeypatch, handler)
    result = asyncio.run(service.check_provider(_provider(provider_type, base_url, endpoint), []))

    assert result == {"ok": ok, "message": message}
    assert seen == {"path": expected_path, "auth": "Bearer secret"}


def test_remote_models_uses_models_endpoint(monkeypatch):
    monkeypatch.setenv("FS_TEST_KEY", "secret")
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(
            200, json={"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}, {"id": "bge-m3"}]}
        )

    _client(monkeypatch, handler)
    existing = [SimpleNamespace(model="deepseek-chat")]

    items = asyncio.run(
        service.remote_models(_provider(endpoint="/v1/models", capabilities=("chat", "embedding")), existing)
    )

    assert seen["path"] == "/v1/models"
    assert items == [
        {"model": "deepseek-chat", "capability": "chat", "added": True},
        {"model": "deepseek-reasoner", "capability": "chat", "added": False},
        {"model": "bge-m3", "capability": "embedding", "added": False},
    ]


def test_remote_models_errors(monkeypatch):
    monkeypatch.setenv("FS_TEST_KEY", "secret")
    _client(monkeypatch, lambda request: httpx.Response(404))
    with pytest.raises(service.ProviderError, match="拉取模型列表失败"):
        asyncio.run(service.remote_models(_provider(), []))
    with pytest.raises(service.ProviderError, match="Models Endpoint"):
        asyncio.run(service.remote_models(_provider(endpoint=""), []))


# ── 真实 PostgreSQL ──────────────────────────────────────────────────────


def _admin_dsn() -> dict:
    parsed = urlparse(TEST_DATABASE_URL.replace("postgresql+asyncpg", "postgresql"))
    return {
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
    }


async def _recreate_database(drop_only: bool = False) -> None:
    import asyncpg

    db = urlparse(TEST_DATABASE_URL.replace("postgresql+asyncpg", "postgresql")).path.lstrip("/")
    conn = await asyncpg.connect(database="postgres", **_admin_dsn())
    try:
        await conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=$1 AND pid <> pg_backend_pid()",
            db,
        )
        await conn.execute(f'DROP DATABASE IF EXISTS "{db}"')
        if not drop_only:
            await conn.execute(f'CREATE DATABASE "{db}"')
    finally:
        await conn.close()


@pytest.fixture
def pg():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL 未设置")
    asyncio.run(_recreate_database())
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    import app.db.migrations as migrations

    mp = pytest.MonkeyPatch()
    mig_engine = create_async_engine(TEST_DATABASE_URL)
    mp.setattr(migrations, "engine", mig_engine)
    mp.setattr(migrations.get_settings(), "database_url", TEST_DATABASE_URL)
    asyncio.run(migrations.bootstrap_and_upgrade())
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())
    asyncio.run(mig_engine.dispose())
    mp.undo()
    asyncio.run(_recreate_database(drop_only=True))


def _deployment(slug, driver, modality, model, base_url, credential_ref):
    from app.model_runtime.deployment_config import DeploymentInput

    return DeploymentInput(
        name=slug,
        slug=slug,
        driver=driver,
        modality=modality,
        model=model,
        base_url=base_url,
        credential_ref=credential_ref,
    )


@requires_pg
def test_existing_models_are_grouped_into_providers(pg):
    from app.services import model_deployment_service

    ark = "https://ark.cn-beijing.volces.com/api/v3"

    async def _run():
        async with pg() as s:
            for payload in (
                _deployment(
                    "seedance", "ark_video", "video", "doubao-seedance-1-5-pro", ark, "env:VOLCANO_API_KEY"
                ),
                _deployment(
                    "doubao-chat", "openai_compatible", "chat", "doubao-seed-1-6", ark, "env:VOLCANO_API_KEY"
                ),
                _deployment(
                    "sf-qwen",
                    "openai_compatible",
                    "chat",
                    "Qwen/Qwen3",
                    "https://api.siliconflow.cn/v1",
                    "env:OPENAI_API_KEY",
                ),
                _deployment(
                    "local-h3",
                    "local_h3_gateway",
                    "video",
                    "MiniMax-H3",
                    "http://192.168.10.60:8088",
                    "env:LOCAL_H3_API_KEY",
                ),
            ):
                await model_deployment_service.create_deployment(s, payload)
            first = await service.assign_unowned_deployments(s)
            second = await service.assign_unowned_deployments(s)
            await s.commit()
            out = {}
            for provider in await service.list_providers(s):
                models = await service.models_of(s, provider)
                out[provider.slug] = (
                    provider.name,
                    provider.provider_type,
                    provider.credential_ref,
                    provider.capabilities,
                    sorted(m.slug for m in models),
                )
            return first, second, out

    first, second, grouped = asyncio.run(_run())

    assert (first, second) == (4, 0)
    assert grouped == {
        "volcengine-ark": (
            "Volcengine Ark",
            "volcengine-ark",
            "env:VOLCANO_API_KEY",
            ["video", "chat"],
            ["doubao-chat", "seedance"],
        ),
        "siliconflow": ("api.siliconflow.cn", "openai", "env:OPENAI_API_KEY", ["chat"], ["sf-qwen"]),
        "comfyui-gateway": (
            "ComfyUI Gateway (H3)",
            "comfyui-gateway",
            "env:LOCAL_H3_API_KEY",
            ["video"],
            ["local-h3"],
        ),
    }


@requires_pg
def test_create_validation(pg):
    async def _run():
        errors = []
        async with pg() as s:
            for kwargs in (
                {"slug": "Bad ID", "name": "x", "provider_type": "openai", "base_url": "https://a.test"},
                {"slug": "ok-id", "name": "x", "provider_type": "unknown", "base_url": "https://a.test"},
                {
                    "slug": "ok-id",
                    "name": "x",
                    "provider_type": "openai",
                    "base_url": "https://a.test",
                    "capabilities": ["video"],
                },
                {
                    "slug": "ok-id",
                    "name": "x",
                    "provider_type": "openai",
                    "base_url": "https://a.test",
                    "api_key_env": "lower",
                },
                {"slug": "ok-id", "name": "x", "provider_type": "comfyui-gateway", "base_url": ""},
            ):
                try:
                    await service.create_provider(s, **kwargs)
                except service.ProviderError as error:
                    errors.append(str(error))
            created = await service.create_provider(
                s,
                slug="jd-ds",
                name="jd-ds",
                provider_type="openai",
                base_url="https://modelservice.jdcloud.com/v1/",
            )
            try:
                await service.create_provider(
                    s, slug="jd-ds", name="dup", provider_type="openai", base_url="https://b.test"
                )
            except service.ProviderError as error:
                errors.append(str(error))
            return errors, (
                created.credential_ref,
                created.base_url,
                created.models_endpoint,
                created.capabilities,
            )

    errors, created = asyncio.run(_run())

    assert len(errors) == 6
    assert errors[0].startswith("Provider ID 只能")
    assert errors[1].startswith("未知 Provider Type")
    assert "不支持能力：video" in errors[2]
    assert errors[3].startswith("API Key Env")
    assert errors[4] == "请填写 Base URL"
    assert errors[5] == "Provider ID 已存在：jd-ds"
    assert created == (
        "env:JD_DS_API_KEY",
        "https://modelservice.jdcloud.com/v1",
        "/models",
        ["chat", "embedding", "rerank", "asr"],
    )


@requires_pg
def test_provider_lifecycle_and_sync(pg):
    from app.db.models import ModelRoute
    from app.model_runtime.catalog import CatalogManager

    async def _run():
        async with pg() as s:
            provider = await service.create_provider(
                s,
                slug="siliconflow-cn",
                name="SiliconFlow",
                provider_type="openai",
                base_url="https://api.siliconflow.cn/v1",
                api_key_env="SILICONFLOW_API_KEY",
                capabilities=["chat", "embedding", "rerank"],
            )
            chat = await service.add_model(
                s,
                provider,
                capability="chat",
                model="Qwen/Qwen3-32B",
                pricing={"input_per_million_tokens": "1", "output_per_million_tokens": "4"},
            )
            embed = await service.add_model(s, provider, capability="embedding", model="BAAI/bge-m3")
            with pytest.raises(service.ProviderError, match="没有开启 asr"):
                await service.add_model(s, provider, capability="asr", model="x")
            with pytest.raises(service.ProviderError, match="先删除这些模型"):
                await service.update_provider(s, provider, capabilities=["chat", "rerank"])
            await service.update_provider(
                s,
                provider,
                base_url="https://api.siliconflow.com/v1/",
                api_key_env="SF_KEY",
                models_endpoint="v1/models",
            )
            await s.refresh(chat)
            await s.refresh(embed)
            s.add(
                ModelRoute(
                    purpose="chat",
                    tier="final",
                    modality="chat",
                    primary_deployment=chat.slug,
                    fallback_deployments=[],
                )
            )
            await s.flush()
            with pytest.raises(service.ProviderError, match="仍被默认模型引用"):
                await service.delete_provider(s, provider)
            await service.update_provider(s, provider, enabled=False)
            await s.commit()
            snapshot = await CatalogManager().refresh(s)
            return {
                "chat": (chat.slug, chat.driver, chat.modality, chat.base_url, chat.credential_ref),
                "embed": (embed.driver, embed.modality, embed.base_url, embed.credential_ref),
                "provider": (provider.models_endpoint, provider.credential_ref),
                "catalog": (snapshot.get(chat.slug).enabled, snapshot.get(embed.slug).enabled),
            }

    result = asyncio.run(_run())

    assert result["chat"] == (
        "siliconflow-cn-qwen-qwen3-32b",
        "openai_compatible",
        "chat",
        "https://api.siliconflow.com/v1",
        "env:SF_KEY",
    )
    assert result["embed"] == ("generic_model", "embedding", "https://api.siliconflow.com/v1", "env:SF_KEY")
    assert result["provider"] == ("/v1/models", "env:SF_KEY")
    assert result["catalog"] == (False, False)


@requires_pg
def test_delete_provider_removes_unreferenced_models(pg):
    async def _run():
        async with pg() as s:
            provider = await service.create_provider(
                s,
                slug="deepseek",
                name="DeepSeek",
                provider_type="openai",
                base_url="https://api.deepseek.com",
            )
            await service.add_model(s, provider, capability="chat", model="deepseek-chat")
            await service.delete_provider(s, provider)
            await s.commit()
            return await service.list_providers(s)

    assert asyncio.run(_run()) == []
