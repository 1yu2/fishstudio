"""画布历史持久化（Postgres 后端）。

外部接口由原同步函数改为 async；调用方（routers/chat.py）需 await。
"""

import logging
from typing import Any

from sqlalchemy import desc, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Canvas

logger = logging.getLogger(__name__)


def _row_to_dict(row: Canvas) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "createdAt": row.created_at_ts,
        "images": row.images or [],
        "data": row.data,
        "messages": row.messages or [],
    }


class HistoryService:
    async def get_canvases(
        self, session: AsyncSession, owner_id: int, *, is_admin: bool = False
    ) -> list[dict[str, Any]]:
        # 按更新时间倒序，与原"新的在前"一致
        stmt = select(Canvas).order_by(desc(Canvas.updated_at))
        stmt = stmt.where(
            or_(Canvas.owner_id == owner_id, Canvas.owner_id.is_(None))
            if is_admin
            else Canvas.owner_id == owner_id
        )
        result = await session.execute(stmt)
        return [_row_to_dict(row) for row in result.scalars().all()]

    async def save_canvas(
        self, session: AsyncSession, canvas_data: dict[str, Any], owner_id: int, *, is_admin: bool = False
    ) -> dict[str, Any]:
        if owner_id is None:
            raise ValueError("canvas owner is required")
        cid = canvas_data.get("id")
        if not cid:
            raise ValueError("canvas id is required")
        # 与聊天预留共用原子插入，冲突时等待对方事务，再锁定当前记录。
        existing = await self._bind_canvas(session, str(cid), owner_id)
        # 无主历史数据仅管理员可认领；有主数据仅 owner 可改。
        if existing.owner_id != owner_id and not (existing.owner_id is None and is_admin):
            raise PermissionError("canvas belongs to another user")
        existing.name = str(canvas_data.get("name", existing.name))
        if "createdAt" in canvas_data:
            existing.created_at_ts = float(canvas_data.get("createdAt") or 0.0)
        if "images" in canvas_data:
            existing.images = canvas_data.get("images") or []
        if "data" in canvas_data:
            existing.data = canvas_data.get("data")
        if "messages" in canvas_data:
            existing.messages = canvas_data.get("messages") or []
        if owner_id is not None and existing.owner_id is None:
            existing.owner_id = owner_id
        row = existing
        await session.commit()
        await session.refresh(row)
        return _row_to_dict(row)

    async def _bind_canvas(self, session: AsyncSession, canvas_id: str, owner_id: int) -> Canvas:
        if owner_id is None:
            raise ValueError("canvas owner is required")
        await session.execute(
            insert(Canvas)
            .values(id=canvas_id, owner_id=owner_id)
            .on_conflict_do_nothing(index_elements=[Canvas.id])
        )
        return await session.scalar(
            select(Canvas)
            .where(Canvas.id == canvas_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def can_access_canvas(
        self, session: AsyncSession, canvas_id: str, owner_id: int, *, is_admin: bool = False
    ) -> bool:
        # 聊天开始前先持久化归属；并发聊天或保存只能有一个 owner 获胜。
        row = await self._bind_canvas(session, canvas_id, owner_id)
        allowed = row.owner_id == owner_id or (row.owner_id is None and is_admin)
        if allowed:
            row.owner_id = owner_id
            await session.commit()
        else:
            await session.rollback()
        return allowed

    async def get_canvas_owner(self, session: AsyncSession, canvas_id: str) -> int | None:
        """返回画布的 owner_id；画布不存在返回 None（与"无主"无法区分，调用方按场景处理）。"""
        row = await session.get(Canvas, canvas_id)
        return row.owner_id if row is not None else None

    async def delete_canvas(
        self, session: AsyncSession, canvas_id: str, owner_id: int, *, is_admin: bool = False
    ) -> bool:
        row = await session.get(Canvas, canvas_id)
        if row is None:
            return False
        if row.owner_id != owner_id and not (row.owner_id is None and is_admin):
            return False
        await session.delete(row)
        await session.commit()
        return True


history_service = HistoryService()
