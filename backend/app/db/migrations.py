"""Alembic 调用封装：智能 bootstrap + upgrade。

- 全新库：alembic upgrade head 创建所有表
- 已有 alembic_version：alembic upgrade head 跑增量
- 老库（有业务表但没 alembic_version）：先 stamp 到 head，再后续 upgrade（首次升级时）
"""

import asyncio
import logging
from pathlib import Path

from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command
from app.config import get_settings
from app.db.database import engine

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


def _get_alembic_config() -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    return cfg


async def _has_table(table_name: str) -> bool:
    """检查给定表是否存在（不区分 schema）。"""
    async with engine.connect() as conn:

        def _check(sync_conn):
            return inspect(sync_conn).has_table(table_name)

        return await conn.run_sync(_check)


async def bootstrap_and_upgrade() -> None:
    """根据当前 DB 状态选择 upgrade 或 stamp。"""
    cfg = _get_alembic_config()

    has_alembic = await _has_table("alembic_version")
    has_users = await _has_table("users")

    if has_alembic:
        logger.info("📦 alembic_version 已存在，运行 upgrade head ...")
        await asyncio.to_thread(command.upgrade, cfg, "head")
        logger.info("✅ alembic upgrade head 完成")
    elif has_users:
        # 老库：业务表已存在（可能来自更早的 create_all 或外部 dump），
        # 标记当前为 head 后续才能正常 upgrade，不会重复建表
        logger.info("📦 检测到已有业务表但无 alembic_version，stamp head ...")
        await asyncio.to_thread(command.stamp, cfg, "head")
        logger.info("✅ alembic stamp head 完成")
    else:
        logger.info("📦 全新数据库，运行 upgrade head ...")
        await asyncio.to_thread(command.upgrade, cfg, "head")
        logger.info("✅ alembic upgrade head 完成（建表）")


async def current_revision() -> str:
    """返回当前 DB 的 alembic revision（用于诊断）。"""
    if not await _has_table("alembic_version"):
        return "<no alembic_version table>"
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        row = result.first()
        return row[0] if row else "<empty>"
