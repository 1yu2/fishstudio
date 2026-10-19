"""供应商管理：创建 / 修改（同步到其下模型）、密钥、连接检查、拉取模型列表、按能力添加模型，以及存量模型归类。

供应商是 Base URL 与密钥环境变量的唯一真相源；其下模型（Deployment）的 base_url / credential_ref
由这里同步写入，Catalog、路由与 Worker 读 Deployment 的代码保持不变。
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelDeployment, ModelProvider, ModelRoute
from app.model_runtime.deployment_config import DRIVER_SPECS, DeploymentInput, DeploymentPatch
from app.model_runtime.provider_types import (
    CAPABILITY_MODALITY,
    ENV_NAME_PATTERN,
    MODALITY_CAPABILITY,
    PROVIDER_ID_PATTERN,
    PROVIDER_TYPES,
    ProviderType,
    default_env_name,
    host_of,
    infer_capability,
    slugify,
    type_for,
)
from app.services import model_deployment_service

CHECK_TIMEOUT_SECONDS = 15


class ProviderError(ValueError):
    """面向用户的供应商操作错误（消息可直接展示）。"""


def type_of(provider: ModelProvider) -> ProviderType:
    return PROVIDER_TYPES.get(provider.provider_type) or PROVIDER_TYPES["openai"]


def env_name(provider: ModelProvider) -> str:
    return provider.credential_ref.split(":", 1)[1] if provider.credential_ref.startswith("env:") else ""


def credential_value(reference: str) -> str:
    if reference.startswith("env:"):
        return os.getenv(reference.split(":", 1)[1], "").strip()
    return ""


async def list_providers(session: AsyncSession) -> list[ModelProvider]:
    result = await session.execute(
        select(ModelProvider).order_by(ModelProvider.sort_order, ModelProvider.created_at)
    )
    return list(result.scalars().all())


async def get_provider(session: AsyncSession, slug: str) -> Optional[ModelProvider]:
    return (
        await session.execute(select(ModelProvider).where(ModelProvider.slug == slug))
    ).scalar_one_or_none()


async def models_of(session: AsyncSession, provider: ModelProvider) -> list[ModelDeployment]:
    result = await session.execute(
        select(ModelDeployment)
        .where(ModelDeployment.provider_id == provider.id)
        .order_by(ModelDeployment.modality, ModelDeployment.name)
    )
    return list(result.scalars().all())


def _normalize_url(value: str) -> str:
    return (value or "").strip().rstrip("/")


def _normalize_endpoint(value: str) -> str:
    value = (value or "").strip()
    if value and not value.startswith("/"):
        value = f"/{value}"
    return value


def _validate_capabilities(provider_type: ProviderType, capabilities: list[str]) -> list[str]:
    wanted = [item for item in dict.fromkeys(capabilities) if item]
    unsupported = [item for item in wanted if item not in provider_type.drivers]
    if unsupported:
        raise ProviderError(f"{provider_type.label} 不支持能力：{', '.join(unsupported)}")
    if not wanted:
        raise ProviderError("至少选择一种能力")
    return [item for item in provider_type.capabilities if item in wanted]


def _validate_env(name: str) -> str:
    name = (name or "").strip()
    if not ENV_NAME_PATTERN.fullmatch(name):
        raise ProviderError("API Key Env 只能是大写字母、数字和下划线，并以字母开头")
    return name


async def _unique_slug(session: AsyncSession, model, base: str) -> str:
    candidate, index = base, 2
    while (await session.execute(select(model.id).where(model.slug == candidate))).first():
        candidate = f"{base[:115]}-{index}"
        index += 1
    return candidate


async def create_provider(
    session: AsyncSession,
    *,
    slug: str,
    name: str,
    provider_type: str,
    base_url: str = "",
    api_key_env: str = "",
    models_endpoint: Optional[str] = None,
    capabilities: Optional[list[str]] = None,
) -> ModelProvider:
    kind = PROVIDER_TYPES.get(provider_type)
    if kind is None:
        raise ProviderError(f"未知 Provider Type: {provider_type}")
    slug = (slug or "").strip().lower()
    if not PROVIDER_ID_PATTERN.fullmatch(slug):
        raise ProviderError("Provider ID 只能是小写字母、数字、- 和 _，3～64 个字符")
    if await get_provider(session, slug):
        raise ProviderError(f"Provider ID 已存在：{slug}")
    base_url = _normalize_url(base_url or kind.default_base_url)
    if not base_url:
        raise ProviderError("请填写 Base URL")
    provider = ModelProvider(
        slug=slug,
        name=(name or "").strip() or slug,
        provider_type=kind.id,
        base_url=base_url,
        credential_ref=f"env:{_validate_env(api_key_env or default_env_name(slug))}",
        models_endpoint=_normalize_endpoint(
            kind.default_models_endpoint if models_endpoint is None else models_endpoint
        ),
        capabilities=_validate_capabilities(
            kind, capabilities if capabilities is not None else list(kind.capabilities)
        ),
        enabled=True,
        sort_order=100,
    )
    session.add(provider)
    await session.flush()
    return provider


async def assign_unowned_deployments(session: AsyncSession) -> int:
    """把没有供应商的模型按 类型 + 地址 + 密钥变量 归入供应商；幂等，启动时调用。"""
    rows = (
        (await session.execute(select(ModelDeployment).where(ModelDeployment.provider_id.is_(None))))
        .scalars()
        .all()
    )
    if not rows:
        return 0
    providers = await list_providers(session)
    for row in rows:
        kind = type_for(row.driver, row.base_url)
        host = host_of(row.base_url)
        capability = MODALITY_CAPABILITY.get(row.modality, "chat")
        provider = next(
            (
                item
                for item in providers
                if item.provider_type == kind.id
                and item.credential_ref == row.credential_ref
                and host_of(item.base_url) == host
            ),
            None,
        )
        if provider is None:
            labels = host.split(".")
            base = labels[-2] if kind.id == "openai" and len(labels) >= 2 else kind.id
            slug = await _unique_slug(session, ModelProvider, slugify(base))
            provider = ModelProvider(
                slug=slug,
                name=host if kind.id == "openai" and host else kind.label.removesuffix(" API"),
                provider_type=kind.id,
                base_url=row.base_url,
                credential_ref=row.credential_ref,
                models_endpoint=kind.default_models_endpoint,
                capabilities=[],
                enabled=True,
                sort_order=100,
            )
            session.add(provider)
            await session.flush()
            providers.append(provider)
        if capability not in (provider.capabilities or []):
            provider.capabilities = [*list(provider.capabilities or []), capability]
        row.provider_id = provider.id
    await session.flush()
    return len(rows)


async def update_provider(
    session: AsyncSession,
    provider: ModelProvider,
    *,
    name: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key_env: Optional[str] = None,
    models_endpoint: Optional[str] = None,
    capabilities: Optional[list[str]] = None,
    enabled: Optional[bool] = None,
) -> ModelProvider:
    kind = type_of(provider)
    models = await models_of(session, provider)
    if name is not None and name.strip():
        provider.name = name.strip()
    if enabled is not None:
        provider.enabled = enabled
    if models_endpoint is not None:
        provider.models_endpoint = _normalize_endpoint(models_endpoint)
    if capabilities is not None:
        normalized = _validate_capabilities(kind, capabilities)
        in_use = {MODALITY_CAPABILITY.get(model.modality, "") for model in models}
        removed = sorted(item for item in in_use if item and item not in normalized)
        if removed:
            raise ProviderError(f"还有 {', '.join(removed)} 类型的模型，先删除这些模型再取消该能力")
        provider.capabilities = normalized
    patch: dict[str, Any] = {}
    if base_url is not None and _normalize_url(base_url) != provider.base_url:
        patch["base_url"] = _normalize_url(base_url)
    if api_key_env is not None:
        reference = f"env:{_validate_env(api_key_env)}"
        if reference != provider.credential_ref:
            patch["credential_ref"] = reference
    if patch:
        # 先逐个校验并写入模型，任何一个不合法都整体失败。
        for model in models:
            await model_deployment_service.update_deployment(session, model, DeploymentPatch(**patch))
        provider.base_url = patch.get("base_url", provider.base_url)
        provider.credential_ref = patch.get("credential_ref", provider.credential_ref)
    provider.revision += 1
    await session.flush()
    return provider


async def referencing_routes(session: AsyncSession, slugs: list[str]) -> list[str]:
    if not slugs:
        return []
    conditions = [ModelRoute.primary_deployment.in_(slugs)] + [
        ModelRoute.fallback_deployments.contains([slug]) for slug in slugs
    ]
    rows = (await session.execute(select(ModelRoute.purpose, ModelRoute.tier).where(or_(*conditions)))).all()
    return sorted({f"{purpose}:{tier}" for purpose, tier in rows})


async def delete_provider(session: AsyncSession, provider: ModelProvider) -> None:
    models = await models_of(session, provider)
    routes = await referencing_routes(session, [model.slug for model in models])
    if routes:
        raise ProviderError(f"该供应商的模型仍被默认模型引用：{', '.join(routes)}，请先改绑")
    for model in models:
        await session.delete(model)
    await session.flush()
    await session.delete(provider)
    await session.flush()


async def add_model(
    session: AsyncSession,
    provider: ModelProvider,
    *,
    capability: str,
    model: str,
    name: str = "",
    config: Optional[dict[str, Any]] = None,
    capabilities: Optional[dict[str, Any]] = None,
    pricing: Optional[dict[str, Any]] = None,
    enabled: bool = True,
) -> ModelDeployment:
    kind = type_of(provider)
    if capability not in (provider.capabilities or []):
        raise ProviderError(f"{provider.name} 没有开启 {capability} 能力")
    driver = kind.driver_for(capability)
    if driver is None:
        raise ProviderError(f"{kind.label} 不支持 {capability}")
    model = model.strip()
    if not model:
        raise ProviderError("模型 ID 不能为空")
    modality = CAPABILITY_MODALITY[capability]
    if modality not in DRIVER_SPECS[driver].modalities:
        raise ProviderError(f"{driver} 不支持 {capability}")
    slug = await _unique_slug(session, ModelDeployment, slugify(f"{provider.slug}-{model}"))
    payload = DeploymentInput(
        name=name.strip() or model,
        slug=slug,
        driver=driver,
        modality=modality,
        model=model,
        base_url=provider.base_url,
        credential_ref=provider.credential_ref,
        enabled=enabled,
        config=config or {},
        capabilities=capabilities or {},
        pricing=pricing or {},
    )
    row = await model_deployment_service.create_deployment(session, payload)
    row.provider_id = provider.id
    await session.flush()
    return row


def _http_message(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status in {401, 403}:
            return "API Key 无效或没有权限"
        if status == 404:
            return "地址不正确（接口不存在）"
        return f"服务返回 {status}"
    if isinstance(error, httpx.TimeoutException):
        return "连接超时"
    return "无法连接到该地址"


async def check_provider(provider: ModelProvider, models: list[ModelDeployment]) -> dict[str, Any]:
    """检查地址与密钥是否可用；不创建任何生成任务、不产生费用。"""
    kind = type_of(provider)
    key = credential_value(provider.credential_ref)
    if not key:
        return {"ok": False, "message": f"环境变量 {env_name(provider)} 里还没有 API Key"}
    headers = {"Authorization": f"Bearer {key}"}
    base = provider.base_url.rstrip("/")
    method = kind.check
    if method == "models_endpoint" and not provider.models_endpoint:
        method = "deployment_probe"
    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
            if method == "models_endpoint":
                response = await client.get(f"{base}{provider.models_endpoint}", headers=headers)
            elif method == "ark":
                response = await client.get(
                    f"{base}/contents/generations/tasks",
                    headers=headers,
                    params={"page_num": 1, "page_size": 1},
                )
            elif method == "h3_health":
                response = await client.get(f"{base}/health", headers=headers)
            else:
                return await _probe_first_model(models)
            response.raise_for_status()
    except httpx.HTTPError as error:
        return {"ok": False, "message": _http_message(error)}
    return {"ok": True, "message": "连接正常"}


async def _probe_first_model(models: list[ModelDeployment]) -> dict[str, Any]:
    from app.model_runtime.catalog import get_catalog
    from app.model_runtime.registry import DEFAULT_DRIVER_REGISTRY

    item = next((get_catalog().snapshot.get(model.slug) for model in models if model.enabled), None)
    if item is None:
        return {"ok": False, "message": "该类型需要先添加并启用一个模型才能检查"}
    try:
        await DEFAULT_DRIVER_REGISTRY.probe(item.driver, item.instance)
    except Exception as error:  # noqa: BLE001 - 各 Driver 的探测异常类型不同
        return {"ok": False, "message": str(error)[:200] or "检查失败"}
    return {"ok": True, "message": "连接正常"}


async def remote_models(provider: ModelProvider, existing: list[ModelDeployment]) -> list[dict[str, Any]]:
    """GET {base_url}{models_endpoint}，返回可添加的模型及推断的能力。"""
    if not provider.models_endpoint:
        raise ProviderError("没有配置 Models Endpoint，请手动添加模型")
    key = credential_value(provider.credential_ref)
    if not key:
        raise ProviderError(f"环境变量 {env_name(provider)} 里还没有 API Key")
    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{provider.base_url.rstrip('/')}{provider.models_endpoint}",
                headers={"Authorization": f"Bearer {key}"},
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as error:
        message = _http_message(error) if isinstance(error, httpx.HTTPError) else "返回格式无法识别"
        raise ProviderError(f"拉取模型列表失败：{message}") from error
    kind = type_of(provider)
    enabled = tuple(provider.capabilities or [])
    added = {row.model for row in existing}
    entries = data.get("data") if isinstance(data, dict) else data
    items: list[dict[str, Any]] = []
    for entry in entries or []:
        model_id = str(entry.get("id") or "").strip() if isinstance(entry, dict) else str(entry).strip()
        capability = infer_capability(kind, model_id, enabled) if model_id else None
        if capability is None:
            continue
        items.append({"model": model_id, "capability": capability, "added": model_id in added})
    return sorted(items, key=lambda item: (item["capability"], item["model"]))


def provider_dto(
    provider: ModelProvider, models: list[ModelDeployment], availability: dict[str, bool]
) -> dict[str, Any]:
    return {
        "id": provider.id,
        "slug": provider.slug,
        "name": provider.name,
        "provider_type": type_of(provider).summary(),
        "base_url": provider.base_url,
        # 环境变量名不是密钥本身；密钥值永不返回
        "api_key_env": env_name(provider),
        "credential_configured": bool(credential_value(provider.credential_ref)),
        "models_endpoint": provider.models_endpoint,
        "capabilities": list(provider.capabilities or []),
        "enabled": provider.enabled,
        "revision": provider.revision,
        "models": [
            {
                **model_deployment_service.to_dto(model).model_dump(mode="json"),
                "capability": MODALITY_CAPABILITY.get(model.modality, model.modality),
                "available": availability.get(model.slug),
            }
            for model in models
        ],
    }
