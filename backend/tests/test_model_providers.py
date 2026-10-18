"""供应商 → 模型 两级配置：预设识别、连接检查、拉取模型列表，以及真实 PG 上的归类与同步。"""

import asyncio
import os
from types import SimpleNamespace
from urllib.parse import urlparse

import httpx
import pytest

from app.model_runtime.provider_presets import (
    PRESETS,
    infer_model_kind,
    preset_for,
    provider_env_name,
    slugify,
)
from app.services import model_provider_service as service

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
requires_pg = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="TEST_DATABASE_URL 未设置，跳过真实 PG 供应商测试"
)


# ── 预设 ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("driver", "base_url", "preset"),
    [
        ("ark_video", "https://ark.cn-beijing.volces.com/api/v3", "volcano-ark"),
        ("openai_compatible", "https://ark.cn-beijing.volces.com/api/v3", "volcano-ark"),
        ("openai_compatible", "https://api.siliconflow.cn/v1", "siliconflow"),
        ("openai_compatible", "https://api.deepseek.com/v1", "deepseek"),
        ("openai_compatible", "http://10.0.0.5:8000/v1", "openai-compatible"),
        ("local_h3_gateway", "http://192.168.10.60:8088", "local-h3"),
        ("qwen_tts", "", "dashscope"),
        ("minimax_video", "https://api.minimaxi.com", "minimax"),
    ],
)
def test_preset_for_existing_models(driver, base_url, preset):
    assert preset_for(driver, base_url).id == preset


def test_infer_model_kind_and_names():
    ark = PRESETS["volcano-ark"]
    assert infer_model_kind(ark, "doubao-seedance-1-5-pro-251215") == ("ark_video", "video")
    assert infer_model_kind(ark, "doubao-seedream-4-5") == ("volcano_image", "image")
    assert infer_model_kind(ark, "doubao-seed-1-6") == ("openai_compatible", "chat")
    assert infer_model_kind(PRESETS["siliconflow"], "BAAI/bge-m3-embedding") is None
    assert infer_model_kind(PRESETS["local-h3"], "MiniMax-H3") is None
    assert provider_env_name("api-deepseek-com") == "FS_PROVIDER_API_DEEPSEEK_COM_API_KEY"
    assert slugify("api.deepseek.com") == "api-deepseek-com"


# ── 连接检查与拉取模型列表（MockTransport） ───────────────────────────────


def _client(monkeypatch, handler):
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr("app.services.model_provider_service.httpx.AsyncClient", factory)


def _provider(preset="deepseek", base_url="https://api.deepseek.com/v1"):
    return SimpleNamespace(
        id="p1",
        slug=preset,
        name=PRESETS[preset].name,
        preset=preset,
        base_url=base_url,
        credential_ref="env:FS_TEST_KEY",
    )


def test_check_without_key(monkeypatch):
    monkeypatch.delenv("FS_TEST_KEY", raising=False)
    result = asyncio.run(service.check_provider(_provider(), []))
    assert result == {"ok": False, "message": "还没有填写 API 密钥"}


@pytest.mark.parametrize(
    ("preset", "base_url", "expected_path", "status", "ok", "message"),
    [
        ("deepseek", "https://api.deepseek.com/v1", "/v1/models", 200, True, "连接正常"),
        ("deepseek", "https://api.deepseek.com/v1", "/v1/models", 401, False, "密钥无效或没有权限"),
        (
            "volcano-ark",
            "https://ark.test/api/v3",
            "/api/v3/contents/generations/tasks",
            200,
            True,
            "连接正常",
        ),
        ("local-h3", "http://h3.test:8088", "/health", 404, False, "地址不正确（接口不存在）"),
    ],
)
def test_check_provider(monkeypatch, preset, base_url, expected_path, status, ok, message):
    monkeypatch.setenv("FS_TEST_KEY", "secret")
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(status, json={})

    _client(monkeypatch, handler)
    result = asyncio.run(service.check_provider(_provider(preset, base_url), []))

    assert result == {"ok": ok, "message": message}
    assert seen == {"path": expected_path, "auth": "Bearer secret"}


