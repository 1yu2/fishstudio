"""
FishStudio后端主程序
使用FastAPI + LangGraph实现
"""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv()  # 在导入 settings 之前加载 .env

from app.auth.security import decode_token
from app.config import get_settings
from app.db.init_db import db_healthcheck, init_db
from app.observability import (
    RequestIdMiddleware,
    init_sentry,
    install_exception_handlers,
)
from app.routers import artifacts as artifacts_router
from app.routers import assets as assets_router
from app.routers import auth as auth_router
from app.routers import chat
from app.routers import eval as eval_router
from app.routers import jobs as jobs_router
from app.routers import model_runtime as model_runtime_router
from app.routers import series as series_router
from app.routers import settings as settings_router
from app.routers import workflow as workflow_router
from app.services import workspace_service
from app.services.connection_manager import manager
from app.utils.logger import setup_logging

_settings = get_settings()

# 初始化日志系统（含 structlog JSON 模式，由 LOG_FORMAT 控制）+ Sentry
setup_logging(log_level=_settings.log_level, log_format=_settings.log_format)
init_sentry()
logger = __import__("logging").getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    try:
        await init_db()
    except RuntimeError as e:
        # init_db 已把人话错误写进 message
        logger.error(str(e))
        raise SystemExit(1) from None
    logger.info("🚀 FishStudio API ready")
    # 内嵌 generation worker（开发方便；生产可 EMBEDDED_WORKER=false 单独跑 python -m app.worker.generation_worker）
    worker_task = None
    if os.getenv("EMBEDDED_WORKER", "true").lower() == "true":
        from app.worker.generation_worker import run_worker

        worker_task = asyncio.create_task(run_worker())
        logger.info("🧵 embedded generation worker started")
    yield
    # shutdown
    if worker_task is not None:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
    from app.db.database import engine as _engine

    await _engine.dispose()
    logger.info("👋 engine disposed, shutdown complete")


app = FastAPI(title="FishStudio API", version="1.0.0", lifespan=lifespan)

# 注意：中间件按"添加倒序"执行；最后 add 的最先执行
# request_id 必须最先（这样后续日志/异常都能拿到 rid），CORS 最后处理浏览器预检
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(RequestIdMiddleware)

# 安装统一异常处理（{error: {code, message, request_id}}）
install_exception_handlers(app)

# 确保存储目录存在
BASE_DIR = Path(__file__).parent.parent
STORAGE_DIR = BASE_DIR / "storage"
IMAGES_DIR = STORAGE_DIR / "images"
MODELS_DIR = STORAGE_DIR / "models"
VIDEOS_DIR = STORAGE_DIR / "videos"
AUDIOS_DIR = STORAGE_DIR / "audios"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
AUDIOS_DIR.mkdir(parents=True, exist_ok=True)
# 公共素材目录：/storage/public/** 无需登录即可访问（见下方 /storage 路由）
PUBLIC_DIR = STORAGE_DIR / "public"
PUBLIC_DIR.mkdir(parents=True, exist_ok=True)

# 确保工作空间默认文件存在
workspace_service.ensure_workspace_defaults()

# ─────────────────────────────────────────
# /storage 访问策略（review P0-3 复审：项目级鉴权）
#
# - 默认（STORAGE_PUBLIC_LEGACY=false）：不再整目录公开挂载。
#   由下方 GET /storage/{path} 手工鉴权服务：
#     * public/ 前缀：公共素材，直接放行；
#     * 其他路径：要求有效登录 token（query ?token= 或 Authorization header），
#       然后按 Artifact.storage_key == path 反查项目归属（ownership.check_project_access）：
#       owner / admin 放行；artifact 无 project_id → 仅 admin；
#       查不到 artifact 记录（老聊天上传的历史文件）→ 默认 404，仅当 env
#       STORAGE_LEGACY_AUTHENTICATED_READ=true 时退化为"登录即可读"
#       （默认 false；老聊天上传文件无 artifact 记录，迁移后可关闭此开关）。
# - STORAGE_PUBLIC_LEGACY=true：恢复旧行为，整目录 StaticFiles 公开挂载
#   （仅用于老前端未改造期间的临时兼容，生产应关闭）。
# 注意顺序：自定义路由必须先于任何 /storage mount 注册；legacy 模式下不注册
# 自定义路由，避免遮蔽 StaticFiles。
# ─────────────────────────────────────────
STORAGE_PUBLIC_LEGACY = os.getenv("STORAGE_PUBLIC_LEGACY", "false").lower() == "true"

if STORAGE_PUBLIC_LEGACY and STORAGE_DIR.exists():
    app.mount("/storage", StaticFiles(directory=str(STORAGE_DIR)), name="storage")
