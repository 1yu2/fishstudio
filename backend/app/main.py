"""
FishStudio后端主程序
使用FastAPI + LangGraph实现
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()  # 在导入 settings 之前加载 .env

from app.config import get_settings

from app.db.init_db import db_healthcheck, init_db
from app.routers import auth as auth_router
from app.routers import chat, settings as settings_router
from app.services import workspace_service
from app.services.connection_manager import manager
from app.utils.logger import setup_logging

_settings = get_settings()

# 初始化日志系统
setup_logging(log_level=_settings.log_level)
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
    yield
    # shutdown
    from app.db.database import engine as _engine
    await _engine.dispose()
    logger.info("👋 engine disposed, shutdown complete")


app = FastAPI(title="FishStudio API", version="1.0.0", lifespan=lifespan)

# 配置 CORS：环境变量驱动的白名单
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

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

# 确保工作空间默认文件存在
workspace_service.ensure_workspace_defaults()

# 配置静态文件服务
if STORAGE_DIR.exists():
    app.mount("/storage", StaticFiles(directory=str(STORAGE_DIR)), name="storage")

# 注册路由
app.include_router(auth_router.router, prefix="/api", tags=["auth"])
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(settings_router.router, prefix="/api", tags=["settings"])


@app.websocket("/ws/{canvas_id}")
async def websocket_endpoint(websocket: WebSocket, canvas_id: str):
    """Subscribe a browser canvas to live chat stream events."""
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
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_includes=["*.py"],
        log_level="info"
    )
