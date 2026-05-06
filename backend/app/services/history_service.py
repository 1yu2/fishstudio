"""画布历史持久化（Postgres 后端）。

外部接口由原同步函数改为 async；调用方（routers/chat.py）需 await。
"""
import logging
from typing import Any

from sqlalchemy import desc, select
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
    async def get_canvases(self, session: AsyncSession) -> list[dict[str, Any]]:
        # 按更新时间倒序，与原"新的在前"一致
        result = await session.execute(select(Canvas).order_by(desc(Canvas.updated_at)))
        return [_row_to_dict(row) for row in result.scalars().all()]

    async def save_canvas(
        self, session: AsyncSession, canvas_data: dict[str, Any], owner_id: int | None = None
    ) -> dict[str, Any]:
        cid = canvas_data.get("id")
        if not cid:
            raise ValueError("canvas id is required")
        existing = await session.get(Canvas, cid)
        if existing is None:
            row = Canvas(
                id=str(cid),
                name=str(canvas_data.get("name", "")),
                created_at_ts=float(canvas_data.get("createdAt") or 0.0),
                images=canvas_data.get("images") or [],
                data=canvas_data.get("data"),
                messages=canvas_data.get("messages") or [],
                owner_id=owner_id,
            )
            session.add(row)
        else:
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

    async def delete_canvas(self, session: AsyncSession, canvas_id: str) -> bool:
        row = await session.get(Canvas, canvas_id)
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
        return True


history_service = HistoryService()
