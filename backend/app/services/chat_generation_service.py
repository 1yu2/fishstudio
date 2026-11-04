"""聊天 Agent 的图片 / 视频生成：和节点画布一样走 GenerationJob，由 Worker 执行。

Agent 工具只负责「提交 job + 异步等待结果」，不直连厂商：
- 受每日云端花费上限约束（budget_service.reserve），超限不建 job；
- 可取消（job 取消接口）、留下 ModelInvocation / UsageEvent（由 Worker 执行器写入）；
- Worker 可能嵌入 API 进程，也可能是独立进程，所以只通过数据库轮询 job 状态，不假设同进程。
"""

from __future__ import annotations

import asyncio
import os
import posixpath
import re
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Artifact, Canvas, GenerationJob, SeriesProject
from app.services import asset_service, budget_service, job_event_service, job_service, series_service

CHAT_PROJECT_TYPE = "chat"
POLL_INTERVAL_SECONDS = 2.0
IMAGE_TIMEOUT_SECONDS = 5 * 60
VIDEO_TIMEOUT_SECONDS = 15 * 60
SOURCE = "chat"
_ARTIFACT_URL = re.compile(r"/api/artifacts/([^/?#]+)/download")
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1")
STORAGE_DIR = Path(__file__).resolve().parent.parent.parent / "storage"


class ChatGenerationError(ValueError):
    """面向用户的错误，消息可直接返回给 Agent / 用户。"""


# ── 归属项目 ──────────────────────────────────────────────────────────────


async def get_or_create_chat_project(
    session: AsyncSession, user_id: int, canvas_id: Optional[str]
) -> SeriesProject:
    """聊天画布对应的归属项目（GenerationJob.project_id 非空）。

    同一画布重复调用复用同一项目：优先取 SeriesProject.canvas_id 已绑定的项目（聊天路由用的也是它）；
    没有则新建 project_type="chat" 的项目并绑定画布。画布行尚未落库（或没有画布）时
    无法绑定 canvas_id，退化为用 settings.chat_canvas_id 标记的无画布项目，同样可复用。
    """
    project = await _find_chat_project(session, user_id, canvas_id)
    if project is not None:
        return project
    bind_canvas = None
    if canvas_id:
        canvas = await session.get(Canvas, canvas_id)
        if canvas is not None and canvas.owner_id == user_id:
            bind_canvas = canvas_id
    try:
        return await series_service.create_project(
            session,
            user_id,
            canvas_id=bind_canvas,
            project_type=CHAT_PROJECT_TYPE,
            title="聊天生成",
            settings={"chat_canvas_id": canvas_id or "", "workspace_mode": "chat"},
            episode_count=1,
        )
    except (IntegrityError, ValueError):
        # 并发首次创建：另一请求已先绑定了该画布（canvas_id 唯一约束 / create_project 的重复校验），
        # 回滚失败的事务后重新查询，复用已存在的项目
        await session.rollback()
        project = await _find_chat_project(session, user_id, canvas_id)
        if project is None:
            raise
        return project


async def _find_chat_project(
    session: AsyncSession, user_id: int, canvas_id: Optional[str]
) -> Optional[SeriesProject]:
    if canvas_id:
        bound = (
            (await session.execute(select(SeriesProject).where(SeriesProject.canvas_id == canvas_id)))
            .scalars()
            .first()
        )
        if bound is not None:
            if bound.user_id is not None and bound.user_id != user_id:
                raise ChatGenerationError("无权在该画布上生成内容")
            return bound
    return (
        (
            await session.execute(
                select(SeriesProject).where(
                    SeriesProject.user_id == user_id,
                    SeriesProject.project_type == CHAT_PROJECT_TYPE,
                    SeriesProject.canvas_id.is_(None),
                    SeriesProject.settings["chat_canvas_id"].as_string() == (canvas_id or ""),
                )
            )
        )
        .scalars()
        .first()
    )


# ── 输入素材 ──────────────────────────────────────────────────────────────


async def _artifact_by_key(session: AsyncSession, key: str) -> Optional[Artifact]:
    return (
        await session.execute(select(Artifact).where(Artifact.storage_key == key).limit(1))
    ).scalar_one_or_none()


async def _check_artifact_owner(session: AsyncSession, user_id: int, artifact: Optional[Artifact]) -> None:
    """产物必须属于当前用户的项目（与 GET /storage/{path} 的项目级鉴权一致）。"""
    owner = (
        await session.scalar(select(SeriesProject.user_id).where(SeriesProject.id == artifact.project_id))
        if artifact is not None and artifact.project_id
        else None
    )
    if artifact is None or owner != user_id:
        raise ChatGenerationError("引用的素材不存在或无权访问")


def _legacy_authenticated_read() -> bool:
    return os.getenv("STORAGE_LEGACY_AUTHENTICATED_READ", "false").lower() == "true"


