"""节点式无限画布：画布 CRUD 与自动保存、节点生成（图片 / 视频）、结果、上传、报价、可选模型、Copilot 拆镜头。

画布的图（节点 / 连线）由前端维护并整体保存；节点运行时前端把已解析好的输入
（提示词、参考图 / 首尾帧的 Artifact id）发给后端，后端校验归属后创建 GenerationJob，
由 Worker 执行。结果按 job.params.canvas_node_id 归到节点上。
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Artifact, GenerationJob, ModelRoute, NodeCanvas, SeriesProject, User
from app.services import asset_service, budget_service, feedback_service, job_service, series_service

logger = logging.getLogger(__name__)

PROJECT_TYPE = "node_canvas"
ASPECT_RATIOS = ("9:16", "16:9", "1:1", "4:3", "3:4", "21:9")
ACTIVE_JOB_STATUSES = ("pending", "leased", "running")
MAX_GRAPH_BYTES = 2 * 1024 * 1024
MAX_NODES = 500
UPLOAD_LIMITS = {"image": 20 * 1024 * 1024, "video": 300 * 1024 * 1024}
UPLOAD_MIME = {
    "image/png": "image",
    "image/jpeg": "image",
    "image/webp": "image",
    "video/mp4": "video",
    "video/quicktime": "video",
    "video/webm": "video",
}
STORAGE_DIR = Path(__file__).resolve().parent.parent.parent / "storage"


class CanvasError(ValueError):
    """面向用户的错误，消息可直接展示。"""


class CanvasConflict(CanvasError):
    """自动保存的版本已过期（另一个窗口保存过）。"""


# ── 画布 ──────────────────────────────────────────────────────────────────


def _empty_graph() -> dict[str, Any]:
    return {"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}


def _idea_graph(idea: str) -> dict[str, Any]:
    graph = _empty_graph()
    if idea.strip():
        graph["nodes"].append(
            {
                "id": f"text-{uuid.uuid4().hex[:8]}",
                "type": "text",
                "position": {"x": 80, "y": 160},
                "data": {"title": "想法", "text": idea.strip()},
            }
        )
    return graph


async def create_canvas(
    session: AsyncSession, user: User, *, title: str, aspect_ratio: str = "9:16", idea: str = ""
) -> NodeCanvas:
    if aspect_ratio not in ASPECT_RATIOS:
        raise CanvasError(f"画幅只支持 {', '.join(ASPECT_RATIOS)}")
    title = title.strip() or (idea.strip()[:20] if idea.strip() else "未命名画布")
    project = await series_service.create_project(
        session,
        user.id,
        project_type=PROJECT_TYPE,
        title=title,
        settings={"workspace_mode": "node_canvas"},
        episode_count=1,
    )
    canvas = NodeCanvas(
        owner_id=user.id,
        project_id=project.id,
        title=title,
        aspect_ratio=aspect_ratio,
        graph=_idea_graph(idea),
        revision=1,
    )
    session.add(canvas)
    await session.flush()
    return canvas


async def list_canvases(session: AsyncSession, user: User) -> list[NodeCanvas]:
    stmt = select(NodeCanvas).order_by(NodeCanvas.updated_at.desc())
    if not user.is_admin:
        stmt = stmt.where(NodeCanvas.owner_id == user.id)
    return list((await session.execute(stmt)).scalars().all())


async def get_canvas(session: AsyncSession, canvas_id: str, user: User) -> NodeCanvas:
    canvas = await session.get(NodeCanvas, canvas_id)
    if canvas is None or (canvas.owner_id != user.id and not user.is_admin):
        raise LookupError("画布不存在")
    return canvas


class GraphNode(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    type: Literal["text", "image", "video"]
    position: dict[str, float]
    data: dict[str, Any] = Field(default_factory=dict)
    width: Optional[float] = None
    height: Optional[float] = None


class GraphEdge(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    source: str
    target: str
    sourceHandle: Optional[str] = None
    targetHandle: Optional[str] = None


class Graph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list, max_length=MAX_NODES)
    edges: list[GraphEdge] = Field(default_factory=list, max_length=MAX_NODES * 4)
    viewport: dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0, "zoom": 1})

    @field_validator("edges")
    @classmethod
    def _edges_unique(cls, edges: list[GraphEdge]) -> list[GraphEdge]:
        if len({edge.id for edge in edges}) != len(edges):
            raise ValueError("连线 id 重复")
        return edges


async def save_graph(
    session: AsyncSession,
    canvas: NodeCanvas,
    graph: dict[str, Any],
    revision: int,
    user_id: Optional[int] = None,
) -> NodeCanvas:
    if revision != canvas.revision:
        raise CanvasConflict("画布已在其他窗口更新，请刷新后再编辑")
    if len(json.dumps(graph, ensure_ascii=False).encode()) > MAX_GRAPH_BYTES:
        raise CanvasError("画布内容过大")
    try:
        normalized = Graph.model_validate(graph)
    except ValidationError as error:
        raise CanvasError(f"画布数据不合法：{error.errors()[0].get('msg', '')}") from error
    ids = {node.id for node in normalized.nodes}
    if len(ids) != len(normalized.nodes):
        raise CanvasError("节点 id 重复")
    normalized.edges = [edge for edge in normalized.edges if edge.source in ids and edge.target in ids]
    old_selected = _selected_map(canvas.graph)
    canvas.graph = normalized.model_dump(mode="json", exclude_none=True)
    canvas.revision += 1
    await session.flush()
    new_selected = _selected_map(canvas.graph)
    for node_id, artifact_id in new_selected.items():
        if old_selected.get(node_id) != artifact_id:
            await _safe_feedback(session, canvas, "selected", user_id=user_id, artifact_id=artifact_id)
    return canvas


def _selected_map(graph: Optional[dict[str, Any]]) -> dict[str, str]:
    """节点 id → 当前选中的 artifact_id（没选中的节点不出现）。"""
    result: dict[str, str] = {}
    for node in (graph or {}).get("nodes", []):
        selected = (node.get("data") or {}).get("selected")
        if isinstance(selected, str) and selected:
            result[node.get("id")] = selected
    return result


async def _safe_feedback(
    session: AsyncSession,
    canvas: NodeCanvas,
    action: str,
    *,
    user_id: Optional[int],
    artifact_id: Optional[str] = None,
    node_id: Optional[str] = None,
) -> None:
    """写画布反馈；给了 node_id 时先查该节点已有的成功结果再记在它身上。

    为反馈做的查询和写入都在同一个 savepoint 里：任何失败只回滚 savepoint 并记日志，
    不会让外层事务进入 aborted 状态，也就不会阻断保存 / 建任务 / 上传主流程。
    """
    try:
        async with session.begin_nested():
            target = artifact_id
            if node_id is not None:
                target = await _existing_result_id(session, canvas, node_id)
            if not target:
                return
            await feedback_service.record_canvas_feedback(
                session, canvas=canvas, artifact_id=target, user_id=user_id, action=action
            )
    except Exception:
        logger.warning("画布反馈写入失败（已忽略）：canvas=%s action=%s", canvas.id, action, exc_info=True)


async def _existing_result_id(session: AsyncSession, canvas: NodeCanvas, node_id: str) -> Optional[str]:
    """节点已有的成功生成结果：优先当前选中的，否则最新的；没有则返回 None。须在 savepoint 内调用。"""
    rows = (
        (
            await session.execute(
                select(Artifact.id)
                .join(GenerationJob, Artifact.job_id == GenerationJob.id)
                .where(
                    Artifact.project_id == canvas.project_id,
                    GenerationJob.status == "succeeded",
                    GenerationJob.params["canvas_node_id"].astext == node_id,
                )
                .order_by(Artifact.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    selected = _selected_map(canvas.graph).get(node_id)
    return selected if selected in rows else rows[0]


async def update_meta(
    session: AsyncSession, canvas: NodeCanvas, *, title: Optional[str], aspect_ratio: Optional[str]
) -> NodeCanvas:
    if title is not None and title.strip():
        canvas.title = title.strip()
        project = await session.get(SeriesProject, canvas.project_id)
        if project is not None:
            project.title = canvas.title
    if aspect_ratio is not None:
        if aspect_ratio not in ASPECT_RATIOS:
            raise CanvasError(f"画幅只支持 {', '.join(ASPECT_RATIOS)}")
        canvas.aspect_ratio = aspect_ratio
    await session.flush()
    return canvas


async def delete_canvas(session: AsyncSession, canvas: NodeCanvas) -> None:
    project = await session.get(SeriesProject, canvas.project_id)
    await session.delete(canvas)
    if project is not None:
        project.status = "archived"
    await session.flush()


def canvas_summary(canvas: NodeCanvas) -> dict[str, Any]:
    nodes = (canvas.graph or {}).get("nodes", [])
    return {
        "id": canvas.id,
        "title": canvas.title,
        "aspect_ratio": canvas.aspect_ratio,
        "node_count": len(nodes),
        "updated_at": canvas.updated_at.isoformat() if canvas.updated_at else None,
        "created_at": canvas.created_at.isoformat() if canvas.created_at else None,
    }


# ── 节点生成 ──────────────────────────────────────────────────────────────


class NodeRunRequest(BaseModel):
    kind: Literal["image", "video"]
    prompt: str = Field(default="", max_length=8_000)
    model: str = Field(default="", max_length=128, description="Deployment slug；留空使用默认模型")
    aspect_ratio: Optional[str] = None
    duration: int = Field(default=5, ge=2, le=15)
    tier: Literal["preview", "final"] = "final"
    count: int = Field(default=1, ge=1, le=4)
    reference_artifact_ids: list[str] = Field(default_factory=list, max_length=8)
    first_frame_artifact_id: Optional[str] = None
    last_frame_artifact_id: Optional[str] = None
    # 幂等标识：前端每次点击生成一个，自动重试复用；不传则保持旧行为（每次都是新任务）
    client_request_id: Optional[str] = Field(default=None, min_length=1, max_length=64)


# 批量报价时，尚未生成的上游节点用 “pending:节点id” 占位（只用于估价，不会写进任务）
PENDING_PREFIX = "pending:"


# ── 服务端输入解析（与前端 graph.ts 的 resolveInputs 等价）─────────────────

# 每种节点的输入端口 → 该端口接受的上游节点类型（与前端 INPUTS 一致）
HANDLE_LABELS = {"prompt": "提示词", "reference": "参考图", "first_frame": "首帧", "last_frame": "尾帧"}
_KIND_LABEL = {"text": "文本", "image": "图片", "video": "视频"}


def node_label(node: dict[str, Any]) -> str:
    text_value = str((node.get("data") or {}).get("label") or "").strip()
    return text_value or _KIND_LABEL.get(node.get("type"), "")


def _num(value: Any, default: int) -> int:
    """对应前端的 `value || default`：0 / 空 / 非数字都取默认值。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number or default


