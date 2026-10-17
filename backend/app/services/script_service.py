"""单集剧本版本服务。

ScriptRevision 是不可变事实：编辑等价于创建下一版。旧版本和审批只会被标记
superseded，不会被覆盖或删除，保证分镜能明确追溯到输入剧本。
"""

from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Approval,
    Episode,
    ScriptRevision,
    SeriesBibleRevision,
    SeriesProject,
    StyleBibleRevision,
)
from app.schemas.series import ScriptPayload
from app.services.ownership import check_project_access


async def _owned_episode(
    session: AsyncSession, episode_id: str, user_id: Optional[int], is_admin: bool = False
) -> tuple[Episode, SeriesProject]:
    episode = await session.get(Episode, episode_id)
    if episode is None:
        raise ValueError(f"episode {episode_id} not found")
    project = await session.get(SeriesProject, episode.project_id)
    if project is None:
        raise ValueError(f"project {episode.project_id} not found")
    check_project_access(project, user_id=user_id, is_admin=is_admin)
    return episode, project


async def _next_revision(session: AsyncSession, episode_id: str) -> int:
    current = (
        await session.execute(
            select(func.max(ScriptRevision.revision)).where(ScriptRevision.episode_id == episode_id)
        )
    ).scalar_one_or_none()
    return (current or 0) + 1


async def create_revision(
    session: AsyncSession,
    episode_id: str,
    structured_payload: dict[str, Any],
    *,
    user_id: Optional[int],
    created_by: str = "",
    source_type: str = "user",
    based_on_revision: Optional[int] = None,
    schema_version: int = 1,
    is_admin: bool = False,
) -> ScriptRevision:
    """校验结构化剧本并创建下一版，同时使旧剧本审批和下游分镜失效。"""
    episode, _ = await _owned_episode(session, episode_id, user_id, is_admin)
    payload = ScriptPayload.model_validate(structured_payload)
    if schema_version != payload.schema_version:
        raise ValueError(
            f"schema_version {schema_version} 与 structured_payload.schema_version "
            f"{payload.schema_version} 不一致"
        )

    latest = (
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
    if based_on_revision is not None and (latest is None or based_on_revision != latest.revision):
        current = latest.revision if latest else None
        raise ValueError(f"based_on_revision {based_on_revision} 不是当前版本 {current}")
    if based_on_revision is None and latest is not None:
        based_on_revision = latest.revision

    revision_no = await _next_revision(session, episode_id)
    series_bible = (
        (
            await session.execute(
                select(SeriesBibleRevision)
                .where(
                    SeriesBibleRevision.project_id == episode.project_id,
                    SeriesBibleRevision.status == "approved",
                )
                .order_by(SeriesBibleRevision.revision.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    style_bible = (
        (
            await session.execute(
                select(StyleBibleRevision)
                .where(
                    StyleBibleRevision.project_id == episode.project_id,
                    StyleBibleRevision.status == "approved",
                )
                .order_by(StyleBibleRevision.revision.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    row = ScriptRevision(
        episode_id=episode_id,
        based_on_series_bible_revision_id=series_bible.id if series_bible else None,
        based_on_style_bible_revision_id=style_bible.id if style_bible else None,
        revision=revision_no,
        schema_version=schema_version,
        structured_payload=payload.model_dump(),
        source_type=source_type,
        based_on_revision=based_on_revision,
        status="draft",
        created_by=created_by,
    )
    session.add(row)

    if latest is not None and latest.status != "superseded":
        latest.status = "superseded"

    approvals = (
        (
            await session.execute(
                select(Approval).where(
                    Approval.target_type == "script",
                    Approval.target_id == episode_id,
                    Approval.status != "superseded",
                )
            )
        )
        .scalars()
        .all()
    )
    for approval in approvals:
        approval.status = "superseded"

    from app.services import dependency_invalidation_service

    await dependency_invalidation_service.apply_invalidation(
        session,
        project_id=episode.project_id,
        source_type="script",
        source_id=episode_id,
        source_revision=revision_no,
        changed_fields=["structured_payload"],
        user_id=user_id,
        is_admin=is_admin,
        reason="script_revision_created",
        change_token=row.id,
    )

    episode.objective = payload.episode_objective
    episode.hook = payload.hook
    episode.conflict = payload.conflict
    episode.payoff = payload.payoff
    episode.cliffhanger = payload.cliffhanger
    episode.series_bible_revision_id = series_bible.id if series_bible else None
    episode.style_bible_revision_id = style_bible.id if style_bible else None
    episode.status = "script_review"

    await session.commit()
    await session.refresh(row)
    return row


async def list_revisions(
    session: AsyncSession,
    episode_id: str,
    *,
    user_id: Optional[int],
    is_admin: bool = False,
) -> list[ScriptRevision]:
    await _owned_episode(session, episode_id, user_id, is_admin)
    rows = await session.execute(
        select(ScriptRevision)
        .where(ScriptRevision.episode_id == episode_id)
        .order_by(ScriptRevision.revision.desc())
    )
    return list(rows.scalars().all())


async def get_current_revision(
    session: AsyncSession,
    episode_id: str,
    *,
    user_id: Optional[int],
    is_admin: bool = False,
) -> Optional[ScriptRevision]:
    await _owned_episode(session, episode_id, user_id, is_admin)
    return (
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
