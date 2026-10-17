"""图片用途的唯一 Route Resolver 与执行入口。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import mock_providers_enabled
from app.db.models import ModelRoute
from app.model_runtime.catalog import CatalogDeployment, CatalogManager, get_catalog
from app.model_runtime.routes import PURPOSES
from app.providers.base import ProviderResult
from app.providers.image_base import ImageGenerationRequest, ImageProvider, MockImageProvider

IMAGE_PURPOSES = {"image_generation", "image_edit"}


class _LegacyPathMockImageProvider(ImageProvider):
    """兼容旧 MOCK_MODE/MOCK_IMAGE_PATH；只在没有 DB 图片配置时构造。"""

    name = "mock_image"

    def __init__(self, image_path: str) -> None:
        self.image_path = image_path.strip()
        self.model_name = "mock-image"
        self.deployment_name = "legacy-mock-image"

    def is_available(self) -> bool:
        return bool(self.image_path)

    async def generate(self, req: ImageGenerationRequest) -> ProviderResult:
        storage_key = self.image_path.removeprefix("/storage/").lstrip("/")
        return ProviderResult(
            success=True,
            provider=self.name,
            model=self.model_name,
            artifacts=[{"storage_key": storage_key, "type": "image"}],
            usage={"output_image_count": 1},
            raw_metadata={"mocked": True, "legacy_mock_mode": True},
        )


@dataclass(frozen=True)
class ResolvedImageExecution:
    purpose: str
    deployment_name: str
    provider: ImageProvider
    model: str
    mocked: bool
    routing_snapshot: dict[str, Any]
    result: ProviderResult


class ImageResolver:
    """按 DB Route 顺序解析图片 Deployment，并统一执行 fallback。"""

    def __init__(
        self,
        catalog: CatalogManager | None = None,
        *,
        mock_enabled: Callable[[], bool] = mock_providers_enabled,
    ) -> None:
        self.catalog = catalog or get_catalog()
        self.mock_enabled = mock_enabled

    @staticmethod
    def purpose_for(req: ImageGenerationRequest) -> str:
        return "image_edit" if req.source_image else "image_generation"

    @staticmethod
    def _supports(deployment: CatalogDeployment, purpose: str) -> bool:
        required = PURPOSES[purpose][1]
        capabilities = deployment.capabilities
        return all(
            capabilities.get(capability) is True or capability in capabilities.get("operations", [])
            for capability in required
        )

    async def _configured_candidates(
        self, session: AsyncSession, purpose: str
    ) -> tuple[list[CatalogDeployment], dict[str, Any]] | None:
        route = await session.get(ModelRoute, purpose)
        snapshot = self.catalog.snapshot
        image_deployments = snapshot.list("image")

        if route is None:
            # 只要 Catalog 中已经有图片 Deployment，就属于 DB 配置不完整，不能静默退回 env。
            if image_deployments:
                raise RuntimeError(f"已有图片 Deployment，但未配置用途 Route: {purpose}")
            return None
        if not route.enabled:
            raise RuntimeError(f"图片用途 Route 已停用: {purpose}")
        if route.modality != "image":
            raise RuntimeError(f"图片用途 Route 模态错误: {purpose}/{route.modality}")

        candidates: list[CatalogDeployment] = []
        skipped: list[dict[str, str]] = []
        for slug in [route.primary_deployment, *route.fallback_deployments]:
            deployment = snapshot.get(slug)
            if deployment is None:
                skipped.append({"deployment": slug, "reason": "missing"})
                continue
            if not deployment.enabled:
                skipped.append({"deployment": slug, "reason": "disabled"})
                continue
            if deployment.modality != "image":
                skipped.append({"deployment": slug, "reason": "modality_mismatch"})
                continue
            if not self._supports(deployment, purpose):
                skipped.append({"deployment": slug, "reason": "capability_mismatch"})
                continue
            if not isinstance(deployment.instance, ImageProvider):
                skipped.append({"deployment": slug, "reason": "adapter_mismatch"})
                continue
            candidates.append(deployment)

        return candidates, {
            "purpose": purpose,
            "route_revision": route.revision,
            "catalog_revision": snapshot.catalog_revision,
            "candidate_deployments": [route.primary_deployment, *route.fallback_deployments],
            "skipped": skipped,
            "legacy": False,
        }

    @staticmethod
    def _legacy_provider(purpose: str) -> ImageProvider:
        from app.providers.volcano_image import VolcanoImageProvider

        return VolcanoImageProvider.from_legacy_env(purpose)

    async def execute(
        self,
        session: AsyncSession,
        req: ImageGenerationRequest,
        *,
        purpose: str | None = None,
    ) -> ResolvedImageExecution:
        purpose = purpose or self.purpose_for(req)
        if purpose not in IMAGE_PURPOSES:
            raise ValueError(f"未知图片用途: {purpose}")
        expected = self.purpose_for(req)
        if purpose != expected:
            raise ValueError(f"请求与图片用途不匹配: expected={expected}, actual={purpose}")

        configured = await self._configured_candidates(session, purpose)
        if configured is None:
            legacy_mock = os.getenv("MOCK_MODE", "false").strip().lower() in {"1", "true", "yes"}
            provider = (
                _LegacyPathMockImageProvider(os.getenv("MOCK_IMAGE_PATH", ""))
                if legacy_mock
                else self._legacy_provider(purpose)
            )
            candidates: list[tuple[str, str, ImageProvider]] = [
                (provider.deployment_name, provider.model_name, provider)
            ]
            route_snapshot: dict[str, Any] = {
                "purpose": purpose,
                "candidate_deployments": [provider.deployment_name],
                "legacy": True,
            }
        else:
            deployments, route_snapshot = configured
            candidates = [
                (deployment.slug, deployment.model, deployment.instance) for deployment in deployments
            ]

        unavailable = list(route_snapshot.get("skipped", []))
        for deployment_name, model, provider in candidates:
            if not provider.is_available():
                unavailable.append({"deployment": deployment_name, "reason": "unavailable"})
                continue
            result = await provider.generate(req)
            if not result.success and result.error_code == "no_provider":
                unavailable.append({"deployment": deployment_name, "reason": "no_provider"})
                continue
            routing_snapshot = {
                **route_snapshot,
                "deployment_name": deployment_name,
                "selected_deployment": deployment_name,
                "model": model,
                "selected_model": model,
                "fallback_used": deployment_name != route_snapshot["candidate_deployments"][0],
                "unavailable": unavailable,
            }
            result.raw_metadata = {
                **result.raw_metadata,
                "deployment_name": deployment_name,
                "routing_snapshot": routing_snapshot,
            }
            return ResolvedImageExecution(
                purpose=purpose,
                deployment_name=deployment_name,
                provider=provider,
                model=model,
                mocked=isinstance(provider, _LegacyPathMockImageProvider),
                routing_snapshot=routing_snapshot,
                result=result,
            )

        if self.mock_enabled():
            provider = MockImageProvider()
            result = await provider.generate(req)
            routing_snapshot = {
                **route_snapshot,
                "deployment_name": "mock_image",
                "selected_deployment": "mock_image",
                "model": "mock-image",
                "selected_model": "mock-image",
                "fallback_used": True,
                "unavailable": unavailable,
            }
            result.raw_metadata = {
                **result.raw_metadata,
                "deployment_name": "mock_image",
                "routing_snapshot": routing_snapshot,
            }
            return ResolvedImageExecution(
                purpose=purpose,
                deployment_name="mock_image",
                provider=provider,
                model="mock-image",
                mocked=True,
                routing_snapshot=routing_snapshot,
                result=result,
            )

        raise RuntimeError(f"用途 {purpose} 没有可用图片 Deployment；unavailable={unavailable}")


_IMAGE_RESOLVER: ImageResolver | None = None


def get_image_resolver() -> ImageResolver:
    global _IMAGE_RESOLVER
    if _IMAGE_RESOLVER is None:
        _IMAGE_RESOLVER = ImageResolver()
    return _IMAGE_RESOLVER


async def resolve_and_generate_image(
    session: AsyncSession,
    req: ImageGenerationRequest,
    *,
    purpose: str | None = None,
) -> ResolvedImageExecution:
    """Agent Tool 与 Worker 共用的唯一图片解析/执行函数。"""
    return await get_image_resolver().execute(session, req, purpose=purpose)
