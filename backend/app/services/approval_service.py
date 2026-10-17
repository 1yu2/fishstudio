"""审批服务：approve/reject、审批失效（superseded）、生成 Gate 校验。"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Approval,
    AssetCard,
    DirectorPlanRevision,
    Episode,
    ScriptRevision,
    SeriesBibleRevision,
    StoryboardRevision,
    StyleBibleRevision,
    WorkflowRun,
)

logger = logging.getLogger(__name__)


async def _supersede_target_approvals(session: AsyncSession, target_type: str, target_id: str) -> None:
    """同 target 的非 superseded 旧审批全部置 superseded。"""
    stmt = select(Approval).where(
        Approval.target_type == target_type,
        Approval.target_id == target_id,
        Approval.status != "superseded",
    )
    for row in (await session.execute(stmt)).scalars().all():
        row.status = "superseded"


async def _validate_target(
    session: AsyncSession,
    target_type: str,
    target_id: str,
    target_revision: int,
    project_id: str,
) -> Any:
    """校验目标存在、版本匹配且属于传入项目；不存在/版本不符抛 ValueError，跨项目抛 PermissionError。"""
    if target_type == "storyboard":
        # storyboard 的 target_id 为 episode_id，target_revision 为 revision 号
        stmt = select(StoryboardRevision).where(
            StoryboardRevision.episode_id == target_id,
            StoryboardRevision.revision == target_revision,
        )
        row = (await session.execute(stmt)).scalars().first()
        if row is None:
            raise ValueError(f"storyboard revision {target_revision} of episode {target_id} not found")
        episode = await session.get(Episode, row.episode_id)
        if episode is None or episode.project_id != project_id:
            raise PermissionError("target belongs to another project")
        return row
    if target_type == "script":
        stmt = select(ScriptRevision).where(
            ScriptRevision.episode_id == target_id,
            ScriptRevision.revision == target_revision,
        )
        row = (await session.execute(stmt)).scalars().first()
        if row is None:
            raise ValueError(f"script revision {target_revision} of episode {target_id} not found")
        episode = await session.get(Episode, row.episode_id)
        if episode is None or episode.project_id != project_id:
            raise PermissionError("target belongs to another project")
        return row
    if target_type == "director_plan":
        row = (
            (
                await session.execute(
                    select(DirectorPlanRevision).where(
                        DirectorPlanRevision.episode_id == target_id,
                        DirectorPlanRevision.revision == target_revision,
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            raise ValueError(f"director plan revision {target_revision} not found")
        episode = await session.get(Episode, row.episode_id)
        if episode is None or episode.project_id != project_id:
            raise PermissionError("target belongs to another project")
        return row
    if target_type in {"series_bible", "style_bible"}:
        model = SeriesBibleRevision if target_type == "series_bible" else StyleBibleRevision
        row = (
            (
                await session.execute(
                    select(model).where(
                        model.project_id == target_id,
                        model.revision == target_revision,
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            raise ValueError(f"{target_type} revision {target_revision} of project {target_id} not found")
        if row.project_id != project_id:
            raise PermissionError("target belongs to another project")
        return row
    if target_type == "asset":
        row = await session.get(AssetCard, target_id)
        if row is None or row.version != target_revision:
            raise ValueError(f"asset {target_id} version {target_revision} not found")
        if row.project_id != project_id:
            raise PermissionError("target belongs to another project")
        return row
    raise ValueError(f"unknown target_type: {target_type}")


async def approve(
    session: AsyncSession,
    *,
    project_id: str,
    target_type: str,
    target_id: str,
    target_revision: int,
    approved_by: Optional[int],
    comment: str = "",
) -> Approval:
    """审批通过：旧审批 superseded，新 Approval(approved)，目标状态置 approved。"""
    target = await _validate_target(session, target_type, target_id, target_revision, project_id)
    await _supersede_target_approvals(session, target_type, target_id)

    row = Approval(
        project_id=project_id,
        target_type=target_type,
        target_id=target_id,
        target_revision=target_revision,
        status="approved",
        approved_by=approved_by,
        approved_at=datetime.now(timezone.utc),
        comment=comment,
    )
    session.add(row)

    target.status = "approved"
    if target_type == "script":
        episode = await session.get(Episode, target.episode_id)
        if episode is not None:
            episode.status = "storyboarding"
    if target_type == "director_plan":
        episode = await session.get(Episode, target.episode_id)
        if episode is not None:
            episode.status = "storyboarding"
    if target_type in {"series_bible", "style_bible"}:
        model = SeriesBibleRevision if target_type == "series_bible" else StyleBibleRevision
        id_field = "series_bible_revision_id" if target_type == "series_bible" else "style_bible_revision_id"
        # 新批准版本成为项目真相源；旧批准版本与基于旧版的创作结果保留但失效。
        old_rows = (
            (
                await session.execute(
                    select(model).where(
                        model.project_id == project_id,
                        model.id != target.id,
                        model.status == "approved",
                    )
                )
            )
            .scalars()
            .all()
        )
        for old in old_rows:
            old.status = "superseded"
        episodes = (
            (await session.execute(select(Episode).where(Episode.project_id == project_id))).scalars().all()
        )
        episode_ids = [episode.id for episode in episodes]
        for episode in episodes:
            setattr(episode, id_field, target.id)
            if episode.status not in {"draft", "planning"}:
                episode.status = "scripting"
        from app.services import dependency_invalidation_service

        await dependency_invalidation_service.apply_invalidation(
            session,
            project_id=project_id,
            source_type=target_type,
            source_id=project_id,
            source_revision=target.revision,
            changed_fields=["structured_payload"],
            user_id=approved_by,
            reason=f"{target_type}_approved",
            change_token=target.id,
        )

    await session.commit()
    await session.refresh(row)
    return row


async def reject(
    session: AsyncSession,
    *,
    project_id: str,
    target_type: str,
    target_id: str,
    target_revision: int,
    approved_by: Optional[int],
    comment: str = "",
) -> Approval:
    """审批拒绝：旧审批 superseded，新 Approval(rejected)。目标行状态不回写。"""
    await _validate_target(session, target_type, target_id, target_revision, project_id)
    await _supersede_target_approvals(session, target_type, target_id)

    row = Approval(
        project_id=project_id,
        target_type=target_type,
        target_id=target_id,
        target_revision=target_revision,
        status="rejected",
        approved_by=approved_by,
        approved_at=datetime.now(timezone.utc),
        comment=comment,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def is_approved(session: AsyncSession, target_type: str, target_id: str, target_revision: int) -> bool:
    stmt = select(Approval).where(
        Approval.target_type == target_type,
        Approval.target_id == target_id,
        Approval.target_revision == target_revision,
        Approval.status == "approved",
    )
    return (await session.execute(stmt)).scalars().first() is not None


def collect_character_asset_ids(payload: dict[str, Any], episode_no: Optional[int] = None) -> list[str]:
    """从 revision payload 收集去重后的 character_asset_ids（纯函数，便于单测）。"""
    seen: list[str] = []
    for ep in payload.get("episodes", []) or []:
        if episode_no is not None and ep.get("number") != episode_no:
            continue
        for scene in ep.get("scenes", []) or []:
            for shot in scene.get("shots", []) or []:
                for aid in shot.get("character_asset_ids", []) or []:
                    if aid and aid not in seen:
                        seen.append(aid)
    return seen


async def check_generation_gate(
    session: AsyncSession, project_id: str, episode_id: str
) -> tuple[bool, list[str]]:
    """收费 Gate：当前 revision 已审批 且 所有引用角色资产当前版本已审批。

    返回 (ok, reasons)。
    """
    reasons: list[str] = []

    requires_bibles = bool(
        await session.scalar(
            select(WorkflowRun.id)
            .where(
                WorkflowRun.project_id == project_id,
                WorkflowRun.template_version >= 3,
            )
            .limit(1)
        )
    )
    if requires_bibles:
        series_bible = await session.scalar(
            select(SeriesBibleRevision)
            .where(
                SeriesBibleRevision.project_id == project_id,
                SeriesBibleRevision.status == "approved",
            )
            .order_by(SeriesBibleRevision.revision.desc())
            .limit(1)
        )
        style_bible = await session.scalar(
            select(StyleBibleRevision)
            .where(
                StyleBibleRevision.project_id == project_id,
                StyleBibleRevision.status == "approved",
            )
            .order_by(StyleBibleRevision.revision.desc())
            .limit(1)
        )
        if series_bible is None:
            reasons.append("Series Bible 尚未审批")
        if style_bible is None:
            reasons.append("Style Bible 尚未审批")

    script = (
        (
            await session.execute(
                select(ScriptRevision)
                .where(ScriptRevision.episode_id == episode_id)
                .order_by(ScriptRevision.revision.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    # 兼容没有剧本版本的存量项目；一旦进入新版剧本流，就必须先通过审稿。
    if script is not None and script.status != "approved":
        reasons.append(f"剧本 revision {script.revision} 未审批（当前状态 {script.status}）")

    stmt = (
        select(StoryboardRevision)
        .where(StoryboardRevision.episode_id == episode_id)
        .order_by(StoryboardRevision.revision.desc())
        .limit(1)
    )
    revision = (await session.execute(stmt)).scalars().first()
    if revision is None:
        return False, ["该集还没有分镜 revision"]
    if revision.status != "approved":
        reasons.append(f"分镜 revision {revision.revision} 未审批（当前状态 {revision.status}）")
    if script is not None and revision.based_on_script_revision_id != script.id:
        reasons.append(f"分镜 revision {revision.revision} 未基于当前剧本 revision {script.revision}")

    asset_ids = collect_character_asset_ids(revision.payload or {})
    for aid in asset_ids:
        asset = await session.get(AssetCard, aid)
        if asset is None:
            reasons.append(f"角色资产 {aid} 不存在")
        elif asset.status != "approved":
            reasons.append(
                f"角色资产 {asset.name or aid}（v{asset.version}）未审批（当前状态 {asset.status}）"
            )

    return (len(reasons) == 0, reasons)


async def list_approvals(session: AsyncSession, project_id: str) -> list[Approval]:
    stmt = select(Approval).where(Approval.project_id == project_id).order_by(Approval.created_at.desc())
    return list((await session.execute(stmt)).scalars().all())
