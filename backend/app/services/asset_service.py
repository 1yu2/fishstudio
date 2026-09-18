"""资产服务：AssetCard 版本管理、Artifact 建档、绑定关系。"""

import hashlib
import logging
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Approval,
    Artifact,
    AssetArtifactRef,
    AssetCard,
    ShotAssetRef,
)
from app.services.ownership import check_project_access

logger = logging.getLogger(__name__)

# storage 根目录（backend/storage）
STORAGE_DIR = Path(__file__).resolve().parent.parent.parent / "storage"


def artifact_url(artifact: Artifact) -> str:
    """返回给前端的受保护下载 URL（按 artifact id 鉴权，不暴露 storage_key 直链）。

    新生成产物的 job result 里同时保留 storage_key（内部/兼容用）和 artifact_id，
    前端应逐步改用该 URL 访问产物文件。
    """
    return f"/api/artifacts/{artifact.id}/download"


async def create_asset_card(
    session: AsyncSession,
    project_id: Optional[str],
    user_id: Optional[int],
    *,
    type: str,
    name: str,
    description: str = "",
    provider_meta: Optional[dict[str, Any]] = None,
    tags: Optional[list[Any]] = None,
    scope: str = "project",
    source: str = "",
    profile_schema_version: int = 1,
    profile: Optional[dict[str, Any]] = None,
) -> AssetCard:
    """创建资产卡首版（version=1, status=draft）。

    P2-3：scope=personal 时允许 project_id=None（个人资产库独立于项目），
    但 user_id 必填（归属与鉴权依据）；scope=project 时 project_id 必填。
    """
    if scope == "personal":
        if user_id is None:
            raise ValueError("scope=personal 的资产必须指定 user_id")
    elif project_id is None:
        raise ValueError("scope=project 的资产必须指定 project_id")
    from app.schemas.series import validate_asset_profile

    card = AssetCard(
        project_id=project_id,
        user_id=user_id,
        type=type,
        name=name,
        version=1,
        description=description,
        provider_meta=provider_meta or {},
        profile_schema_version=profile_schema_version,
        profile=validate_asset_profile(type, profile or {}),
        tags=tags or [],
        scope=scope,
        source=source,
        status="draft",
    )
    session.add(card)
    await session.commit()
    await session.refresh(card)
    return card


async def copy_personal_asset_to_project(
    session: AsyncSession,
    asset_id: str,
    project_id: str,
    user_id: Optional[int],
) -> AssetCard:
    """把个人资产复制进项目（P2-3）。

    复制为新行（scope=project, project_id=目标项目, version=1, source=原资产 id）。
    锁定版本语义：复制的是当前版本快照，项目内的后续版本演进（new_asset_version）
    独立于个人库，个人库里的改动不会回流到项目副本。
    """
    src = await session.get(AssetCard, asset_id)
    if src is None:
        raise ValueError(f"asset {asset_id} not found")
    if src.scope != "personal":
        raise ValueError("只有 scope=personal 的个人资产可复制到项目")
    if src.user_id is not None and src.user_id != user_id:
        raise PermissionError("只能复制自己的个人资产")
    card = AssetCard(
        project_id=project_id,
        user_id=user_id,
        type=src.type,
        name=src.name,
        version=1,
        description=src.description,
        provider_meta=dict(src.provider_meta or {}),
        profile_schema_version=src.profile_schema_version,
        profile=dict(src.profile or {}),
        tags=list(src.tags or []),
        scope="project",
        source=src.id,
        license_meta=dict(src.license_meta or {}),
        status="draft",
    )
    session.add(card)
    await session.commit()
    await session.refresh(card)
    return card


async def _supersede_asset_approvals(session: AsyncSession, asset: AssetCard) -> None:
    """同名同 type 的旧版本资产相关 Approval 全部置 superseded。"""
    stmt = (
        select(AssetCard.id)
        .where(
            AssetCard.project_id == asset.project_id,
            AssetCard.type == asset.type,
            AssetCard.name == asset.name,
        )
    )
    sibling_ids = [r[0] for r in (await session.execute(stmt)).all()]
    stmt = select(Approval).where(
        Approval.target_type == "asset",
        Approval.target_id.in_(sibling_ids),
        Approval.status != "superseded",
    )
    for row in (await session.execute(stmt)).scalars().all():
        row.status = "superseded"


