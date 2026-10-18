"""统一 Deployment Catalog 与 validate/build/probe/atomic-swap 热加载。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelDeployment, ModelProvider, ModelRoute
from app.model_runtime.deployment_config import DeploymentInput
from app.model_runtime.registry import DEFAULT_DRIVER_REGISTRY, DriverRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CatalogDeployment:
    id: str
    slug: str
    name: str
    driver: str
    modality: str
    model: str
    base_url: str
    enabled: bool
    priority: int
    capabilities: Mapping[str, Any]
    pricing: Mapping[str, Any]
    revision: int
    config_hash: str
    instance: Any


@dataclass(frozen=True)
class CatalogSnapshot:
    deployments: Mapping[str, CatalogDeployment]
    catalog_revision: str
    loaded_at: datetime
    # 已启用的用途路由：(purpose, tier) → 有序候选 slug（主 + 备用），供同步调用方（对话模型）读取
    routes: Mapping[tuple[str, str], tuple[str, ...]] = MappingProxyType({})

    @classmethod
    def empty(cls) -> "CatalogSnapshot":
        return cls(MappingProxyType({}), "empty", datetime.now(timezone.utc))

    def list(self, modality: str | None = None) -> tuple[CatalogDeployment, ...]:
        rows = self.deployments.values()
        return tuple(row for row in rows if modality is None or row.modality == modality)

    def get(self, slug: str) -> CatalogDeployment | None:
        return self.deployments.get(slug)

    def route_candidates(self, purpose: str, tier: str = "final") -> tuple[str, ...]:
        """某用途档位的候选；该档位未配置时回退 final 档。"""
        return self.routes.get((purpose, tier)) or self.routes.get((purpose, "final")) or ()


@dataclass(frozen=True)
class ReloadFailure:
    reason: str
    attempted_revision: str
    failed_at: datetime


def _row_payload(row: ModelDeployment) -> DeploymentInput:
    return DeploymentInput.model_validate({key: getattr(row, key) for key in DeploymentInput.model_fields})


def _revision(
    rows: Iterable[ModelDeployment],
    disabled_providers: frozenset[str] = frozenset(),
    routes: Mapping[tuple[str, str], tuple[str, ...]] | None = None,
) -> str:
    values = sorted((row.slug, row.revision, row.config_hash) for row in rows)
    route_values = sorted([list(key), list(value)] for key, value in (routes or {}).items())
    raw = json.dumps(
        [values, sorted(disabled_providers), route_values], separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(raw.encode()).hexdigest()


class CatalogManager:
    def __init__(self, registry: DriverRegistry = DEFAULT_DRIVER_REGISTRY) -> None:
        self.registry = registry
        self._snapshot = CatalogSnapshot.empty()
        self._reload_lock = asyncio.Lock()
        self._last_failure: ReloadFailure | None = None

    @property
    def snapshot(self) -> CatalogSnapshot:
        return self._snapshot

    @property
    def last_failure(self) -> ReloadFailure | None:
        return self._last_failure

    async def reload_rows(
        self,
        rows: Iterable[ModelDeployment],
        *,
        probe: bool = False,
        force: bool = False,
        disabled_providers: frozenset[str] = frozenset(),
        routes: Mapping[tuple[str, str], tuple[str, ...]] | None = None,
    ) -> CatalogSnapshot:
        """disabled_providers：已停用供应商的 id，其下模型一律视为停用；routes：已启用的用途路由。"""
        rows = list(rows)
        routes = dict(routes or {})
        attempted_revision = _revision(rows, disabled_providers, routes)
        async with self._reload_lock:
            if not force and attempted_revision == self._snapshot.catalog_revision:
                return self._snapshot
            try:
                built: dict[str, CatalogDeployment] = {}
                for row in rows:
                    payload = _row_payload(row)
                    if getattr(row, "provider_id", None) in disabled_providers:
                        payload = payload.model_copy(update={"enabled": False})
                    instance = self.registry.build(payload)
                    if probe and payload.enabled:
                        await self.registry.probe(payload, instance)
                    built[payload.slug] = CatalogDeployment(
                        id=row.id,
                        slug=payload.slug,
                        name=payload.name,
                        driver=payload.driver,
                        modality=payload.modality,
                        model=payload.model,
                        base_url=payload.base_url,
                        enabled=payload.enabled,
                        priority=payload.priority,
                        capabilities=MappingProxyType(dict(payload.capabilities)),
                        pricing=MappingProxyType(dict(payload.pricing)),
                        revision=row.revision,
                        config_hash=row.config_hash,
                        instance=instance,
                    )
                next_snapshot = CatalogSnapshot(
                    MappingProxyType(built),
                    attempted_revision,
                    datetime.now(timezone.utc),
                    MappingProxyType(routes),
                )
            except Exception as error:
                self._last_failure = ReloadFailure(str(error), attempted_revision, datetime.now(timezone.utc))
                logger.exception("model catalog reload failed; keeping previous snapshot")
                raise
            # 唯一写点：完整 Snapshot 构造成功后才替换引用。
            self._snapshot = next_snapshot
            self._last_failure = None
            return next_snapshot

    async def refresh(
        self, session: AsyncSession, *, probe: bool = False, force: bool = False
    ) -> CatalogSnapshot:
        result = await session.execute(
            select(ModelDeployment).order_by(
                ModelDeployment.modality, ModelDeployment.priority, ModelDeployment.slug
            )
        )
        disabled = frozenset(
            (await session.execute(select(ModelProvider.id).where(ModelProvider.enabled.is_(False))))
            .scalars()
            .all()
        )
        route_rows = (
            (await session.execute(select(ModelRoute).where(ModelRoute.enabled.is_(True)))).scalars().all()
        )
        routes = {
            (row.purpose, row.tier): (row.primary_deployment, *list(row.fallback_deployments or []))
            for row in route_rows
        }
        return await self.reload_rows(
            result.scalars().all(), probe=probe, force=force, disabled_providers=disabled, routes=routes
        )


CATALOG = CatalogManager()


def get_catalog() -> CatalogManager:
    """API 与 Worker 共用同一构造及原子刷新逻辑。"""
    return CATALOG