def test_remote_models_infers_kinds_and_marks_added(monkeypatch):
    monkeypatch.setenv("FS_TEST_KEY", "secret")
    payload = {"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}, {"id": "text-embedding-x"}]}
    _client(monkeypatch, lambda request: httpx.Response(200, json=payload))
    existing = [SimpleNamespace(driver="openai_compatible", model="deepseek-chat")]

    items = asyncio.run(service.remote_models(_provider(), existing))

    assert items == [
        {"model": "deepseek-chat", "driver": "openai_compatible", "modality": "chat", "added": True},
        {"model": "deepseek-reasoner", "driver": "openai_compatible", "modality": "chat", "added": False},
    ]


def test_remote_models_errors_are_user_facing(monkeypatch):
    monkeypatch.setenv("FS_TEST_KEY", "secret")
    _client(monkeypatch, lambda request: httpx.Response(404))
    with pytest.raises(service.ProviderError, match="控制台"):
        asyncio.run(service.remote_models(_provider("volcano-ark", "https://ark.test/api/v3"), []))
    with pytest.raises(service.ProviderError, match="不提供模型列表"):
        asyncio.run(service.remote_models(_provider("minimax", "https://api.minimaxi.com"), []))


# ── 真实 PostgreSQL：归类、同步、删除保护、停用供应商 ─────────────────────


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


def _deployment(slug, driver, modality, model, base_url, credential_ref, **extra):
    from app.model_runtime.deployment_config import DeploymentInput

    return DeploymentInput(
        name=slug,
        slug=slug,
        driver=driver,
        modality=modality,
        model=model,
        base_url=base_url,
        credential_ref=credential_ref,
        **extra,
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
            providers = await service.list_providers(s)
            grouped = {p.slug: sorted(m.slug for m in await service.models_of(s, p)) for p in providers}
            return first, second, grouped, {p.slug: p.credential_ref for p in providers}

    first, second, grouped, refs = asyncio.run(_run())

    assert (first, second) == (4, 0)
    assert grouped == {
        "volcano-ark": ["doubao-chat", "seedance"],
        "local-h3": ["local-h3"],
        "siliconflow": ["sf-qwen"],
    }
    # 存量供应商沿用原有密钥变量，不要求重新填写
    assert refs == {
        "volcano-ark": "env:VOLCANO_API_KEY",
        "local-h3": "env:LOCAL_H3_API_KEY",
        "siliconflow": "env:OPENAI_API_KEY",
    }


@requires_pg
def test_provider_lifecycle_and_sync(pg):
    from app.db.models import ModelRoute
    from app.model_runtime.catalog import CatalogManager

    async def _run():
        async with pg() as s:
            deepseek = await service.create_provider(s, "deepseek")
            custom = await service.create_provider(
                s, "openai-compatible", name="vLLM", base_url="http://10.0.0.5:8000/v1/"
            )
            second_sf = await service.create_provider(s, "siliconflow")
            third_sf = await service.create_provider(s, "siliconflow")
            names = (second_sf.name, third_sf.name)
            chat = await service.add_model(
                s,
                deepseek,
                driver="openai_compatible",
                model="deepseek-chat",
                pricing={"input_per_million_tokens": "2", "output_per_million_tokens": "8"},
            )
            with pytest.raises(service.ProviderError, match="不支持"):
                await service.add_model(s, deepseek, driver="ark_video", model="x")
            revision_before = chat.revision
            await service.update_provider(s, deepseek, base_url="https://api.deepseek.com/v2/")
            await s.refresh(chat)
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
                await service.delete_provider(s, deepseek)
            await service.update_provider(s, deepseek, enabled=False)
            await s.commit()
            snapshot = await CatalogManager().refresh(s)
            return {
                "deepseek": (deepseek.slug, deepseek.credential_ref, deepseek.base_url),
                "custom": (custom.slug, custom.name, custom.base_url, custom.credential_ref),
                "sf": (second_sf.slug, second_sf.credential_ref, third_sf.slug, third_sf.credential_ref),
                "chat": (
                    chat.slug,
                    chat.base_url,
                    chat.credential_ref,
                    chat.provider_id == deepseek.id,
                    chat.revision > revision_before,
                ),
                "catalog_enabled": snapshot.get(chat.slug).enabled,
                "names": names,
            }

    result = asyncio.run(_run())

    assert result["deepseek"] == (
        "deepseek",
        "env:FS_PROVIDER_DEEPSEEK_API_KEY",
        "https://api.deepseek.com/v2",
    )
    assert result["custom"] == ("vllm", "vLLM", "http://10.0.0.5:8000/v1", "env:FS_PROVIDER_VLLM_API_KEY")
    # 第一个硅基流动沿用 OPENAI_API_KEY；第二个同类供应商自动换成独立变量
    assert result["sf"] == (
        "siliconflow",
        "env:OPENAI_API_KEY",
        "siliconflow-2",
        "env:FS_PROVIDER_SILICONFLOW_2_API_KEY",
    )
    assert result["chat"] == (
        "deepseek-deepseek-chat",
        "https://api.deepseek.com/v2",
        "env:FS_PROVIDER_DEEPSEEK_API_KEY",
        True,
        True,
    )
    assert result["catalog_enabled"] is False
    assert result["names"] == ("硅基流动", "硅基流动 2")


@requires_pg
def test_delete_provider_removes_unreferenced_models(pg):
    async def _run():
        async with pg() as s:
            provider = await service.create_provider(s, "moonshot")
            await service.add_model(s, provider, driver="openai_compatible", model="kimi-k2")
            await service.delete_provider(s, provider)
            await s.commit()
            return await service.list_providers(s)

    assert asyncio.run(_run()) == []
