"""供应商（账号）管理：归类存量模型、增删改、密钥、连接检查、拉取模型列表、在供应商下添加模型。

供应商是地址与密钥引用的唯一真相源；其下模型（Deployment）的 base_url / credential_ref
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
from app.model_runtime.provider_presets import (
    PRESETS,
    ProviderPreset,
    host_of,
    infer_model_kind,
    preset_for,
    provider_env_name,
    slugify,
)
from app.services import model_deployment_service

CHECK_TIMEOUT_SECONDS = 15


class ProviderError(ValueError):
    """面向用户的供应商操作错误（消息可直接展示）。"""


def preset_of(provider: ModelProvider) -> ProviderPreset:
    return PRESETS.get(provider.preset) or PRESETS["openai-compatible"]


def credential_value(reference: str) -> str:
    if reference.startswith("env:"):
        return os.getenv(reference.split(":", 1)[1], "").strip()
    return ""


async def list_providers(session: AsyncSession) -> list[ModelProvider]:
    result = await session.execute(
        select(ModelProvider).order_by(ModelProvider.sort_order, ModelProvider.name)
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


async def _unique_slug(session: AsyncSession, model, base: str) -> str:
    candidate, index = base, 2
    while (await session.execute(select(model.id).where(model.slug == candidate))).first():
        candidate = f"{base[:115]}-{index}"
        index += 1
    return candidate


async def _credential_ref_for(session: AsyncSession, preset: ProviderPreset, slug: str) -> str:
    """预设沿用旧变量（兼容已有 .env）；该变量已被其他供应商占用时用独立变量。"""
    if preset.credential_env:
        reference = f"env:{preset.credential_env}"
        taken = (
            await session.execute(select(ModelProvider.id).where(ModelProvider.credential_ref == reference))
        ).first()
        if not taken:
            return reference
    return f"env:{provider_env_name(slug)}"


async def create_provider(
    session: AsyncSession,
    preset_id: str,
    *,
    name: str = "",
    base_url: str = "",
    credential_ref: str = "",
) -> ModelProvider:
    preset = PRESETS.get(preset_id)
    if preset is None:
        raise ProviderError(f"未知供应商类型: {preset_id}")
    base_url = (base_url or preset.default_base_url).strip().rstrip("/")
    if preset.custom and not base_url:
        raise ProviderError("自定义供应商需要填写 API 地址")
    display = name.strip() or (host_of(base_url) if preset.custom else preset.name)
    # 同名供应商（如同一厂商的第二个账号）自动编号，列表里才能区分。
    names = set((await session.execute(select(ModelProvider.name))).scalars().all())
    if display in names:
        index = 2
        while f"{display} {index}" in names:
            index += 1
        display = f"{display} {index}"
    slug_base = slugify(display if preset.custom else preset.id)
    slug = await _unique_slug(session, ModelProvider, slug_base)
    provider = ModelProvider(
        slug=slug,
        name=display,
        preset=preset.id,
        base_url=base_url,
        credential_ref=credential_ref or await _credential_ref_for(session, preset, slug),
        enabled=True,
        sort_order=list(PRESETS).index(preset.id) * 10,
    )
    session.add(provider)
    await session.flush()
    return provider


async def assign_unowned_deployments(session: AsyncSession) -> int:
    """把没有供应商的模型按 地址 + 密钥引用 + 预设 归入供应商；幂等，启动时调用。"""
    rows = (
        (await session.execute(select(ModelDeployment).where(ModelDeployment.provider_id.is_(None))))
        .scalars()
        .all()
    )
    if not rows:
        return 0
    providers = await list_providers(session)
    assigned = 0
    for row in rows:
        preset = preset_for(row.driver, row.base_url)
        host = host_of(row.base_url)
        provider = next(
            (
                item
                for item in providers
                if item.preset == preset.id
                and item.credential_ref == row.credential_ref
                and host_of(item.base_url) == host
            ),
            None,
        )
        if provider is None:
            provider = await create_provider(
                session, preset.id, base_url=row.base_url, credential_ref=row.credential_ref
            )
            providers.append(provider)
        row.provider_id = provider.id
        assigned += 1
    await session.flush()
    return assigned


async def update_provider(
    session: AsyncSession,
    provider: ModelProvider,
    *,
    name: Optional[str] = None,
    base_url: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> ModelProvider:
    if name is not None and name.strip():
        provider.name = name.strip()
    if enabled is not None:
        provider.enabled = enabled
    if base_url is not None:
        normalized = base_url.strip().rstrip("/")
        if normalized != provider.base_url:
            # 先逐个校验并写入模型，任何一个地址不合法都整体失败。
            for model in await models_of(session, provider):
                await model_deployment_service.update_deployment(
                    session, model, DeploymentPatch(base_url=normalized)
                )
            provider.base_url = normalized
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
        raise ProviderError(f"该供应商的模型仍被默认模型引用：{'、'.join(routes)}，请先改绑")
    for model in models:
        await session.delete(model)
    await session.flush()
    await session.delete(provider)
    await session.flush()


async def add_model(
    session: AsyncSession,
    provider: ModelProvider,
    *,
    driver: str,
    model: str,
    name: str = "",
    config: Optional[dict[str, Any]] = None,
    capabilities: Optional[dict[str, Any]] = None,
    pricing: Optional[dict[str, Any]] = None,
    enabled: bool = True,
) -> ModelDeployment:
    preset = preset_of(provider)
    if driver not in preset.drivers:
        raise ProviderError(f"{provider.name} 不支持接入类型 {driver}")
    spec = DRIVER_SPECS[driver]
    model = model.strip()
    if not model:
        raise ProviderError("模型 ID 不能为空")
    slug = await _unique_slug(session, ModelDeployment, slugify(f"{provider.slug}-{model}"))
    payload = DeploymentInput(
        name=name.strip() or model,
        slug=slug,
        driver=driver,
        modality=next(iter(spec.modalities)),
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
            return "密钥无效或没有权限"
        if status == 404:
            return "地址不正确（接口不存在）"
        return f"服务返回 {status}"
    if isinstance(error, httpx.TimeoutException):
        return "连接超时"
    return "无法连接到该地址"


async def check_provider(provider: ModelProvider, models: list[ModelDeployment]) -> dict[str, Any]:
    """检查地址与密钥是否可用；不创建任何生成任务、不产生费用。"""
    preset = preset_of(provider)
    key = credential_value(provider.credential_ref)
    if not key:
        return {"ok": False, "message": "还没有填写 API 密钥"}
    headers = {"Authorization": f"Bearer {key}"}
    base = provider.base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
            if preset.check == "openai_models":
                response = await client.get(f"{base}/models", headers=headers)
            elif preset.check == "ark":
                response = await client.get(
                    f"{base}/contents/generations/tasks",
                    headers=headers,
                    params={"page_num": 1, "page_size": 1},
                )
            elif preset.check == "h3_health":
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
        return {"ok": False, "message": "该供应商需要先添加并启用一个模型才能检查"}
    try:
        await DEFAULT_DRIVER_REGISTRY.probe(item.driver, item.instance)
    except Exception as error:  # noqa: BLE001 - 各 Driver 的探测异常类型不同
        return {"ok": False, "message": str(error)[:200] or "检查失败"}
    return {"ok": True, "message": "连接正常"}


async def remote_models(provider: ModelProvider, existing: list[ModelDeployment]) -> list[dict[str, Any]]:
    """GET {base_url}/models，返回可添加的模型及推断的接入类型。"""
    preset = preset_of(provider)
    if not preset.lists_models:
        raise ProviderError(f"{provider.name} 不提供模型列表接口，请手动添加模型")
    key = credential_value(provider.credential_ref)
    if not key:
        raise ProviderError("请先填写 API 密钥")
    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{provider.base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {key}"}
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as error:
        hint = "；方舟的模型 ID 可在控制台「在线推理」中查看后手动添加" if preset.id == "volcano-ark" else ""
        message = _http_message(error) if isinstance(error, httpx.HTTPError) else "返回格式无法识别"
        raise ProviderError(f"拉取模型列表失败：{message}{hint}") from error
    added = {(row.driver, row.model) for row in existing}
    items: list[dict[str, Any]] = []
    for entry in data.get("data") or []:
        model_id = str((entry or {}).get("id") or "").strip()
        kind = infer_model_kind(preset, model_id) if model_id else None
        if kind is None:
            continue
        driver, modality = kind
        items.append(
            {"model": model_id, "driver": driver, "modality": modality, "added": (driver, model_id) in added}
        )
    return sorted(items, key=lambda item: (item["modality"], item["model"]))


def provider_dto(
    provider: ModelProvider, models: list[ModelDeployment], availability: dict[str, bool]
) -> dict[str, Any]:
    preset = preset_of(provider)
    return {
        "id": provider.id,
        "slug": provider.slug,
        "name": provider.name,
        "preset": preset.summary(),
        "base_url": provider.base_url,
        "credential_configured": bool(credential_value(provider.credential_ref)),
        "enabled": provider.enabled,
        "revision": provider.revision,
        "models": [
            {
                **model_deployment_service.to_dto(model).model_dump(mode="json"),
                "available": availability.get(model.slug),
            }
            for model in models
        ],
    }
