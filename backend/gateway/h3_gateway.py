"""MiniMax H3 -> ComfyUI 异步 Gateway。

对 FishStudio 暴露稳定的 local_h3_v1 合同，并把具体 ComfyUI 工作流、节点 ID、
队列和文件结构封装在远端服务器内。
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

GATEWAY_DIR = Path(__file__).resolve().parent
DEFAULT_WORKFLOW = GATEWAY_DIR / "workflows" / "video_minimax_h3_t2v.json"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "true" if default else "false")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.api_key = os.getenv("H3_GATEWAY_API_KEY", "").strip()
        self.comfy_url = os.getenv("H3_COMFY_URL", "http://127.0.0.1:8190").rstrip("/")
        self.public_url = os.getenv("H3_GATEWAY_PUBLIC_URL", "http://127.0.0.1:8088").rstrip("/")
        self.deployment = os.getenv("H3_GATEWAY_DEPLOYMENT", "h3-fl2va-int8-convrot").strip()
        self.model = os.getenv("H3_GATEWAY_MODEL", "MiniMax-H3").strip()
        self.workflow_path = Path(os.getenv("H3_GATEWAY_WORKFLOW", str(DEFAULT_WORKFLOW))).expanduser()
        state_dir = Path(os.getenv("H3_GATEWAY_STATE_DIR", str(GATEWAY_DIR / "state"))).expanduser()
        self.db_path = state_dir / "gateway.sqlite3"
        self.artifact_dir = state_dir / "artifacts"
        self.poll_seconds = max(0.5, float(os.getenv("H3_GATEWAY_POLL_SECONDS", "2")))
        self.request_timeout = float(os.getenv("H3_GATEWAY_COMFY_TIMEOUT_SECONDS", "30"))
        self.artifact_timeout = float(os.getenv("H3_GATEWAY_ARTIFACT_TIMEOUT_SECONDS", "900"))
        self.max_queue = max(1, int(os.getenv("H3_GATEWAY_MAX_QUEUE", "8")))
        self.max_artifact_bytes = int(os.getenv("H3_GATEWAY_MAX_ARTIFACT_BYTES", str(512 * 1024 * 1024)))
        self.turbo = _env_bool("H3_GATEWAY_TURBO", True)
        self.turbo_lora = os.getenv(
            "H3_GATEWAY_TURBO_LORA",
            "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
        ).strip()
        self.turbo_steps = max(1, int(os.getenv("H3_GATEWAY_TURBO_STEPS", "8")))

        state_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)


class GenerationRequest(BaseModel):
    deployment: str
    model: str = "MiniMax-H3"
    prompt: str = Field(min_length=1, max_length=20_000)
    mode: str = "text"
    duration: float = Field(ge=4, le=6)
    resolution: str = "preview"
    aspect_ratio: str = "16:9"
    quality: str = "preview"
    seed: int | None = None
    references: list[dict[str, Any]] = Field(default_factory=list)


class GatewayStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = asyncio.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    prompt_id TEXT NOT NULL DEFAULT '',
                    artifact_json TEXT NOT NULL DEFAULT '',
                    error_json TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL
                )
                """
            )
            # ComfyUI 不提供幂等提交键。若进程恰好在 POST /prompt 成功、但 prompt_id
            # 尚未落库时崩溃，自动重提可能产生重复推理；宁可标记为不确定失败并人工重试。
            conn.execute(
                """UPDATE tasks
                   SET status='failed',
                       error_json=?,
                       finished_at=?,
                       updated_at=?
                   WHERE status='submitting'""",
                (
                    json.dumps(
                        {
                            "code": "submission_uncertain",
                            "message": "Gateway 在 ComfyUI 提交确认窗口内重启，未自动重提以避免重复推理",
                        },
                        ensure_ascii=False,
                    ),
                    time.time(),
                    time.time(),
                ),
            )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["request"] = json.loads(data.pop("request_json"))
        data["artifacts"] = json.loads(data.pop("artifact_json")) if data["artifact_json"] else []
        data["error"] = json.loads(data.pop("error_json")) if data["error_json"] else None
        return data

    async def get(self, task_id: str) -> dict[str, Any] | None:
        async with self.lock:
            with self._connect() as conn:
                return self._row(conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone())

    async def get_by_key(self, key: str) -> dict[str, Any] | None:
        async with self.lock:
            with self._connect() as conn:
                return self._row(
                    conn.execute("SELECT * FROM tasks WHERE idempotency_key=?", (key,)).fetchone()
                )

    async def active_count(self) -> int:
        async with self.lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS total FROM tasks WHERE status IN ('queued','running')"
                ).fetchone()
                return int(row["total"])

    async def create(self, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        task_id = f"h3_{uuid.uuid4().hex}"
        async with self.lock:
            with self._connect() as conn:
                try:
                    conn.execute(
                        """INSERT INTO tasks
                           (task_id,idempotency_key,status,request_json,created_at,updated_at)
                           VALUES (?,?,?,?,?,?)""",
                        (task_id, key, "queued", json.dumps(payload, ensure_ascii=False), now, now),
                    )
                except sqlite3.IntegrityError:
                    row = conn.execute("SELECT * FROM tasks WHERE idempotency_key=?", (key,)).fetchone()
                    existing = self._row(row)
                    if existing is None:
                        raise
                    return existing
        created = await self.get(task_id)
        assert created is not None
        return created

    async def next_active(self) -> dict[str, Any] | None:
        async with self.lock:
            with self._connect() as conn:
                row = conn.execute(
                    """SELECT * FROM tasks
                       WHERE status IN ('running','queued')
                       ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, created_at
                       LIMIT 1"""
                ).fetchone()
                return self._row(row)

    async def update(self, task_id: str, **values: Any) -> dict[str, Any] | None:
        if not values:
            return await self.get(task_id)
        encoded: dict[str, Any] = {"updated_at": time.time()}
        for key, value in values.items():
            if key == "artifacts":
                encoded["artifact_json"] = json.dumps(value, ensure_ascii=False)
            elif key == "error":
                encoded["error_json"] = json.dumps(value, ensure_ascii=False)
            else:
                encoded[key] = value
        columns = ", ".join(f"{key}=?" for key in encoded)
        async with self.lock:
            with self._connect() as conn:
                conn.execute(
                    f"UPDATE tasks SET {columns} WHERE task_id=?",
                    (*encoded.values(), task_id),
                )
        return await self.get(task_id)


class WorkflowBuilder:
    PROMPT_NODE = "140:131"
    DURATION_NODE = "140:133"
    SEED_NODE = "140:129"
    LORA_NODE = "140:134"
    CLIP_NODE = "140:128"
    TURBO_SWITCH_NODE = "140:139"
    TURBO_STEPS_NODE = "140:138"
    OUTPUT_NODE = "92"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.template = json.loads(settings.workflow_path.read_text(encoding="utf-8"))
        required = {
            self.PROMPT_NODE,
            self.DURATION_NODE,
            self.SEED_NODE,
            self.LORA_NODE,
            self.CLIP_NODE,
            self.TURBO_SWITCH_NODE,
            self.TURBO_STEPS_NODE,
            self.OUTPUT_NODE,
        }
        missing = required - self.template.keys()
        if missing:
            raise RuntimeError(f"H3 API 工作流缺少节点: {sorted(missing)}")

    @staticmethod
    def size(aspect_ratio: str, resolution: str) -> tuple[int, int]:
        preview = resolution.lower() == "preview"
        sizes = {
            "16:9": (512, 288) if preview else (672, 384),
            "9:16": (288, 512) if preview else (384, 672),
            "1:1": (384, 384) if preview else (512, 512),
            "4:3": (448, 336) if preview else (576, 432),
            "3:4": (336, 448) if preview else (432, 576),
        }
        if aspect_ratio not in sizes:
            raise ValueError(f"不支持的画幅: {aspect_ratio}")
        return sizes[aspect_ratio]

    def build(self, task_id: str, request: GenerationRequest) -> tuple[dict[str, Any], int, int]:
        if request.deployment != self.settings.deployment:
            raise ValueError("deployment 与 Gateway 不匹配")
        if request.model != self.settings.model:
            raise ValueError("model 与 Gateway 不匹配")
        if request.mode != "text" or request.references:
            raise ValueError("当前 Gateway 仅开放纯文生视频 text 模式")
        width, height = self.size(request.aspect_ratio, request.resolution)
        workflow = copy.deepcopy(self.template)
        prompt_inputs = workflow[self.PROMPT_NODE]["inputs"]
        prompt_inputs.update({"prompt": request.prompt, "width": width, "height": height})
        workflow[self.DURATION_NODE]["inputs"]["value"] = request.duration
        workflow[self.SEED_NODE]["inputs"]["noise_seed"] = (
            request.seed if request.seed is not None else int.from_bytes(os.urandom(8), "big")
        )
        workflow[self.OUTPUT_NODE]["inputs"]["filename_prefix"] = f"fishstudio/{task_id}"
        workflow[self.CLIP_NODE]["inputs"]["device"] = "cpu"
        workflow[self.TURBO_SWITCH_NODE]["inputs"]["value"] = self.settings.turbo
        workflow[self.LORA_NODE]["inputs"]["lora_name"] = self.settings.turbo_lora
        workflow[self.TURBO_STEPS_NODE]["inputs"]["value"] = self.settings.turbo_steps
        return workflow, width, height


class ComfyClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        timeout = kwargs.pop("timeout", self.settings.request_timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, f"{self.settings.comfy_url}{path}", **kwargs)
            response.raise_for_status()
            return response

    async def health(self) -> tuple[dict[str, Any], dict[str, Any]]:
        stats, queue = await asyncio.gather(
            self._request("GET", "/system_stats"), self._request("GET", "/queue")
        )
        return stats.json(), queue.json()

    async def submit(self, workflow: dict[str, Any], client_id: str) -> str:
        response = await self._request("POST", "/prompt", json={"prompt": workflow, "client_id": client_id})
        data = response.json()
        if data.get("node_errors"):
            raise ValueError(f"ComfyUI node_errors: {data['node_errors']}")
        prompt_id = str(data.get("prompt_id") or "")
        if not prompt_id:
            raise RuntimeError("ComfyUI /prompt 响应缺少 prompt_id")
        return prompt_id

    async def history(self, prompt_id: str) -> dict[str, Any] | None:
        response = await self._request("GET", f"/history/{prompt_id}")
        data = response.json()
        return data.get(prompt_id) or None

    async def cancel(self, prompt_id: str) -> None:
        try:
            await self._request("POST", "/queue", json={"delete": [prompt_id]})
        except httpx.HTTPError:
            pass
        try:
            await self._request("POST", "/interrupt")
        except httpx.HTTPError:
            pass

    async def download_output(self, descriptor: dict[str, Any]) -> bytes:
        response = await self._request(
            "GET",
            "/view",
            params={
                "filename": descriptor["filename"],
                "subfolder": descriptor.get("subfolder", ""),
                "type": descriptor.get("type", "output"),
            },
            timeout=self.settings.artifact_timeout,
        )
        content = response.content
        if not content or len(content) > self.settings.max_artifact_bytes:
            raise ValueError("ComfyUI 视频产物为空或超过大小限制")
        return content


def _find_video(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        filename = str(value.get("filename") or "")
        if filename.lower().endswith((".mp4", ".mov", ".webm", ".mkv")):
            return value
        for child in value.values():
            found = _find_video(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_video(child)
            if found:
                return found
    return None


def _history_error(history: dict[str, Any]) -> str:
    messages = history.get("status", {}).get("messages", [])
    for message in reversed(messages):
        if isinstance(message, list) and len(message) > 1 and message[0] == "execution_error":
            payload = message[1] if isinstance(message[1], dict) else {}
            return str(payload.get("exception_message") or payload)[:2000]
    status = history.get("status", {})
    if status.get("completed") is False and status.get("status_str") == "error":
        return "ComfyUI 执行失败"
    return ""


def _probe_video(path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:format=duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        data = json.loads(result.stdout)
        stream = (data.get("streams") or [{}])[0]
        duration = float((data.get("format") or {}).get("duration") or 0)
        return {
            "width": stream.get("width"),
            "height": stream.get("height"),
            "duration_ms": round(duration * 1000) if duration else None,
        }
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return {}


class GatewayService:
    def __init__(self, settings: Settings, store: GatewayStore, comfy: ComfyClient):
        self.settings = settings
        self.store = store
        self.comfy = comfy
        self.builder = WorkflowBuilder(settings)

    async def run(self) -> None:
        while True:
            try:
                task = await self.store.next_active()
                if task is None:
                    await asyncio.sleep(self.settings.poll_seconds)
                    continue
                if task["status"] == "queued":
                    await self._submit(task)
                else:
                    await self._poll(task)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(self.settings.poll_seconds)

    async def _submit(self, task: dict[str, Any]) -> None:
        try:
            request = GenerationRequest.model_validate(task["request"])
            workflow, _, _ = self.builder.build(task["task_id"], request)
            await self.store.update(task["task_id"], status="submitting")
            prompt_id = await self.comfy.submit(workflow, task["task_id"])
            await self.store.update(
                task["task_id"], status="running", prompt_id=prompt_id, started_at=time.time()
            )
        except Exception as error:
            await self._fail(task["task_id"], error)

    async def _poll(self, task: dict[str, Any]) -> None:
        try:
            history = await self.comfy.history(task["prompt_id"])
            if history is None:
                await asyncio.sleep(self.settings.poll_seconds)
                return
            error = _history_error(history)
            if error:
                raise RuntimeError(error)
            descriptor = _find_video(history.get("outputs", {}))
            if descriptor is None:
                await asyncio.sleep(self.settings.poll_seconds)
                return
            content = await self.comfy.download_output(descriptor)
            current = await self.store.get(task["task_id"])
            if current is None or current["status"] == "canceled":
                return
            artifact_id = uuid.uuid4().hex
            path = self.settings.artifact_dir / f"{artifact_id}.mp4"
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            metadata = _probe_video(path)
            request = GenerationRequest.model_validate(task["request"])
            artifact = {
                "artifact_id": artifact_id,
                "type": "video",
                "url": f"{self.settings.public_url}/v1/files/{artifact_id}",
                "sha256": digest,
                "duration_ms": metadata.get("duration_ms") or round(request.duration * 1000),
                "width": metadata.get("width"),
                "height": metadata.get("height"),
            }
            await self.store.update(
                task["task_id"],
                status="succeeded",
                artifacts=[artifact],
                finished_at=time.time(),
            )
        except Exception as error:
            await self._fail(task["task_id"], error)

    async def _fail(self, task_id: str, error: Exception) -> None:
        message = str(error)[:2000]
        lowered = message.lower()
        if "out of memory" in lowered or "cuda oom" in lowered:
            code = "oom"
        elif isinstance(error, httpx.TimeoutException):
            code = "timeout"
        elif isinstance(error, ValueError):
            code = "invalid_request"
        else:
            code = "inference_failed"
        await self.store.update(
            task_id,
            status="failed",
            error={"code": code, "message": message},
            finished_at=time.time(),
        )


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_id": task["task_id"],
        "status": task["status"],
        "progress": 100 if task["status"] == "succeeded" else 0,
        "artifacts": task["artifacts"],
        "error": task["error"],
        "created_at": task["created_at"],
        "updated_at": task["updated_at"],
    }
    if task.get("started_at"):
        payload["usage"] = {
            "gpu_seconds": max(0, (task.get("finished_at") or time.time()) - task["started_at"]),
            "output_video_seconds": task["request"].get("duration", 0),
        }
    return payload


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    if not settings.api_key:
        raise RuntimeError("H3_GATEWAY_API_KEY 未配置")
    store = GatewayStore(settings.db_path)
    comfy = ComfyClient(settings)
    service = GatewayService(settings, store, comfy)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        worker = asyncio.create_task(service.run(), name="h3-gateway-worker")
        try:
            yield
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    app = FastAPI(title="FishStudio MiniMax H3 Gateway", version="1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.comfy = comfy
    app.state.service = service

    async def authenticate(authorization: str = Header(default="")) -> None:
        prefix = "Bearer "
        token = authorization[len(prefix) :] if authorization.startswith(prefix) else ""
        if not token or not hmac.compare_digest(token, settings.api_key):
            raise HTTPException(status_code=401, detail="invalid bearer token")

    @app.get("/health", dependencies=[Depends(authenticate)])
    async def health() -> dict[str, Any]:
        try:
            stats, queue = await comfy.health()
        except httpx.HTTPError as error:
            raise HTTPException(status_code=503, detail=f"ComfyUI unavailable: {error}") from error
        running = len(queue.get("queue_running", []))
        pending = len(queue.get("queue_pending", []))
        return {
            "status": "ok",
            "deployment": settings.deployment,
            "model": settings.model,
            "backend": "comfyui",
            "gpu_ready": True,
            "queue_depth": running + pending,
            "gateway_queue_depth": await store.active_count(),
            "capabilities": {
                "modes": ["text"],
                "durations": [4, 5, 6],
                "aspect_ratios": ["16:9", "9:16", "1:1", "4:3", "3:4"],
                "max_concurrency": 1,
                "turbo_steps": settings.turbo_steps if settings.turbo else None,
            },
            "system": stats,
        }

    @app.post("/v1/generations", dependencies=[Depends(authenticate)], status_code=202)
    async def create_generation(
        body: GenerationRequest,
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise HTTPException(status_code=400, detail="Idempotency-Key is required")
        existing = await store.get_by_key(idempotency_key.strip())
        if existing:
            return _public_task(existing)
        if await store.active_count() >= settings.max_queue:
            raise HTTPException(status_code=429, detail="gateway queue is full")
        try:
            service.builder.build("validation", body)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        task = await store.create(idempotency_key.strip(), body.model_dump())
        return _public_task(task)

    @app.get("/v1/generations/{task_id}", dependencies=[Depends(authenticate)])
    async def get_generation(task_id: str) -> dict[str, Any]:
        task = await store.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return _public_task(task)

    @app.delete("/v1/generations/{task_id}", dependencies=[Depends(authenticate)])
    async def cancel_generation(task_id: str) -> dict[str, Any]:
        task = await store.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        if task["status"] in {"succeeded", "failed", "canceled"}:
            return _public_task(task)
        if task.get("prompt_id"):
            await comfy.cancel(task["prompt_id"])
        updated = await store.update(task_id, status="canceled", finished_at=time.time())
        assert updated is not None
        return _public_task(updated)

    @app.get("/v1/files/{artifact_id}", dependencies=[Depends(authenticate)])
    async def get_file(artifact_id: str, request: Request) -> FileResponse:
        if not artifact_id or any(char not in "0123456789abcdef" for char in artifact_id):
            raise HTTPException(status_code=404, detail="artifact not found")
        path = settings.artifact_dir / f"{artifact_id}.mp4"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="artifact not found")
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    return app
