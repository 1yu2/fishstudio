"""管理命令：把"初始化 / 健康检查 / 重置 admin / 数据库迁移"从 app 启动里拆出来。

使用：
    python -m app.cli init-db                  # 自动建库 + alembic upgrade + upsert admin + 迁移旧 JSON
    python -m app.cli check                    # 跑一遍 DB 健康检查
    python -m app.cli reset-admin              # 重新写 admin 密码（按 .env 里的 ADMIN_PASSWORD）
    python -m app.cli migrate                  # 仅运行 alembic upgrade head（不动 admin / 数据迁移）
    python -m app.cli migrate-status           # 显示当前 DB revision
    python -m app.cli revision -m "<msg>"      # 自动生成新 migration 文件（diff 当前 metadata）
"""

import argparse
import asyncio
import logging
import sys

from dotenv import load_dotenv


def _setup() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


async def cmd_init_db() -> int:
    from app.db.init_db import init_db

    try:
        await init_db()
        print("✅ init-db 完成")
        return 0
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1


async def cmd_check() -> int:
    from app.db.init_db import db_healthcheck

    result = await db_healthcheck()
    if result["ok"]:
        print(f"✅ DB OK ({result['latency_ms']} ms)")
        return 0
    print(f"❌ DB FAIL: {result['error']}", file=sys.stderr)
    return 1


async def cmd_migrate() -> int:
    """只跑 alembic upgrade head，不做 admin upsert / JSON 迁移。"""
    from app.db.migrations import bootstrap_and_upgrade

    try:
        await bootstrap_and_upgrade()
        print("✅ migrate 完成")
        return 0
    except Exception as e:
        print(f"❌ migrate 失败 ({type(e).__name__}): {e}", file=sys.stderr)
        return 1


async def cmd_migrate_status() -> int:
    from app.db.migrations import current_revision

    rev = await current_revision()
    print(f"current revision: {rev}")
    return 0


async def cmd_revision(message: str, autogenerate: bool) -> int:
    """生成新 migration 文件。手动模式：python -m app.cli revision -m "add foo"。
    自动模式（diff 当前 metadata vs DB）：加 --autogenerate。"""
    from alembic import command
    from app.db.migrations import _get_alembic_config

    cfg = _get_alembic_config()
    try:
        await asyncio.to_thread(command.revision, cfg, message=message, autogenerate=autogenerate)
        print("✅ revision 已生成（见 alembic/versions/）")
        return 0
    except Exception as e:
        print(f"❌ revision 失败 ({type(e).__name__}): {e}", file=sys.stderr)
        return 1


async def cmd_reset_admin() -> int:
    from sqlalchemy import select

    from app.auth.security import hash_password
    from app.config import get_settings
    from app.db.database import SessionLocal
    from app.db.models import User

    s = get_settings()
    async with SessionLocal() as session:
        row = (
            await session.execute(select(User).where(User.username == s.admin_username))
        ).scalar_one_or_none()
        if row is None:
            row = User(
                username=s.admin_username,
                password_hash=hash_password(s.admin_password),
                is_admin=True,
            )
            session.add(row)
            await session.commit()
            print(f"✅ 创建 admin: {s.admin_username}")
        else:
            row.password_hash = hash_password(s.admin_password)
            row.is_admin = True
            await session.commit()
            print(f"✅ 重置 admin 密码: {s.admin_username}")
    return 0


def main() -> int:
    _setup()
    parser = argparse.ArgumentParser(prog="app.cli", description="FishStudio 管理命令")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="检测/建库 + alembic upgrade + admin + 数据迁移")
    sub.add_parser("check", help="DB 健康检查")
    sub.add_parser("reset-admin", help="按 .env 重置 admin 密码")
    sub.add_parser("migrate", help="仅运行 alembic upgrade head")
    sub.add_parser("migrate-status", help="显示当前 DB revision")
    rev = sub.add_parser("revision", help="生成新 migration 文件")
    rev.add_argument("-m", "--message", required=True, help="迁移说明")
    rev.add_argument("--autogenerate", action="store_true", help="diff metadata vs DB 自动生成")

    args = parser.parse_args()
    if args.cmd == "revision":
        return asyncio.run(cmd_revision(args.message, args.autogenerate))
    handler = {
        "init-db": cmd_init_db,
        "check": cmd_check,
        "reset-admin": cmd_reset_admin,
        "migrate": cmd_migrate,
        "migrate-status": cmd_migrate_status,
    }[args.cmd]
    return asyncio.run(handler())


if __name__ == "__main__":
    sys.exit(main())
