"""Alembic 调用封装：按实际 schema 识别老库 revision，再 bootstrap + upgrade。

- 全新库：alembic upgrade head 创建所有表
- 已有 alembic_version：alembic upgrade head 跑增量
- 老库（有业务表但没 alembic_version）：按特征表检测实际 revision，
  先 stamp 到匹配 revision 再 upgrade head，绝不盲目 stamp head
  （否则老库会被标成最新版却缺 0002/0003 的表，见 review P0-2）
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

# 各 revision 的特征表：该 revision 引入的表（用于无 alembic_version 时识别老库）
_REV1_TABLES = {"users", "canvases", "settings_kv", "audit_logs"}
# 0002 引入 18 张表，取几张代表性的即可判定
_REV2_MARKERS = {"series_projects", "workflow_runs", "generation_jobs", "artifacts", "usage_events"}


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


def _detect_schema_revision(inspector) -> str | None:
    """按特征表/列判断无 alembic_version 老库实际处于哪个 revision。

    返回值：
    - "0005"：generation_jobs 已有 logical_job_key（0005 的改动）
    - "0004"：generation_jobs 已有 workflow_run_id（0004 的改动）
    - "0003"：series 表齐且 generation_jobs.idempotency_key 已是 255（0003 的改动）
    - "0002"：series 表齐但 idempotency_key 仍是 128
    - "0001"：只有 0001 四张基础表，缺 series_projects
    - None：无法识别（视为全新库）
    """
    tables = set(inspector.get_table_names())

    if "series_projects" in tables and _REV2_MARKERS <= tables:
        # 区分 0002 / 0003 / 0004 / 0005：
        #   0003 把 generation_jobs.idempotency_key 放宽到 255
        #   0004 增加 generation_jobs.workflow_run_id / workflow_step_id
        #   0005 增加 generation_jobs.logical_job_key 等重试代际列
        try:
            cols = {c["name"]: c for c in inspector.get_columns("generation_jobs")}
            length = getattr(cols["idempotency_key"]["type"], "length", None)
        except Exception:
            cols, length = {}, None
        if "logical_job_key" in cols:
            return "0005"
        if "workflow_run_id" in cols:
            return "0004"
        return "0003" if length == 255 else "0002"

    if _REV1_TABLES <= tables:
        return "0001"

    return None


async def _detect_revision() -> str | None:
    async with engine.connect() as conn:
        return await conn.run_sync(lambda sync_conn: _detect_schema_revision(inspect(sync_conn)))


async def bootstrap_and_upgrade() -> None:
    """根据当前 DB 状态选择 upgrade 或 stamp + upgrade。"""
    cfg = _get_alembic_config()

    if await _has_table("alembic_version"):
        logger.info("📦 alembic_version 已存在，运行 upgrade head ...")
        await asyncio.to_thread(command.upgrade, cfg, "head")
        logger.info("✅ alembic upgrade head 完成")
        return

    detected = await _detect_revision()
    if detected is None:
        logger.info("📦 全新数据库，运行 upgrade head ...")
        await asyncio.to_thread(command.upgrade, cfg, "head")
        logger.info("✅ alembic upgrade head 完成（建表）")
        return

    # 老库：业务表已存在（可能来自更早的 create_all 或外部 dump），
    # stamp 到检测出的实际 revision 后再 upgrade head 补齐后续表。
    logger.info(f"📦 检测到无 alembic_version 老库（实际 schema ≈ {detected}），stamp {detected} 后 upgrade head ...")
    await asyncio.to_thread(command.stamp, cfg, detected)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    logger.info("✅ alembic stamp + upgrade head 完成")


async def current_revision() -> str:
    """返回当前 DB 的 alembic revision（用于诊断）。"""
    if not await _has_table("alembic_version"):
        return "<no alembic_version table>"
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        row = result.first()
        return row[0] if row else "<empty>"
