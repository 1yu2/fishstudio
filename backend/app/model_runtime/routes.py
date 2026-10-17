"""简单用途绑定校验与不可变路由快照。"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from pydantic import BaseModel, Field, field_validator, model_validator

from app.model_runtime.catalog import CatalogDeployment, CatalogSnapshot

PURPOSES: dict[str, tuple[str, frozenset[str]]] = {
    "chat": ("chat", frozenset()),
    "planner": ("chat", frozenset({"tools"})),
    "director": ("chat", frozenset()),
    "reviewer": ("chat", frozenset({"vision"})),
    "image_generation": ("image", frozenset({"generate"})),
    "image_edit": ("image", frozenset({"edit"})),
    "shot_video": ("video", frozenset()),
    "speech": ("speech", frozenset()),
    "model_3d": ("model_3d", frozenset()),
}


class ModelRouteInput(BaseModel):
    purpose: str = Field(min_length=1, max_length=64)
    modality: str = Field(min_length=1, max_length=32)
    primary_deployment: str = Field(min_length=1, max_length=128)
    fallback_deployments: list[str] = Field(default_factory=list, max_length=16)
    enabled: bool = True

    @field_validator("purpose", "modality", "primary_deployment")
    @classmethod
    def strip_value(cls, value: str) -> str:
        return value.strip()

    @field_validator("fallback_deployments")
    @classmethod
    def normalize_fallbacks(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("备用 Deployment 不能重复")
        return normalized

    @model_validator(mode="after")
    def validate_purpose(self):
        definition = PURPOSES.get(self.purpose)
        if definition is None:
            raise ValueError(f"未知用途: {self.purpose}")
        if definition[0] != self.modality:
            raise ValueError(f"用途 {self.purpose} 的模态必须是 {definition[0]}")
        if self.primary_deployment in self.fallback_deployments:
            raise ValueError("主 Deployment 不能同时出现在备用列表")
        return self


class ModelRouteDTO(ModelRouteInput):
    revision: int


@dataclass(frozen=True)
class DeploymentRouteSnapshot:
    slug: str
    driver: str
    model: str
    deployment_revision: int
    config_hash: str
    pricing: Mapping[str, Any]


@dataclass(frozen=True)
class PurposeRoutingSnapshot:
    purpose: str
    modality: str
    route_revision: int
    catalog_revision: str
    candidates: tuple[DeploymentRouteSnapshot, ...]
    explicit: bool = False

    @property
    def selected(self) -> DeploymentRouteSnapshot:
        return self.candidates[0]


def _supports(item: CatalogDeployment, required: frozenset[str]) -> bool:
    if not required:
        return True
    capabilities = item.capabilities
    for capability in required:
        value = capabilities.get(capability)
        if value is True:
            continue
        if capability in capabilities.get("operations", []):
            continue
        return False
    return True


def validate_route(payload: ModelRouteInput, catalog: CatalogSnapshot) -> None:
    required = PURPOSES[payload.purpose][1]
    for slug in [payload.primary_deployment, *payload.fallback_deployments]:
        deployment = catalog.get(slug)
        if deployment is None:
            raise ValueError(f"Deployment 不存在: {slug}")
        if not deployment.enabled:
            raise ValueError(f"Deployment 已停用: {slug}")
        if deployment.modality != payload.modality:
            raise ValueError(f"Deployment {slug} 模态不匹配")
        if not _supports(deployment, required):
            raise ValueError(f"Deployment {slug} 缺少用途 {payload.purpose} 所需能力")


def resolve_route(
    route: Any,
    catalog: CatalogSnapshot,
    *,
    explicit_deployment: str | None = None,
) -> PurposeRoutingSnapshot:
    payload = ModelRouteInput.model_validate(
        {
            "purpose": route.purpose,
            "modality": route.modality,
            "primary_deployment": route.primary_deployment,
            "fallback_deployments": route.fallback_deployments,
            "enabled": route.enabled,
        }
    )
    if not payload.enabled:
        raise ValueError(f"用途路由已停用: {payload.purpose}")
    if explicit_deployment:
        slugs = [explicit_deployment]
        explicit_payload = payload.model_copy(
            update={
                "primary_deployment": explicit_deployment,
                "fallback_deployments": [],
            }
        )
        validate_route(explicit_payload, catalog)
    else:
        validate_route(payload, catalog)
        slugs = [payload.primary_deployment, *payload.fallback_deployments]
    candidates = tuple(
        DeploymentRouteSnapshot(
            slug=item.slug,
            driver=item.driver,
            model=item.model,
            deployment_revision=item.revision,
            config_hash=item.config_hash,
            pricing=MappingProxyType(dict(item.pricing)),
        )
        for item in (catalog.get(slug) for slug in slugs)
        if item is not None
    )
    return PurposeRoutingSnapshot(
        purpose=payload.purpose,
        modality=payload.modality,
        route_revision=route.revision,
        catalog_revision=catalog.catalog_revision,
        candidates=candidates,
        explicit=bool(explicit_deployment),
    )


def fallback_candidate(
    snapshot: PurposeRoutingSnapshot, attempt: int, *, submitted: bool = False
) -> DeploymentRouteSnapshot:
    if submitted:
        raise RuntimeError("异步任务已提交，禁止自动切换 Deployment")
    if snapshot.explicit and attempt > 0:
        raise RuntimeError("显式选择 Deployment 时禁止自动回退")
    try:
        return snapshot.candidates[attempt]
    except IndexError as error:
        raise RuntimeError("没有更多备用 Deployment") from error