def _safe_storage_key(raw: str) -> str:
    """规范化 /storage/ 之后的相对路径；绝对路径、.. 穿越、逃出 STORAGE_DIR 一律拒绝。"""
    key = posixpath.normpath(unquote(raw).strip())
    if key in ("", ".") or key.startswith(("/", "..")) or ".." in key.split("/"):
        raise ChatGenerationError("素材路径不合法")
    root = STORAGE_DIR.resolve()
    if not (root / key).resolve().is_relative_to(root):
        raise ChatGenerationError("素材路径不合法")
    return key


async def resolve_media_ref(session: AsyncSession, user_id: int, ref: Optional[str]) -> Optional[str]:
    """把 Agent 传来的图片引用转成 Worker / 各 Provider 都能读的输入。

    只接受：
    - /storage/<key> 与 localhost 的 /storage/<key> URL：规范化后必须在 STORAGE_DIR 内，
      且 public/ 下的公共素材，或能按 storage_key 查到属于当前用户项目的 Artifact；
    - /api/artifacts/{id}/download：校验 Artifact 归属后取其 storage_key；
    - 公网 http(s) URL：原样交给 Provider。
    本地引用统一输出 "/storage/<key>"（Volcano 图片 / 视频、Ark、MiniMax 都支持这种形式；
    裸 storage_key 不被 Volcano 图片 Provider 接受）。其余（裸路径、绝对路径、data URI 等）拒绝。
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    parsed = urlparse(ref)
    is_url = parsed.scheme in ("http", "https")
    if parsed.scheme not in ("", "http", "https"):
        raise ChatGenerationError("不支持的素材引用")
    path = parsed.path if is_url else ref
    match = _ARTIFACT_URL.fullmatch(path)
    if match and (not is_url or parsed.hostname in _LOCAL_HOSTS):
        artifact = await session.get(Artifact, match.group(1))
        await _check_artifact_owner(session, user_id, artifact)
        return f"/storage/{_safe_storage_key(artifact.storage_key)}"
    if path.startswith("/storage/") and (not is_url or parsed.hostname in _LOCAL_HOSTS):
        key = _safe_storage_key(path[len("/storage/") :])
        if not key.startswith("public/"):
            artifact = await _artifact_by_key(session, key)
            # 无 artifact 记录的老文件（历史聊天上传）：与 GET /storage/{path} 一致，仅 legacy 开关打开时放行
            if artifact is not None or not _legacy_authenticated_read():
                await _check_artifact_owner(session, user_id, artifact)
        return f"/storage/{key}"
    if is_url and parsed.hostname not in _LOCAL_HOSTS:
        return ref
    raise ChatGenerationError("素材只支持 /storage/ 路径、产物链接或公网图片 URL")


# ── 提交 ──────────────────────────────────────────────────────────────────


async def _reserve_and_create(
    session: AsyncSession,
    *,
    user_id: int,
    estimate: Decimal,
    operation: str,
    project_id: str,
    params: dict[str, Any],
    worker_class: str,
    provider: str = "",
    model: str = "",
) -> GenerationJob:
    try:
        await budget_service.reserve(session, user_id, estimate)
    except budget_service.BudgetExceeded as error:
        await session.rollback()
        raise ChatGenerationError(str(error)) from error
    params[budget_service.ESTIMATE_PARAM] = str(estimate)
    job = await job_service.create_job(
        session,
        operation=operation,
        project_id=project_id,
        params=params,
        provider=provider,
        model=model,
        idempotency_key=f"chat:{project_id}:{operation}:{uuid.uuid4().hex}",
        worker_class=worker_class,
    )
    await session.commit()
    # 提交后再唤醒一次，避免 Worker 在事务提交前被唤醒而扫不到 job
    await job_event_service.notify_ready(job.id)
    return job


async def submit_image_job(
    session: AsyncSession,
    *,
    user_id: int,
    canvas_id: Optional[str],
    prompt: str,
    aspect_ratio: str = "1:1",
    source_image: Optional[str] = None,
) -> GenerationJob:
    from app.services import node_canvas_service

    if not prompt.strip():
        raise ChatGenerationError("图片生成需要提示词")
    project = await get_or_create_chat_project(session, user_id, canvas_id)
    source_key = await resolve_media_ref(session, user_id, source_image)
    slug = await node_canvas_service._default_image_model(session, edit=bool(source_key))
    estimate = node_canvas_service._image_price(slug, 1) or Decimal(0)
    return await _reserve_and_create(
        session,
        user_id=user_id,
        estimate=estimate,
        operation="generate_image",
        project_id=project.id,
        params={
            "canvas_id": canvas_id,
            "source": SOURCE,
            "prompt": prompt.strip(),
            "aspect_ratio": aspect_ratio,
            "candidate_count": 1,
            "source_image": source_key,
            "deployment": None,
        },
        worker_class="image",
    )


async def submit_video_job(
    session: AsyncSession,
    *,
    user_id: int,
    canvas_id: Optional[str],
    prompt: str,
    duration: Optional[int] = None,
    ratio: str = "16:9",
    first_frame: Optional[str] = None,
    last_frame: Optional[str] = None,
) -> GenerationJob:
    from app.model_runtime.video import VideoRoutingError, resolve_video_plan
    from app.providers.pricing import PRICE_TABLE_VERSION
    from app.services.job_planner import build_video_job_params

    if not prompt.strip() and not first_frame:
        raise ChatGenerationError("视频生成需要提示词或首帧图片")
    seconds = max(2, min(15, int(duration or 5)))
    project = await get_or_create_chat_project(session, user_id, canvas_id)
    first = await resolve_media_ref(session, user_id, first_frame)
    last = await resolve_media_ref(session, user_id, last_frame)
    params = build_video_job_params(
        shot_id="",
        step_id="",
        run_id="",
        prompt=prompt.strip(),
        duration_ms=seconds * 1000,
        aspect_ratio=ratio,
        policy={"resolution": "768P", "video_tier": "final"},
        first_frame=first,
    )
    if last and first:
        params["last_frame"] = last
        params["mode"] = "first_last_frame"
    try:
        plan = await resolve_video_plan(
            session,
            duration=seconds,
            tier="final",
            mode=params["mode"],
            aspect_ratio=ratio,
            reference_count=0,
            explicit_deployment="",
        )
    except VideoRoutingError as error:
        raise ChatGenerationError(str(error)) from error
    params.update({"canvas_id": canvas_id, "source": SOURCE})
    params["reference_images"] = []
    params["resolution"] = plan["resolution"]
    params["video_tier"] = "final"
    params["routing_snapshot"] = {
        "provider": plan["provider"],
        "model": plan["model"],
        "resolution": plan["resolution"],
        "protocol": plan.get("protocol", ""),
        "billable_duration": plan["billable_duration"],
        "estimated_total_cost": str(plan["estimated_total_cost"]),
        "price_snapshot": plan.get("price_snapshot", {}),
        "price_table_version": plan.get("price_table_version", PRICE_TABLE_VERSION),
        "deployment_name": plan.get("deployment_name", ""),
        "deployment_revision": plan.get("deployment_revision"),
        "deployment_config_hash": plan.get("deployment_config_hash", ""),
        "catalog_revision": plan.get("catalog_revision", ""),
        "route": plan.get("route", {}),
    }
    for key in ("shot_id", "step_id", "run_id", "trace"):
        params.pop(key, None)
    estimate = (
        Decimal(str(plan["estimated_total_cost"]))
        if budget_service.is_cloud(plan["provider"])
        else Decimal(0)
    )
    return await _reserve_and_create(
        session,
        user_id=user_id,
        estimate=estimate,
        operation="generate_video",
        project_id=project.id,
        params=params,
        worker_class="video",
        provider=plan["provider"],
        model=plan["model"],
    )


# ── 等待结果 ──────────────────────────────────────────────────────────────


async def wait_for_job(job_id: str, *, timeout: float, interval: Optional[float] = None) -> dict[str, Any]:
    """异步轮询 job 直到终态或超时（每次用新会话读库，只依赖数据库状态）。

    返回 {"status": succeeded|failed|canceled|timeout, "job_id", ...}；
    succeeded 附 artifacts=[{"artifact_id","url","storage_key"}]，failed 附 error。
    """
    from app.db.database import SessionLocal

    step = POLL_INTERVAL_SECONDS if interval is None else interval
    deadline = time.monotonic() + timeout
    while True:
        async with SessionLocal() as session:
            job = await job_service.get_job(session, job_id)
            if job is None:
                return {"status": "failed", "job_id": job_id, "error": "任务不存在"}
            if job.status == "succeeded":
                return {
                    "status": "succeeded",
                    "job_id": job_id,
                    "artifacts": await _collect_artifacts(session, job),
                    "result": dict(job.result or {}),
                }
            if job.status in ("failed", "canceled"):
                return {
                    "status": job.status,
                    "job_id": job_id,
                    "error": job.error or ("任务已取消" if job.status == "canceled" else "生成失败"),
                }
        if time.monotonic() + step > deadline:
            return {"status": "timeout", "job_id": job_id}
        await asyncio.sleep(step)


async def _collect_artifacts(session: AsyncSession, job: GenerationJob) -> list[dict[str, str]]:
    result = job.result or {}
    ids = list(result.get("artifact_ids") or [])
    if not ids and result.get("artifact_id"):
        ids = [result["artifact_id"]]
    artifacts: list[dict[str, str]] = []
    for artifact_id in ids:
        artifact = await session.get(Artifact, artifact_id)
        if artifact is not None:
            artifacts.append(
                {
                    "artifact_id": artifact.id,
                    "url": f"/storage/{artifact.storage_key}",
                    "artifact_url": asset_service.artifact_url(artifact),
                    "storage_key": artifact.storage_key,
                }
            )
    return artifacts