async def new_asset_version(
    session: AsyncSession,
    asset_id: str,
    user_id: Optional[int],
    updates: Optional[dict[str, Any]] = None,
) -> AssetCard:
    """同名同 type 下 version+1 新行（status=draft），旧行 status=retired，旧审批 superseded。"""
    old = await session.get(AssetCard, asset_id)
    if old is None:
        raise ValueError(f"asset {asset_id} not found")

    await _supersede_asset_approvals(session, old)

    updates = updates or {}
    from app.schemas.series import validate_asset_profile

    next_profile = validate_asset_profile(old.type, updates.get("profile", old.profile or {}))
    new = AssetCard(
        project_id=old.project_id,
        user_id=old.user_id,
        type=old.type,
        name=old.name,
        version=old.version + 1,
        description=updates.get("description", old.description),
        provider_meta=updates.get("provider_meta", old.provider_meta),
        profile_schema_version=updates.get("profile_schema_version", old.profile_schema_version),
        profile=next_profile,
        tags=updates.get("tags", old.tags),
        scope=updates.get("scope", old.scope),
        source=updates.get("source", old.source),
        license_meta=updates.get("license_meta", old.license_meta),
        status="draft",
    )
    old.status = "retired"
    session.add(new)
    if old.project_id:
        from app.services import dependency_invalidation_service

        await dependency_invalidation_service.apply_invalidation(
            session,
            project_id=old.project_id,
            source_type="asset",
            source_id=old.id,
            source_revision=new.version,
            changed_fields=sorted(updates.keys()) or ["version"],
            user_id=user_id,
            reason="asset_version_created",
            change_token=f"v{new.version}",
        )
    await session.commit()
    await session.refresh(new)
    return new


async def list_assets(
    session: AsyncSession,
    project_id: Optional[str] = None,
    user_id: Optional[int] = None,
    scope: Optional[str] = None,
    type: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
) -> list[AssetCard]:
    """项目资产 + （可选）个人资产；tag/q 简单过滤。

    scope=personal 必须带 user_id（个人资产按用户隔离，不给 user_id 返回空）。
    """
    stmt = select(AssetCard).order_by(AssetCard.created_at.desc())
    if scope == "personal":
        if user_id is None:
            return []
        stmt = stmt.where(AssetCard.scope == "personal", AssetCard.user_id == user_id)
    elif scope:
        stmt = stmt.where(AssetCard.scope == scope)
        if project_id:
            stmt = stmt.where(AssetCard.project_id == project_id)
    elif project_id:
        stmt = stmt.where(AssetCard.project_id == project_id)
    if type:
        stmt = stmt.where(AssetCard.type == type)
    rows = list((await session.execute(stmt)).scalars().all())
    if tag:
        rows = [r for r in rows if tag in (r.tags or [])]
    if q:
        needle = q.lower()
        rows = [
            r for r in rows if needle in (r.name or "").lower() or needle in (r.description or "").lower()
        ]
    return rows


async def bind_artifact(
    session: AsyncSession, asset_id: str, artifact_id: str, role: str = "reference"
) -> AssetArtifactRef:
    """绑定 artifact 到资产（幂等：重复绑定直接返回已有）。"""
    stmt = select(AssetArtifactRef).where(
        AssetArtifactRef.asset_id == asset_id,
        AssetArtifactRef.artifact_id == artifact_id,
        AssetArtifactRef.role == role,
    )
    existing = (await session.execute(stmt)).scalars().first()
    if existing is not None:
        return existing
    ref = AssetArtifactRef(asset_id=asset_id, artifact_id=artifact_id, role=role)
    session.add(ref)
    await session.commit()
    await session.refresh(ref)
    return ref


async def _shot_project_id(session: AsyncSession, shot_id: str) -> Optional[str]:
    """Shot → Scene → Episode → project_id；链路缺失返回 None。"""
    from app.db.models import Episode, Scene, Shot

    shot = await session.get(Shot, shot_id)
    if shot is None:
        return None
    scene = await session.get(Scene, shot.scene_id)
    if scene is None:
        return None
    episode = await session.get(Episode, scene.episode_id)
    return episode.project_id if episode is not None else None


