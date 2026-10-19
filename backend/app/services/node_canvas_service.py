"""节点式无限画布：画布 CRUD 与自动保存、节点生成（图片 / 视频）、结果、上传、报价、可选模型、Copilot 拆镜头。

画布的图（节点 / 连线）由前端维护并整体保存；节点运行时前端把已解析好的输入
（提示词、参考图 / 首尾帧的 Artifact id）发给后端，后端校验归属后创建 GenerationJob，
由 Worker 执行。结果按 job.params.canvas_node_id 归到节点上。
"""

from __future__ import annotations

import json
import logging
import mimetypes
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Artifact, GenerationJob, ModelRoute, NodeCanvas, SeriesProject, User
from app.services import asset_service, job_service, series_service

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
    session: AsyncSession, canvas: NodeCanvas, graph: dict[str, Any], revision: int
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
    canvas.graph = normalized.model_dump(mode="json", exclude_none=True)
    canvas.revision += 1
    await session.flush()
    return canvas


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


async def _storage_key(
    session: AsyncSession, canvas: NodeCanvas, artifact_id: Optional[str], expected: str
) -> Optional[str]:
    if not artifact_id:
        return None
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None or artifact.project_id != canvas.project_id:
        raise CanvasError("引用的素材不属于这个画布")
    if artifact.type != expected:
        raise CanvasError(f"这里需要{'图片' if expected == 'image' else '视频'}素材")
    return artifact.storage_key


async def _video_plan(
    session: AsyncSession, canvas: NodeCanvas, req: NodeRunRequest
) -> tuple[dict[str, Any], dict[str, Any]]:
    from app.model_runtime.video import VideoRoutingError, resolve_video_plan
    from app.services.job_planner import build_video_job_params

    if not req.prompt.strip() and not req.first_frame_artifact_id:
        raise CanvasError("视频节点需要提示词或首帧图片")
    first = await _storage_key(session, canvas, req.first_frame_artifact_id, "image")
    last = await _storage_key(session, canvas, req.last_frame_artifact_id, "image")
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
        raise CanvasError(str(error)) from error
    return params, plan


def _image_price(slug: str, count: int) -> Optional[Decimal]:
    from app.model_runtime.catalog import get_catalog

    item = get_catalog().snapshot.get(slug) if slug else None
    price = (item.pricing or {}).get("output_per_image") if item else None
    try:
        return Decimal(str(price)) * count if price not in (None, "") else None
    except ArithmeticError:
        return None


async def _default_image_model(session: AsyncSession, edit: bool) -> str:
    route = await session.get(ModelRoute, ("image_edit" if edit else "image_generation", "final"))
    return route.primary_deployment if route else ""


async def quote_node(session: AsyncSession, canvas: NodeCanvas, req: NodeRunRequest) -> dict[str, Any]:
    if req.kind == "video":
        params, plan = await _video_plan(session, canvas, req)
        return {
            "kind": "video",
            "mode": params["mode"],
            "model": plan.get("deployment_name") or plan["provider"],
            "resolution": plan["resolution"],
            "estimated_cost": float(plan["estimated_total_cost"]),
        }
    slug = req.model or await _default_image_model(session, edit=bool(req.reference_artifact_ids))
    price = _image_price(slug, req.count)
    return {"kind": "image", "model": slug, "estimated_cost": float(price) if price is not None else None}


async def run_node(
    session: AsyncSession, canvas: NodeCanvas, node_id: str, req: NodeRunRequest
) -> GenerationJob:
    from app.providers.pricing import PRICE_TABLE_VERSION

    if not any(node.get("id") == node_id for node in (canvas.graph or {}).get("nodes", [])):
        raise CanvasError("节点不存在，可能还没保存")
    base = {"canvas_id": canvas.id, "canvas_node_id": node_id}
    if req.kind == "image":
        if not req.prompt.strip():
            raise CanvasError("图片节点需要提示词")
        references = [
            await _storage_key(session, canvas, artifact_id, "image")
            for artifact_id in req.reference_artifact_ids
        ]
        return await job_service.create_job(
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
            },
            idempotency_key=f"canvas:{canvas.id}:{node_id}:image:{uuid.uuid4().hex}",
            worker_class="image",
        )

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
    for key in ("shot_id", "step_id", "run_id"):
        params.pop(key, None)
    return await job_service.create_job(
        session,
        operation="generate_video",
        project_id=canvas.project_id,
        params=params,
        provider=plan["provider"],
        model=plan["model"],
        idempotency_key=f"canvas:{canvas.id}:{node_id}:video:{uuid.uuid4().hex}",
        worker_class="video",
    )


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
        entry["jobs"].append(
            {
                "id": job.id,
                "status": job.status,
                "progress": job.progress or 0,
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
    }


async def upload(
    session: AsyncSession, canvas: NodeCanvas, *, node_id: str, filename: str, content_type: str, data: bytes
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
    return await asset_service.create_artifact(
        session,
        project_id=canvas.project_id,
        type=kind,
        storage_key=relative.as_posix(),
        mime_type=content_type or mimetypes.guess_type(filename)[0] or "",
        meta={"source": "canvas_upload", "canvas_node_id": node_id, "filename": filename[:200]},
    )


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
    return {**out, "defaults": defaults}


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