def selected_artifact(node: dict[str, Any], results: dict[str, list[str]]) -> Optional[str]:
    """节点当前选中的结果：用户点选的那张（仍在结果里），否则是最新一张。"""
    items = results.get(node.get("id"), [])
    chosen = (node.get("data") or {}).get("selected")
    return chosen if chosen in items else (items[0] if items else None)


def resolve_node_request(
    graph: dict[str, Any],
    node_id: str,
    results: dict[str, list[str]],
    aspect_ratio: str,
    missing_upstream: Optional[list[str]] = None,
) -> tuple[dict[str, Any], list[str]]:
    """汇总一个图片 / 视频节点的全部输入，返回 (提交给 run 的请求字段, 不能生成的原因)。

    results：节点 id → 结果 artifact id 列表（最新在前，顺序同 node_runs）。
    missing_upstream：可选的输出列表；每有一条“上游节点还没有图片”的 blocker，就追加该上游节点 id，
    供批量运行结构化判断 blocker 的来源（blocker 文案不变）。
    字段与前端 resolveInputs 的 request 逐项一致，两边共用 tests/fixtures/node_canvas_resolve_cases.json 对照。
    """
    by_id = {node["id"]: node for node in graph.get("nodes", [])}
    node = by_id.get(node_id)
    if node is None or node.get("type") not in ("image", "video"):
        raise CanvasError("节点不存在或不是图片 / 视频节点")
    edges = graph.get("edges", [])
    data = node.get("data") or {}

    def incoming(handle: str) -> list[dict[str, Any]]:
        return [
            by_id[edge["source"]]
            for edge in edges
            if edge.get("target") == node_id
            and edge.get("targetHandle") == handle
            and edge.get("source") in by_id
        ]

    blockers: list[str] = []

    def frame(handle: str) -> Optional[str]:
        upstream = incoming(handle)
        if not upstream:
            return None
        artifact_id = selected_artifact(upstream[0], results)
        if not artifact_id:
            blockers.append(f"{HANDLE_LABELS[handle]}节点「{node_label(upstream[0])}」还没有图片")
            if missing_upstream is not None:
                missing_upstream.append(upstream[0]["id"])
        return artifact_id

    upstream_prompt = "\n".join(
        part
        for part in (str((item.get("data") or {}).get("text") or "").strip() for item in incoming("prompt"))
        if part
    )
    own = str(data.get("prompt") or "").strip()
    prompt = "\n".join(part for part in (upstream_prompt, own) if part)

    if node["type"] == "image":
        references: list[str] = []
        for item in incoming("reference"):
            artifact_id = selected_artifact(item, results)
            if not artifact_id:
                blockers.append(f"参考图节点「{node_label(item)}」还没有图片")
                if missing_upstream is not None:
                    missing_upstream.append(item["id"])
            else:
                references.append(artifact_id)
        if not prompt:
            blockers.append("写提示词，或连入一个文本节点")
        return {
            "kind": "image",
            "prompt": prompt,
            "model": data.get("model") or "",
            "aspect_ratio": aspect_ratio,
            "count": _num(data.get("count"), 1),
            "reference_artifact_ids": references,
        }, blockers

    has_first = bool(incoming("first_frame"))
    first = frame("first_frame")
    last = frame("last_frame")
    if incoming("last_frame") and not has_first:
        blockers.append("有尾帧时也需要连入首帧")
    if not prompt and not has_first:
        blockers.append("写提示词，或连入首帧图片")
    return {
        "kind": "video",
        "prompt": prompt,
        "model": data.get("model") or "",
        "aspect_ratio": aspect_ratio,
        "duration": _num(data.get("duration"), 5),
        "tier": data.get("tier") or "preview",
        "first_frame_artifact_id": first,
        "last_frame_artifact_id": last,
    }, blockers


