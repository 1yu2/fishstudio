"""系列剧项目服务：SeriesProject / Episode 的创建、查询、状态流转。"""

import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Canvas, Episode, SeriesProject
from app.services.ownership import check_project_access

logger = logging.getLogger(__name__)


async def create_project(
    session: AsyncSession,
    user_id: Optional[int],
    *,
    canvas_id: Optional[str] = None,
    project_type: str = "short_drama",
    title: str = "",
    workflow_template_id: Optional[str] = None,
    active_skill_ids: Optional[list[Any]] = None,
    settings: Optional[dict[str, Any]] = None,
    episode_count: int = 1,
    target_duration_ms: int = 90000,
) -> SeriesProject:
    """创建项目 + 默认第 1 集。

    canvas_id 非空时校验归属（review P0-6）：
    - canvas 不存在 → ValueError（路由映射 404）；
    - canvas 已被其他 project 绑定 → ValueError（400）；
    - canvas owner_id 为空（历史无主）→ PermissionError，需先认领再绑定；
    - canvas 属于其他用户 → PermissionError（403）。
    """
    if canvas_id:
        canvas = await session.get(Canvas, canvas_id)
        if canvas is None:
            raise ValueError(f"canvas {canvas_id} 不存在")
        if canvas.owner_id is None:
            # 历史无主 canvas：不做运行时自动认领，提示先走认领/迁移流程
            raise PermissionError(f"canvas {canvas_id} 为历史无主画布，请先认领后再绑定项目")
        if user_id is not None and canvas.owner_id != user_id:
            raise PermissionError("canvas belongs to another user")
        stmt = select(SeriesProject).where(SeriesProject.canvas_id == canvas_id)
        existing = (await session.execute(stmt)).scalars().first()
        if existing is not None:
            raise ValueError(f"canvas {canvas_id} 已绑定项目 {existing.id}")

    project = SeriesProject(
        canvas_id=canvas_id,
        user_id=user_id,
        project_type=project_type,
        title=title,
        workflow_template_id=workflow_template_id,
        active_skill_ids=active_skill_ids or [],
        settings=settings or {},
    )
    session.add(project)
    await session.flush()
    episode_count = max(1, min(int(episode_count), 100))
    target_duration_ms = max(1000, int(target_duration_ms))
    for episode_no in range(1, episode_count + 1):
        episode_title = title if episode_count == 1 else f"{title} 第{episode_no}集"
        session.add(
            Episode(
                project_id=project.id,
                episode_no=episode_no,
                title=episode_title,
                target_duration_ms=target_duration_ms,
            )
        )
    await session.commit()
    await session.refresh(project)
    return project


async def set_workspace_mode(
    session: AsyncSession,
    project_id: str,
    mode: str,
    user_id: Optional[int] = None,
    is_admin: bool = False,
) -> SeriesProject:
    """只切换工作台呈现方式；领域对象与 Workflow 不复制、不迁移。"""
    if mode not in {"quick", "professional"}:
        raise ValueError("workspace mode must be quick or professional")
    project = await get_project(session, project_id, user_id, is_admin=is_admin)
    if project is None:
        raise ValueError(f"project {project_id} not found")
    project.settings = {**(project.settings or {}), "workspace_mode": mode}
    await session.commit()
    await session.refresh(project)
    return project


async def get_project(
    session: AsyncSession,
    project_id: str,
    user_id: Optional[int] = None,
    is_admin: bool = False,
) -> Optional[SeriesProject]:
    """查项目；归属校验（含无主项目拒绝，见 ownership.check_project_access）；
    不存在返回 None，无权抛 PermissionError。"""
    project = await session.get(SeriesProject, project_id)
    if project is None:
        return None
    check_project_access(project, user_id=user_id, is_admin=is_admin)
    return project


async def list_projects(
    session: AsyncSession,
    user_id: Optional[int],
    project_type: Optional[str] = None,
    status: Optional[str] = None,
) -> list[SeriesProject]:
    stmt = select(SeriesProject).order_by(SeriesProject.updated_at.desc())
    if user_id is not None:
        stmt = stmt.where(SeriesProject.user_id == user_id)
    if project_type:
        stmt = stmt.where(SeriesProject.project_type == project_type)
    if status:
        stmt = stmt.where(SeriesProject.status == status)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def update_project_status(
    session: AsyncSession, project_id: str, status: str, user_id: Optional[int] = None
) -> SeriesProject:
    project = await get_project(session, project_id, user_id)
    if project is None:
        raise ValueError(f"project {project_id} not found")
    project.status = status
    await session.commit()
    await session.refresh(project)
    return project


async def archive_project(
    session: AsyncSession, project_id: str, user_id: Optional[int] = None
) -> SeriesProject:
    return await update_project_status(session, project_id, "archived", user_id)
