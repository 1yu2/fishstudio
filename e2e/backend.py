"""仅供 E2E：隔离环境、重建专用测试 schema、种用户并启动真实 API/worker。"""

import asyncio
import ipaddress
import os
import sys
from urllib.parse import urlparse

import dotenv

# app.main 与其他模块仍会调用 load_dotenv，必须在导入 app 前禁用。
dotenv.load_dotenv = lambda *args, **kwargs: False
os.environ["PYTHON_DOTENV_DISABLED"] = "1"


def local_only(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        name = os.path.basename(os.fsdecode(args[0]))
        if name == ".env" or name.startswith(".env."):
            raise RuntimeError("E2E 禁止读取 .env 文件")
    if event == "socket.connect":
        address = args[1]
        if isinstance(address, tuple):
            try:
                local = ipaddress.ip_address(address[0]).is_loopback
            except ValueError:
                local = address[0] == "localhost"
            if not local:
                raise RuntimeError("E2E 禁止访问外部网络")


sys.addaudithook(local_only)

from app.config import Settings  # noqa: E402

Settings.model_config["env_file"] = None

from app.db.database import SessionLocal, engine  # noqa: E402
from app.db.init_db import init_db  # noqa: E402
from app.db.models import User  # noqa: E402
from app.auth.security import hash_password  # noqa: E402
from sqlalchemy import text  # noqa: E402


async def prepare():
    url = urlparse(os.environ["DATABASE_URL"])
    if (
        url.hostname not in ("127.0.0.1", "localhost")
        or url.path != "/fishstudio_test_c2"
        or url.port not in (5432, 55432)
    ):
        raise RuntimeError("只允许专用本地 fishstudio_test_c2 测试库")
    # 每次独立启动从空 schema 迁移，避免遗留配置启用真实厂商。
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await init_db()
    async with SessionLocal() as session:
        for name in ("e2e_user_a", "e2e_user_b"):
            session.add(
                User(
                    username=name,
                    password_hash=hash_password("e2e-user-password"),
                    is_admin=False,
                )
            )
        await session.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(prepare())
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8031)