else:
    from fastapi import Request
    from fastapi.responses import FileResponse, JSONResponse

    _STORAGE_ROOT = STORAGE_DIR.resolve()

    def _safe_storage_file(path: str) -> Path | None:
        """resolve 后必须仍在 storage 根下，防目录穿越。"""
        full = (_STORAGE_ROOT / path).resolve()
        if not full.is_relative_to(_STORAGE_ROOT) or not full.is_file():
            return None
        return full

    @app.get("/storage/{path:path}")
    async def serve_storage(path: str, request: Request):
        if path.startswith("public/"):
            full = _safe_storage_file(path)
            if full is None:
                return JSONResponse(status_code=404, content={"detail": "not found"})
            return FileResponse(str(full))

        # 非 public：要求有效 token（前端 <img>/<video> 标签无法带 header，走 ?token=）
        token = request.query_params.get("token")
        if not token:
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth[len("Bearer "):]
        payload = decode_token(token) if token else None
        if not payload or "sub" not in payload:
            return JSONResponse(status_code=401, content={"detail": "unauthorized"})

        from sqlalchemy import select

        from app.db.database import SessionLocal
        from app.db.models import Artifact, SeriesProject, User
        from app.services.ownership import check_project_access

        def _404():
            return JSONResponse(status_code=404, content={"detail": "not found"})

        async with SessionLocal() as session:
            result = await session.execute(select(User).where(User.username == payload["sub"]))
            user = result.scalar_one_or_none()
            if user is None:
                return JSONResponse(status_code=401, content={"detail": "unauthorized"})

            artifact = (
                await session.execute(
                    select(Artifact).where(Artifact.storage_key == path).limit(1)
                )
            ).scalar_one_or_none()
            if artifact is None:
                # 无 artifact 记录的老文件（历史聊天上传）：默认拒绝；legacy 开关打开时
                # 退化为"登录即可读"（迁移老文件后可关闭此开关）
                if os.getenv("STORAGE_LEGACY_AUTHENTICATED_READ", "false").lower() != "true":
                    return _404()
            else:
                # 项目级鉴权：无权与不存在统一 404，不泄露存在性
                if artifact.project_id is None:
                    if not user.is_admin:
                        return _404()
                else:
                    project = await session.get(SeriesProject, artifact.project_id)
                    if project is None:
                        return _404()
                    try:
                        check_project_access(project, user)
                    except PermissionError:
                        return _404()

        full = _safe_storage_file(path)
        if full is None:
            return JSONResponse(status_code=404, content={"detail": "not found"})
        return FileResponse(str(full))

# 注册路由
app.include_router(artifacts_router.router, prefix="/api", tags=["artifacts"])
app.include_router(assets_router.router, prefix="/api", tags=["assets"])
app.include_router(auth_router.router, prefix="/api", tags=["auth"])
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(settings_router.router, prefix="/api", tags=["settings"])
app.include_router(jobs_router.router, prefix="/api", tags=["jobs"])
app.include_router(series_router.router, prefix="/api", tags=["series"])
app.include_router(workflow_router.router, prefix="/api", tags=["workflow"])
app.include_router(model_runtime_router.router, prefix="/api", tags=["model-runtime"])
app.include_router(eval_router.router, prefix="/api", tags=["eval"])


async def _ws_authenticate(websocket: WebSocket, canvas_id: str) -> bool:
    """WebSocket 鉴权：query param ?token= 的 JWT + canvas 归属校验。

    返回 False 表示已 close，端点直接 return。
    前端 WS 无法带 Authorization header，token 走 query param。
    """
    # 测试环境开关：conftest 置 true 跳过鉴权。生产严禁设置该变量（默认 false）。
    if os.getenv("WS_AUTH_DISABLED", "false").lower() == "true":
        return True

    token = websocket.query_params.get("token")
    payload = decode_token(token) if token else None
    if not payload or "sub" not in payload:
        await websocket.close(code=4401)
        return False

    from sqlalchemy import select

    from app.db.database import SessionLocal
    from app.db.models import Canvas, User

    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.username == payload["sub"]))
        user = result.scalar_one_or_none()
        if user is None:
            await websocket.close(code=4401)
            return False
        canvas = await session.get(Canvas, canvas_id)

    if canvas is None:
        await websocket.close(code=4404)
        return False
    if canvas.owner_id is None:
        # 历史无主 canvas：需一次性迁移认领归属，不在运行时放行给任意登录用户
        await websocket.close(code=4403)
        return False
    if canvas.owner_id != user.id:
        await websocket.close(code=4403)
        return False
    return True


@app.websocket("/ws/{canvas_id}")
async def websocket_endpoint(websocket: WebSocket, canvas_id: str):
    """Subscribe a browser canvas to live chat stream events."""
    if not await _ws_authenticate(websocket, canvas_id):
        return
    await manager.connect(canvas_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(canvas_id, websocket)


@app.get("/")
async def root():
    return {"message": "FishStudio API", "status": "running"}


@app.get("/health")
async def health():
    """活探针：进程在 = 200。不查 DB，避免 PG 抖动让 K8s 杀进程。"""
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    """就绪探针：DB 也通才返 200。挂 DB 时 503。"""
    db = await db_healthcheck()
    if not db["ok"]:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=503, content={"status": "degraded", "db": db})
    return {"status": "ok", "db": db}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app", host="0.0.0.0", port=8000, reload=True, reload_includes=["*.py"], log_level="info"
    )
