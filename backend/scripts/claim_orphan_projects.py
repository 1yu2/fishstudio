"""一次性迁移脚本：把 user_id 为 NULL 的 series_projects 全部认领给 admin（review 0.4）。

用法：
    cd backend
    python scripts/claim_orphan_projects.py [--dry-run]

--dry-run 只打印将认领的项目明细，不写库。
"""

import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.database import SessionLocal  # noqa: E402
from app.db.models import SeriesProject, User  # noqa: E402


async def main(dry_run: bool) -> None:
    settings = get_settings()
    async with SessionLocal() as session:
        admin = (
            await session.execute(select(User).where(User.username == settings.admin_username))
        ).scalar_one_or_none()
        if admin is None:
            print(f"❌ admin 用户 {settings.admin_username} 不存在，请先启动服务初始化")
            sys.exit(1)

        orphans = (
            (
                await session.execute(
                    select(SeriesProject)
                    .where(SeriesProject.user_id.is_(None))
                    .order_by(SeriesProject.created_at)
                )
            )
            .scalars()
            .all()
        )
        print(f"发现 {len(orphans)} 个无主项目（user_id IS NULL）：")
        for p in orphans:
            print(f"  - {p.id}  title={p.title!r}  status={p.status}  created_at={p.created_at}")

        if dry_run:
            print("dry-run：不做任何修改")
            return

        for p in orphans:
            p.user_id = admin.id
        await session.commit()
        print(f"✅ 已把 {len(orphans)} 个项目认领给 admin（id={admin.id}, {admin.username}）")


if __name__ == "__main__":
    asyncio.run(main(dry_run="--dry-run" in sys.argv))
