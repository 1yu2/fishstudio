"""启动时初始化：检测/建库 → 建表 → upsert admin → 一次性迁移本地 JSON 文件。

针对常见失败做友好诊断；任何子步骤失败都向上抛 RuntimeError，上层启动钩子捕获后退出。
"""
import json
import logging
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db.database import Base, SessionLocal, engine
from app.db.models import Canvas, Setting, User

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
LEGACY_HISTORY = BACKEND_DIR / "storage" / "chat_history.json"
LEGACY_SETTINGS = BACKEND_DIR / "storage" / "settings.json"


# ─────────────────────────────────────────
# 阶段 1：保证数据库存在（连 postgres 默认库 → CREATE DATABASE）
# ─────────────────────────────────────────

def _parse_pg_url(url: str) -> dict:
    """从 SQLAlchemy 风格 URL 拆出 asyncpg 直连所需参数。

    支持 postgresql+asyncpg://user:pass@host:port/dbname
    """
    parsed = urlparse(url.replace("postgresql+asyncpg", "postgresql"))
    if parsed.scheme not in ("postgresql", "postgres"):
        raise RuntimeError(
            f"DATABASE_URL 协议必须是 postgresql+asyncpg://，当前为 {parsed.scheme}"
        )
    return {
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "database": (parsed.path or "/").lstrip("/") or "postgres",
    }


async def _ensure_database_exists() -> None:
    """如果目标库不存在，连 postgres 默认库后 CREATE DATABASE。"""
    cfg = _parse_pg_url(get_settings().database_url)
    target = cfg["database"]

    # 连默认 postgres 库
    admin_cfg = {**cfg, "database": "postgres"}
    try:
        conn = await asyncpg.connect(**admin_cfg)
    except asyncpg.InvalidPasswordError:
        raise RuntimeError(
            f"❌ Postgres 密码错误：用户 {cfg['user']} 在 {cfg['host']}:{cfg['port']}。"
            f" 检查 .env 里的 DATABASE_URL。"
        ) from None
    except (OSError, asyncpg.CannotConnectNowError) as e:
        raise RuntimeError(
            f"❌ 无法连接 Postgres {cfg['host']}:{cfg['port']}：{e}。"
            f" 检查网络 / 防火墙 / pg_hba.conf。"
        ) from None
    except Exception as e:
        raise RuntimeError(
            f"❌ Postgres 连接异常 ({type(e).__name__}): {e}"
        ) from None

    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname=$1", target
        )
        if not exists:
            logger.info(f"📦 数据库 {target} 不存在，正在创建...")
            # asyncpg 不允许在事务内执行 CREATE DATABASE，普通 execute 即可
            await conn.execute(f'CREATE DATABASE "{target}"')
            logger.info(f"✅ 数据库 {target} 创建成功")
        else:
            logger.info(f"✅ 数据库 {target} 已存在")
    finally:
        await conn.close()


# ─────────────────────────────────────────
# 阶段 2：建表 + upsert admin + 数据迁移
# ─────────────────────────────────────────

async def init_db() -> None:
    settings = get_settings()

    # 0) 保证库存在
    await _ensure_database_exists()

    # 1) 建表
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ DB schema ensured")
    except SQLAlchemyError as e:
        raise RuntimeError(f"❌ 建表失败: {e}") from None

    # 延迟导入避免循环
    from app.auth.security import hash_password

    async with SessionLocal() as session:
        # 2) upsert admin
        result = await session.execute(
            select(User).where(User.username == settings.admin_username)
        )
        admin = result.scalar_one_or_none()
        if admin is None:
            admin = User(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password),
                is_admin=True,
            )
            session.add(admin)
            await session.commit()
            await session.refresh(admin)
            logger.info(f"👤 Admin user created: {settings.admin_username}")
        else:
            admin.password_hash = hash_password(settings.admin_password)
            admin.is_admin = True
            await session.commit()
            logger.info(f"👤 Admin user updated: {settings.admin_username}")

        # 3) 迁移 chat_history.json → canvases
        if LEGACY_HISTORY.exists():
            existing = (await session.execute(select(Canvas).limit(1))).scalar_one_or_none()
            if existing is None:
                _migrate_history(session, admin.id)
                await session.commit()
                LEGACY_HISTORY.rename(LEGACY_HISTORY.with_suffix(".json.migrated.bak"))
                logger.info("📦 chat_history.json migrated → canvases")
            else:
                logger.info("ℹ️  canvases 表已有数据，跳过 chat_history.json 迁移")

        # 4) 迁移 settings.json → settings_kv
        if LEGACY_SETTINGS.exists():
            existing = (await session.execute(select(Setting).limit(1))).scalar_one_or_none()
            if existing is None:
                _migrate_settings(session)
                await session.commit()
                LEGACY_SETTINGS.rename(LEGACY_SETTINGS.with_suffix(".json.migrated.bak"))
                logger.info("📦 settings.json migrated → settings_kv")
            else:
                logger.info("ℹ️  settings_kv 表已有数据，跳过 settings.json 迁移")


def _migrate_history(session, admin_id: int) -> None:
    try:
        data = json.loads(LEGACY_HISTORY.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"chat_history.json 解析失败，跳过迁移: {e}")
        return
    if not isinstance(data, list):
        return
    for item in data:
        if not isinstance(item, dict) or "id" not in item:
            continue
        session.add(
            Canvas(
                id=str(item["id"]),
                name=str(item.get("name", "")),
                created_at_ts=float(item.get("createdAt") or 0.0),
                images=item.get("images") or [],
                data=item.get("data"),
                messages=item.get("messages") or [],
                owner_id=admin_id,
            )
        )


def _migrate_settings(session) -> None:
    try:
        data = json.loads(LEGACY_SETTINGS.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"settings.json 解析失败，跳过迁移: {e}")
        return
    if not isinstance(data, dict):
        return
    for k, v in data.items():
        session.add(Setting(key=k, value=v))


# ─────────────────────────────────────────
# 阶段 3：健康检查（供 /health 调用）
# ─────────────────────────────────────────

async def db_healthcheck() -> dict:
    """真实查询 DB，返回 ok/error 与延迟。"""
    import time
    from sqlalchemy import text

    t0 = time.perf_counter()
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return {"ok": True, "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
