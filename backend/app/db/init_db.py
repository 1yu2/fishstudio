"""启动时初始化：建表 + upsert admin + 一次性迁移本地 JSON 文件。"""
import json
import logging
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db.database import Base, SessionLocal, engine
from app.db.models import Canvas, Setting, User

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
LEGACY_HISTORY = BACKEND_DIR / "storage" / "chat_history.json"
LEGACY_SETTINGS = BACKEND_DIR / "storage" / "settings.json"


async def init_db() -> None:
    # 1) 建表（生产环境应改用 Alembic，本期 P0 先 create_all）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ DB schema ensured")

    settings = get_settings()

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
            # 同步密码（方便用户改 .env 后即时生效）
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

        # 4) 迁移 settings.json → settings_kv
        if LEGACY_SETTINGS.exists():
            existing = (await session.execute(select(Setting).limit(1))).scalar_one_or_none()
            if existing is None:
                _migrate_settings(session)
                await session.commit()
                LEGACY_SETTINGS.rename(LEGACY_SETTINGS.with_suffix(".json.migrated.bak"))
                logger.info("📦 settings.json migrated → settings_kv")


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