async def bind_asset_to_shot(
    session: AsyncSession, shot_id: str, asset_id: str, role: str = "character",
    *, reference_type: Optional[str] = None, artifact_id: Optional[str] = None,
    subject: str = "", weight: float = 1.0, sort_order: int = 0, usage: str = "identity",
) -> ShotAssetRef:
    """绑定资产到镜头（幂等：重复绑定直接返回已有）。

    绑定前校验 shot 与 asset 属于同一项目（review P0-5）：
    shot 不存在抛 ValueError；跨项目抛 PermissionError。
    注：scope=personal 的个人资产当前 project_id 同样非空，保持同项目校验；
    个人资产跨项目引用放宽是后续 Wave2 的事，这里不改模型。
    """
    asset = await session.get(AssetCard, asset_id)
    if asset is None:
        raise ValueError(f"asset {asset_id} not found")
    shot_project_id = await _shot_project_id(session, shot_id)
    if shot_project_id is None:
        raise ValueError(f"shot {shot_id} not found")
    if shot_project_id != asset.project_id:
        raise PermissionError("shot and asset belong to different projects")
    reference_type = reference_type or role
    allowed_types = {"character", "scene", "style", "composition", "motion", "first_frame", "last_frame", "source_video", "audio"}
    if reference_type not in allowed_types:
        raise ValueError(f"unsupported reference_type: {reference_type}")
    if not 0 < weight <= 2:
        raise ValueError("reference weight 必须在 0~2 之间")
    if getattr(asset, "status", "approved") != "approved":
        raise ValueError("只能绑定 approved 资产版本")
    if artifact_id:
        artifact = await session.get(Artifact, artifact_id)
        if artifact is None or artifact.project_id != shot_project_id:
            raise ValueError("reference artifact 不存在或不属于当前项目")

    stmt = select(ShotAssetRef).where(
        ShotAssetRef.shot_id == shot_id,
        ShotAssetRef.asset_id == asset_id,
        ShotAssetRef.role == role,
        ShotAssetRef.reference_type == reference_type,
        ShotAssetRef.artifact_id == artifact_id,
    )
    existing = (await session.execute(stmt)).scalars().first()
    if existing is not None:
        return existing
    ref = ShotAssetRef(
        shot_id=shot_id, asset_id=asset_id, role=role, asset_version=getattr(asset, "version", 1),
        reference_type=reference_type, artifact_id=artifact_id, subject=subject,
        weight=weight, sort_order=sort_order, usage=usage,
    )
    session.add(ref)
    from app.db.models import Shot

    shot = await session.get(Shot, shot_id)
    if shot is not None:
        shot.locked_asset_versions = {
            **(getattr(shot, "locked_asset_versions", {}) or {}),
            asset.id: getattr(asset, "version", 1),
        }
    await session.commit()
    await session.refresh(ref)
    return ref


async def get_asset_for_user(
    session: AsyncSession, asset_id: str, user_id: Optional[int]
) -> Optional[AssetCard]:
    """查资产并校验归属（项目资产经 project.user_id；个人资产按 user_id）。"""
    from app.db.models import SeriesProject

    card = await session.get(AssetCard, asset_id)
    if card is None:
        return None
    if card.project_id is None:
        # 个人资产（P2-3）：仅归属用户可见
        if card.user_id is not None and card.user_id != user_id:
            return None
        return card
    project = await session.get(SeriesProject, card.project_id)
    if project is not None:
        try:
            check_project_access(project, user_id=user_id)
        except PermissionError:
            return None
    return card


async def get_personal_asset_for_user(
    session: AsyncSession, asset_id: str, user_id: Optional[int]
) -> Optional[AssetCard]:
    """查 scope=personal 的个人资产并校验归属：仅 owner 本人可见。

    注意：与 get_asset_for_user / check_project_access 不同，这里 admin **不**
    默认放行——个人资产库没有"历史无主认领"场景，保持最严隔离。
    """
    card = await session.get(AssetCard, asset_id)
    if card is None or card.scope != "personal":
        return None
    if user_id is None or card.user_id != user_id:
        return None
    return card


async def set_asset_status(session: AsyncSession, asset_id: str, status: str) -> AssetCard:
    """资产状态流转 helper（draft → preview_ready → approved → retired）。"""
    card = await session.get(AssetCard, asset_id)
    if card is None:
        raise ValueError(f"asset {asset_id} not found")
    card.status = status
    await session.commit()
    await session.refresh(card)
    return card


async def create_artifact(
    session: AsyncSession,
    *,
    project_id: Optional[str],
    job_id: Optional[str] = None,
    type: str,
    storage_key: str,
    mime_type: str = "",
    duration_ms: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    meta: Optional[dict[str, Any]] = None,
) -> Artifact:
    """建档 artifact；文件存在于 backend/storage/<storage_key> 时计算 sha256。"""
    content_hash = ""
    storage_backend = "local"
    path = STORAGE_DIR / storage_key
    if path.is_file():
        from app.services.storage_backend import storage

        storage_backend, content_hash = storage.promote_file(path, storage_key, mime_type)

    artifact = Artifact(
        project_id=project_id,
        job_id=job_id,
        type=type,
        storage_key=storage_key,
        storage_backend=storage_backend,
        content_hash=content_hash,
        mime_type=mime_type,
        duration_ms=duration_ms,
        width=width,
        height=height,
        artifact_metadata=meta or {},
    )
    session.add(artifact)
    await session.commit()
    await session.refresh(artifact)
    return artifact
