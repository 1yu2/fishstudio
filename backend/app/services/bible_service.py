"""Series/Style Bible 不可变版本服务。"""

from typing import Any, Literal, Optional, Type

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Approval,
    SeriesBibleRevision,
    SeriesProject,
    StyleBibleRevision,
)
from app.schemas.series import SeriesBiblePayload, StyleBiblePayload
from app.services.ownership import check_project_access

BibleKind = Literal["series", "style"]
BibleModel = Type[SeriesBibleRevision] | Type[StyleBibleRevision]


def _spec(kind: BibleKind):
    if kind == "series":
        return SeriesBibleRevision, SeriesBiblePayload, "series_bible"
    if kind == "style":
        return StyleBibleRevision, StyleBiblePayload, "style_bible"
    raise ValueError(f"unknown bible kind: {kind}")


async def _owned_project(
    session: AsyncSession, project_id: str, user_id: Optional[int], is_admin: bool = False
) -> SeriesProject:
    project = await session.get(SeriesProject, project_id)
    if project is None:
        raise ValueError(f"project {project_id} not found")
    check_project_access(project, user_id=user_id, is_admin=is_admin)
    return project


async def create_revision(
    session: AsyncSession,
    project_id: str,
    kind: BibleKind,
    structured_payload: dict[str, Any],
    *,
    user_id: Optional[int],
    created_by: str = "",
    source_type: str = "user",
    based_on_revision: Optional[int] = None,
    schema_version: int = 1,
    is_admin: bool = False,
):
    """创建下一版 Bible；草稿不会立即使已批准的制作链失效。"""
    await _owned_project(session, project_id, user_id, is_admin)
    model, payload_model, approval_type = _spec(kind)
    payload = payload_model.model_validate(structured_payload)
    if schema_version != payload.schema_version:
        raise ValueError(
            f"schema_version {schema_version} 与 structured_payload.schema_version "
            f"{payload.schema_version} 不一致"
        )
    latest = (
        await session.execute(
            select(model)
            .where(model.project_id == project_id)
            .order_by(model.revision.desc())
            .limit(1)
        )
    ).scalars().first()
    if based_on_revision is not None and (
        latest is None or latest.revision != based_on_revision
    ):
        raise ValueError(
            f"based_on_revision {based_on_revision} 不是当前版本 "
            f"{latest.revision if latest else None}"
        )
    if based_on_revision is None and latest is not None:
        based_on_revision = latest.revision
    current = await session.scalar(
        select(func.max(model.revision)).where(model.project_id == project_id)
    )
    row = model(
        project_id=project_id,
        revision=(current or 0) + 1,
        schema_version=schema_version,
        structured_payload=payload.model_dump(),
        source_type=source_type,
        based_on_revision=based_on_revision,
        status="draft",
        created_by=created_by,
    )
    session.add(row)
    if latest is not None and latest.status == "draft":
        latest.status = "superseded"
    # 只有同一草稿链上的旧审批失效；已批准 Bible 在新草稿获批前仍然有效。
    approvals = (
        await session.execute(
            select(Approval).where(
                Approval.target_type == approval_type,
                Approval.target_id == project_id,
                Approval.status == "rejected",
            )
        )
    ).scalars().all()
    for approval in approvals:
        approval.status = "superseded"
    await session.commit()
    await session.refresh(row)
    return row


async def list_revisions(
    session: AsyncSession,
    project_id: str,
    kind: BibleKind,
    *,
    user_id: Optional[int],
    is_admin: bool = False,
):
    await _owned_project(session, project_id, user_id, is_admin)
    model, _, _ = _spec(kind)
    result = await session.execute(
        select(model).where(model.project_id == project_id).order_by(model.revision.desc())
    )
    return list(result.scalars().all())


async def get_current_revision(
    session: AsyncSession,
    project_id: str,
    kind: BibleKind,
    *,
    user_id: Optional[int],
    is_admin: bool = False,
):
    await _owned_project(session, project_id, user_id, is_admin)
    model, _, _ = _spec(kind)
    return (
        await session.execute(
            select(model)
            .where(model.project_id == project_id)
            .order_by(model.revision.desc())
            .limit(1)
        )
    ).scalars().first()