async def _storage_key(
    session: AsyncSession,
    canvas: NodeCanvas,
    artifact_id: Optional[str],
    expected: str,
    *,
    quote_only: bool = False,
) -> Optional[str]:
    if not artifact_id:
        return None
    if quote_only and artifact_id.startswith(PENDING_PREFIX):
        return artifact_id
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None or artifact.project_id != canvas.project_id:
        raise CanvasError("引用的素材不属于这个画布")
    if artifact.type != expected:
        raise CanvasError(f"这里需要{'图片' if expected == 'image' else '视频'}素材")
    return artifact.storage_key


async def _video_plan(
    session: AsyncSession, canvas: NodeCanvas, req: NodeRunRequest, *, quote_only: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    from app.model_runtime.video import VideoRoutingError, resolve_video_plan
    from app.services.job_planner import build_video_job_params

    if not req.prompt.strip() and not req.first_frame_artifact_id:
        raise CanvasError("视频节点需要提示词或首帧图片")
    first = await _storage_key(session, canvas, req.first_frame_artifact_id, "image", quote_only=quote_only)
    last = await _storage_key(session, canvas, req.last_frame_artifact_id, "image", quote_only=quote_only)
    ratio = req.aspect_ratio or canvas.aspect_ratio
    params = build_video_job_params(
        shot_id="",
        step_id="",
        run_id="",
        prompt=req.prompt.strip(),
        duration_ms=req.duration * 1000,
        aspect_ratio=ratio,
        policy={"resolution": "768P", "video_tier": req.tier},
        first_frame=first,
    )
    if last and first:
        params["last_frame"] = last
        params["mode"] = "first_last_frame"
    try:
        plan = await resolve_video_plan(
            session,
            duration=req.duration,
            tier=req.tier,
            mode=params["mode"],
            aspect_ratio=ratio,
            reference_count=0,
            explicit_deployment=req.model,
        )
    except VideoRoutingError as error:
        logger.info(f"视频路由不可用: {error}")
        raise CanvasError(video_routing_message(error.code, req.duration)) from error
    return params, plan


NO_VIDEO_MODEL = "还没有设置视频模型：在「设置 → 模型服务 → 默认模型」里设置「镜头视频」"


def video_routing_message(code: str, duration: int) -> str:
    """把视频路由错误换成用户看得懂的话；技术细节只进日志。"""
    if code == "no_route":
        return NO_VIDEO_MODEL
    if code == "route_disabled":
        return "默认的「镜头视频」模型已停用：在「设置 → 模型服务 → 默认模型」里重新启用"
    return (
        f"没有能生成这个 {duration} 秒镜头的视频模型：可能缺少 API Key 或不支持这个时长，"
        "请在「设置 → 模型服务」检查"
    )


def _image_price(slug: str, count: int) -> Optional[Decimal]:
    from app.model_runtime.catalog import get_catalog

    item = get_catalog().snapshot.get(slug) if slug else None
    price = (item.pricing or {}).get("output_per_image") if item else None
    try:
        return Decimal(str(price)) * count if price not in (None, "") else None
    except ArithmeticError:
        return None


NO_IMAGE_MODEL = "还没有可用的图片模型：在「设置 → 模型服务」添加图片模型，并在「默认模型」里设置「图片生成」。也可以先上传图片"


def _image_deployment_ready(item: Any) -> bool:
    if item is None or not item.enabled or item.modality != "image":
        return False
    available = getattr(item.instance, "is_available", None)
    return available() if available is not None else True


async def image_model_problem(session: AsyncSession, model: str = "", *, edit: bool = False) -> str:
    """图片节点能否生成；返回给用户看的原因，空字符串表示可以。

    与 Worker 的解析顺序一致：指定模型 → 用途默认路由（主 + 备用）→ 没有任何图片配置时的旧 env 兜底。
    提前检查，避免任务排进队列后才在 Worker 里失败。
    """
    from app.config import mock_providers_enabled
    from app.model_runtime.catalog import get_catalog
    from app.providers.volcano_image import VolcanoImageProvider

    if mock_providers_enabled():
        return ""
    snapshot = get_catalog().snapshot
    if model:
        if _image_deployment_ready(snapshot.get(model)):
            return ""
        return f"所选图片模型「{model}」不可用：可能已停用或缺少 API Key，请在「设置 → 模型服务」检查，或改用默认模型"
    purpose = "image_edit" if edit else "image_generation"
    route = await session.get(ModelRoute, (purpose, "final"))
    if route is not None and route.enabled:
        slugs = [route.primary_deployment, *(route.fallback_deployments or [])]
        if any(_image_deployment_ready(snapshot.get(slug)) for slug in slugs):
            return ""
        return f"默认的{'图片编辑' if edit else '图片生成'}模型都不可用：请在「设置 → 模型服务」检查是否启用、是否配置了 API Key"
    if (
        route is None
        and not snapshot.list("image")
        and VolcanoImageProvider.from_legacy_env(purpose).is_available()
    ):
        return ""
    if edit:
        return (
            "还没有可用的图片编辑模型：连了参考图时需要在「默认模型」里设置「图片编辑」，或先去掉参考图连线"
        )
    return NO_IMAGE_MODEL


async def _default_image_model(session: AsyncSession, edit: bool) -> str:
    route = await session.get(ModelRoute, ("image_edit" if edit else "image_generation", "final"))
    return route.primary_deployment if route else ""


async def quote_node_with_budget(
    session: AsyncSession, canvas: NodeCanvas, req: NodeRunRequest, *, quote_only: bool = False
) -> tuple[dict[str, Any], Decimal]:
    """报价 + 计入每日预算的云端预估（本地自托管为 0，与创建任务时的预留口径一致）。"""
    if req.kind == "video":
        params, plan = await _video_plan(session, canvas, req, quote_only=quote_only)
        quote = {
            "kind": "video",
            "mode": params["mode"],
            "model": plan.get("deployment_name") or plan["provider"],
            "resolution": plan["resolution"],
            "estimated_cost": float(plan["estimated_total_cost"]),
        }
        cloud = (
            Decimal(str(plan["estimated_total_cost"]))
            if budget_service.is_cloud(plan["provider"])
            else Decimal(0)
        )
        return quote, cloud
    slug = req.model or await _default_image_model(session, edit=bool(req.reference_artifact_ids))
    price = _image_price(slug, req.count)
    quote = {"kind": "image", "model": slug, "estimated_cost": float(price) if price is not None else None}
    return quote, price or Decimal(0)


async def quote_node(session: AsyncSession, canvas: NodeCanvas, req: NodeRunRequest) -> dict[str, Any]:
    return (await quote_node_with_budget(session, canvas, req))[0]


async def _reserve_budget(session: AsyncSession, canvas: NodeCanvas, estimate: Decimal) -> None:
    try:
        await budget_service.reserve(session, canvas.owner_id, estimate)
    except budget_service.BudgetExceeded as error:
        raise CanvasError(str(error)) from error


async def run_node(
    session: AsyncSession,
    canvas: NodeCanvas,
    node_id: str,
    req: NodeRunRequest,
    user_id: Optional[int] = None,
) -> GenerationJob:
    """创建节点生成任务；节点此前已有成功结果时，任务创建成功后记一条 regenerated 反馈。

    带 client_request_id 的重复请求直接返回已有任务：不再预留预算，也不记反馈。
    """
    job, created = await _create_node_job(session, canvas, node_id, req)
    if created:
        # 新任务尚未成功，不会被当作“已有结果”，所以建完再查也只会命中此前的成功结果
        await _safe_feedback(session, canvas, "regenerated", user_id=user_id, node_id=node_id)
    return job


def request_fingerprint(req: NodeRunRequest) -> str:
    """规范化请求字段（不含 client_request_id）的哈希前 16 位，用于幂等 key。"""
    data = req.model_dump(exclude={"client_request_id"})
    data["prompt"] = req.prompt.strip()
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _idempotency_key(canvas: NodeCanvas, node_id: str, req: NodeRunRequest) -> str:
    if req.client_request_id:
        return f"canvas:{canvas.id}:{node_id}:{req.kind}:{request_fingerprint(req)}:{req.client_request_id}"
    return f"canvas:{canvas.id}:{node_id}:{req.kind}:{uuid.uuid4().hex}"


async def advisory_lock(session: AsyncSession, key: str) -> None:
    """按字符串 key 取事务级咨询锁（随事务结束释放），用来串行化同一幂等标识的并发请求。"""
    digest = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": digest})


async def _existing_job_for_key(session: AsyncSession, key: str) -> Optional[GenerationJob]:
    """同 key 的请求先串行再查已有任务。

    先于预算预留执行：并发的重复请求在锁上等第一个提交，之后直接命中它的任务，
    不会重复预留，也不会因为第一个任务的在途预估把自己挤出额度。
    """
    await advisory_lock(session, key)
    return (
        await session.execute(select(GenerationJob).where(GenerationJob.idempotency_key == key))
    ).scalar_one_or_none()


async def _create_node_job(
    session: AsyncSession,
    canvas: NodeCanvas,
    node_id: str,
    req: NodeRunRequest,
    extra_params: Optional[dict[str, Any]] = None,
    graph: Optional[dict[str, Any]] = None,
) -> tuple[GenerationJob, bool]:
    """返回 (job, 是否新建)。"""
    from app.providers.pricing import PRICE_TABLE_VERSION

    # 批量运行按创建时的快照图校验节点，不受之后的画布编辑影响
    if not any(
        node.get("id") == node_id
        for node in ((graph if graph is not None else canvas.graph) or {}).get("nodes", [])
    ):
        raise CanvasError("节点不存在，可能还没保存")
    idem_key = _idempotency_key(canvas, node_id, req)
    if req.client_request_id:
        existing = await _existing_job_for_key(session, idem_key)
        if existing is not None:
            return existing, False
    base = {"canvas_id": canvas.id, "canvas_node_id": node_id, **(extra_params or {})}
    if req.kind == "image":
        if not req.prompt.strip():
            raise CanvasError("图片节点需要提示词")
        references = [
            await _storage_key(session, canvas, artifact_id, "image")
            for artifact_id in req.reference_artifact_ids
        ]
        problem = await image_model_problem(session, req.model, edit=bool(references))
        if problem:
            raise CanvasError(problem)
        slug = req.model or await _default_image_model(session, edit=bool(references))
        estimate = _image_price(slug, req.count) or Decimal(0)
        await _reserve_budget(session, canvas, estimate)
        job = await job_service.create_job(
            session,
            operation="generate_image",
            project_id=canvas.project_id,
            params={
                **base,
                "prompt": req.prompt.strip(),
                "aspect_ratio": req.aspect_ratio or canvas.aspect_ratio,
                "candidate_count": req.count,
                # 有参考图时以第一张为参考生成（图片编辑用途），保持形象一致
                "source_image": references[0] if references else None,
                "deployment": req.model or None,
                budget_service.ESTIMATE_PARAM: str(estimate),
            },
            idempotency_key=idem_key,
            worker_class="image",
        )
        return job, True

    params, plan = await _video_plan(session, canvas, req)
    params.update(base)
    params["reference_images"] = []
    params["resolution"] = plan["resolution"]
    params["video_tier"] = req.tier
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
    await _reserve_budget(session, canvas, estimate)
    params[budget_service.ESTIMATE_PARAM] = str(estimate)
    job = await job_service.create_job(
        session,
        operation="generate_video",
        project_id=canvas.project_id,
        params=params,
        provider=plan["provider"],
        model=plan["model"],
        idempotency_key=idem_key,
        worker_class="video",
    )
    return job, True


def _display_status(job: GenerationJob) -> tuple[str, int]:
    """界面展示用的状态。

    已提交给供应商的任务按供应商的状态展示：供应商那边还在排队就显示排队和位置，否则显示生成中。
    （pending + 外部任务 ID 是旧版 Worker 留下的轮询状态，一并兼容。）
    """
    remote = job.result or {}
    queue_position = int(remote.get("queue_position") or 0)
    if job.status in ("pending", "running") and job.external_task_id:
        if remote.get("remote_status") == "queued" or queue_position > 0:
            return "queued", queue_position
        return "running", 0
    if job.status == "pending":
        return "queued", 0
    return job.status, 0


async def node_runs(session: AsyncSession, canvas: NodeCanvas) -> dict[str, Any]:
    """每个节点的任务状态与结果（最新在前），以及画布累计花费。"""
    from app.services import cost_report_service

    jobs = (
        (
            await session.execute(
                select(GenerationJob)
                .where(GenerationJob.project_id == canvas.project_id)
                .order_by(GenerationJob.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    artifacts = (
        (
            await session.execute(
                select(Artifact)
                .where(Artifact.project_id == canvas.project_id)
                .order_by(Artifact.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    by_job: dict[str, list[Artifact]] = {}
    for artifact in artifacts:
        if artifact.job_id:
            by_job.setdefault(artifact.job_id, []).append(artifact)
    nodes: dict[str, dict[str, Any]] = {}
    for job in jobs:
        node_id = (job.params or {}).get("canvas_node_id")
        if not node_id:
            continue
        entry = nodes.setdefault(node_id, {"jobs": [], "results": []})
        status, queue_position = _display_status(job)
        entry["jobs"].append(
            {
                "id": job.id,
                "status": status,
                "progress": job.progress or 0,
                "queue_position": queue_position,
                "error": (job.error or "")[:300],
                "model": (job.params or {}).get("deployment")
                or ((job.params or {}).get("routing_snapshot") or {}).get("deployment_name")
                or job.model,
                "created_at": job.created_at.isoformat() if job.created_at else None,
            }
        )
        for artifact in by_job.get(job.id, []):
            entry["results"].append(
                {
                    "artifact_id": artifact.id,
                    "type": artifact.type,
                    "url": asset_service.artifact_url(artifact),
                    "tier": (job.params or {}).get("video_tier"),
                    "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
                }
            )
    uploads = [
        {
            "artifact_id": artifact.id,
            "type": artifact.type,
            "url": asset_service.artifact_url(artifact),
            "node_id": (artifact.artifact_metadata or {}).get("canvas_node_id"),
        }
        for artifact in artifacts
        if not artifact.job_id and (artifact.artifact_metadata or {}).get("source") == "canvas_upload"
    ]
    for upload in uploads:
        if upload["node_id"]:
            nodes.setdefault(upload["node_id"], {"jobs": [], "results": []})["results"].append(
                {key: upload[key] for key in ("artifact_id", "type", "url")} | {"uploaded": True}
            )
    cost = await cost_report_service.cost_report(session, project_id=canvas.project_id, dimensions=())
    return {
        "nodes": nodes,
        "active_jobs": sum(job.status in ACTIVE_JOB_STATUSES for job in jobs),
        "spent": cost["totals"]["effective_cost"] or 0,
        "budget": await budget_service.status(session, canvas.owner_id),
    }


async def upload(
    session: AsyncSession,
    canvas: NodeCanvas,
    *,
    node_id: str,
    filename: str,
    content_type: str,
    data: bytes,
    user_id: Optional[int] = None,
) -> Artifact:
    kind = UPLOAD_MIME.get((content_type or "").lower()) or UPLOAD_MIME.get(
        mimetypes.guess_type(filename)[0] or ""
    )
    if kind is None:
        raise CanvasError("只支持 PNG / JPEG / WebP 图片和 MP4 / MOV / WebM 视频")
    if not data or len(data) > UPLOAD_LIMITS[kind]:
        raise CanvasError(f"文件为空或超过 {UPLOAD_LIMITS[kind] // (1024 * 1024)}MB")
    suffix = Path(filename).suffix.lower() or mimetypes.guess_extension(content_type) or ""
    relative = Path("uploads") / "canvas" / canvas.id / f"{uuid.uuid4().hex}{suffix}"
    target = STORAGE_DIR / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    artifact = await asset_service.create_artifact(
        session,
        project_id=canvas.project_id,
        type=kind,
        storage_key=relative.as_posix(),
        mime_type=content_type or mimetypes.guess_type(filename)[0] or "",
        meta={"source": "canvas_upload", "canvas_node_id": node_id, "filename": filename[:200]},
    )
    # 上传产物没有 job，不会被当作“已有生成结果”
    await _safe_feedback(session, canvas, "replaced", user_id=user_id, node_id=node_id)
    return artifact


async def available_models(session: AsyncSession) -> dict[str, Any]:
    """节点上可选的图片 / 视频模型（已启用的模型，含供应商名与价格），以及默认模型。"""
    from app.db.models import ModelProvider
    from app.model_runtime.catalog import get_catalog

    providers = {row.id: row for row in (await session.execute(select(ModelProvider))).scalars().all()}
    from app.db.models import ModelDeployment

    rows = (
        (
            await session.execute(
                select(ModelDeployment).where(ModelDeployment.modality.in_(("image", "video")))
            )
        )
        .scalars()
        .all()
    )
    snapshot = get_catalog().snapshot
    out: dict[str, list[dict[str, Any]]] = {"image": [], "video": []}
    for row in rows:
        item = snapshot.get(row.slug)
        provider = providers.get(row.provider_id or "")
        if item is None or not item.enabled:
            continue
        out[row.modality].append(
            {
                "slug": row.slug,
                "name": row.name,
                "model": row.model,
                "provider": provider.name if provider else "",
                "modes": list((row.capabilities or {}).get("modes") or []),
                "pricing": row.pricing or {},
            }
        )
    defaults = {}
    for purpose, tier, key in (
        ("image_generation", "final", "image"),
        ("shot_video", "preview", "video_preview"),
        ("shot_video", "final", "video_final"),
    ):
        route = await session.get(ModelRoute, (purpose, tier))
        if route is None and tier != "final":
            route = await session.get(ModelRoute, (purpose, "final"))
        defaults[key] = route.primary_deployment if route else ""
    return {**out, "defaults": defaults, "image_problem": await image_model_problem(session)}


# ── Copilot：想法 → 角色与镜头 ────────────────────────────────────────────


class SplitCharacter(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    description: str = Field(default="", max_length=600)


class SplitShot(BaseModel):
    title: str = Field(default="", max_length=60)
    prompt: str = Field(min_length=1, max_length=800)
    characters: list[str] = Field(default_factory=list)
    duration_s: int = Field(default=5, ge=2, le=12)


class SplitResult(BaseModel):
    characters: list[SplitCharacter] = Field(default_factory=list, max_length=6)
    shots: list[SplitShot] = Field(min_length=1, max_length=12)


SPLIT_PROMPT = """你是短视频分镜师。把创作者的想法拆成角色和镜头，只输出一个 JSON 对象，不要解释：
{"characters": [{"name": "角色名", "description": "外貌、年龄、服装、标志特征，用于生成角色定妆图"}],
 "shots": [{"title": "镜头短标题", "prompt": "画面内容与动作，具体可视化，包含景别和光线", "characters": ["出镜角色名"], "duration_s": 3 到 10}]}
要求：镜头数接近要求；每个镜头只有一个动作；同一角色外貌前后一致；不要编造未出现的角色。"""


async def _call_llm(messages: list[dict[str, str]]) -> str:
    from app.llm.factory import create_llm

    try:
        llm = create_llm(purpose="planner")
    except Exception as error:  # noqa: BLE001 - 未配置模型时给出可操作提示
        raise CanvasError(
            "还没有可用的对话模型，请在「设置 → 模型服务 → 默认模型」里设置「剧本拆分与规划」"
        ) from error
    response = await llm.ainvoke(messages)
    return str(getattr(response, "content", response))


def _parse_json(raw: str) -> dict[str, Any]:
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("没有 JSON")
    return json.loads(raw[start : end + 1])


async def copilot_split(canvas: NodeCanvas, *, idea: str, shots: int) -> SplitResult:
    if not idea.strip():
        raise CanvasError("先写一个想法")
    messages = [
        {"role": "system", "content": SPLIT_PROMPT},
        {
            "role": "user",
            "content": f"画幅 {canvas.aspect_ratio}，大约 {shots} 个镜头。想法：\n{idea.strip()}",
        },
    ]
    raw = await _call_llm(messages)
    try:
        return SplitResult.model_validate(_parse_json(raw))
    except (ValueError, ValidationError) as error:
        messages += [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": f"输出不符合要求：{str(error)[:300]}。只输出修正后的完整 JSON。"},
        ]
        repaired = await _call_llm(messages)
        try:
            return SplitResult.model_validate(_parse_json(repaired))
        except (ValueError, ValidationError) as final_error:
            raise CanvasError("模型返回的分镜格式无法识别，请重试或换一个模型") from final_error
