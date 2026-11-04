"""画布批量运行：按依赖顺序在服务端推进整张图（或某节点及其下游）。

- 报价：每个节点复用 quote_node 的口径，合计 = 各节点报价之和；未知价格的节点单独列出，不当作 0。
- 创建：同一画布内 client_request_id 幂等；合计（云端部分）超出今日剩余额度直接拒绝，之后每个任务仍各自预留预算。
- 推进：advance_batch 提交“上游都已完成”的节点；job 结束时由 worker 的 finalize 调用，
  worker 周期循环再兜底扫一遍 running 的批次，防止丢事件。批次状态不依赖浏览器开着。
- 上游失败 / 取消 → 下游 blocked，其余分支继续，批次最终 failed。失败节点由用户用单节点运行重试。
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from collections import deque
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Artifact, CanvasBatchRun, GenerationJob, NodeCanvas, UsageEvent
from app.services import budget_service
from app.services import node_canvas_service as canvas_service
from app.services.node_canvas_service import CanvasError, NodeRunRequest

logger = logging.getLogger(__name__)

# RUNNABLE：默认批量运行的类型（也是节点之间的依赖类型）；配音（tts）只在 kinds 明确包含时才运行
RUNNABLE = ("image", "video")
ALL_KINDS = ("image", "video", "tts")
# 画布节点类型 → 运行类型（配音节点 audio 对应 tts）
NODE_KIND = {"image": "image", "video": "video", "audio": "tts"}
# 节点状态：waiting 等上游；running 已提交任务；skipped 已有成功结果而跳过
OPEN_NODE_STATES = ("waiting", "running")
FAILED_NODE_STATES = ("failed", "blocked", "canceled")
SWEEP_LIMIT = 50
# 图快照里存放批次 kinds 的保留键（resolve / 提交只读 nodes、edges，不受影响）
KINDS_KEY = "_batch_kinds"


class BatchRequest(BaseModel):
    scope: Literal["all", "downstream"] = "all"
    node_id: Optional[str] = Field(default=None, max_length=64)
    skip_succeeded: bool = True
    # 只运行这些类型的节点（分段生成：先画面、再视频）；None / 两种都选 = 全部
    # 配音节点用 "tts"：不传 kinds 时默认不包含配音，生成配音要明确选 tts
    kinds: Optional[list[Literal["image", "video", "tts"]]] = None


class BatchRunRequest(BatchRequest):
    client_request_id: str = Field(min_length=1, max_length=64)
    # 报价时的画布版本；与当前不一致说明确认前画布被改过，价格不再有效
    quoted_revision: Optional[int] = None


def effective_kinds(request: BatchRequest) -> Optional[list[str]]:
    """规范化 kinds：去重排序；空或恰好是图片 + 视频时返回 None（等同不限制，保持原有口径）。"""
    chosen = sorted(set(request.kinds or []))
    if not chosen or set(chosen) == set(RUNNABLE):
        return None
    return chosen


KEY_PREFIX = "v2:"


def _idempotency_key(request: BatchRunRequest) -> str:
    """幂等 key = "v2:" + sha1(规范 JSON [client_request_id, 排序后的 kinds 或 "all"])。

    JSON 编码对这个二元组是单射，所以不同 (id, kinds) 只能靠 sha1 碰撞才会同 key，不会像字符串拼接那样
    让 id 本身含分隔符的请求撞上别的批次。固定前缀 v2: 把新 key 与改动前的原始 id（旧批次直接存原始 id）区分开。
    """
    kinds = effective_kinds(request)
    canonical = json.dumps([request.client_request_id, kinds or "all"], ensure_ascii=False)
    return KEY_PREFIX + hashlib.sha1(canonical.encode()).hexdigest()


def _lookup_keys(request: BatchRunRequest) -> list[str]:
    """查已有批次用的 key：不限 kinds 时兼容改动前存的原始 id。
    旧批次都是不限 kinds 的，且原始 id 不会以 v2: 开头（前端是 uuid）；以 v2: 开头的原始 id 不做旧值查找，
    这样旧值查找永远撞不上任何新 key。
    """
    keys = [_idempotency_key(request)]
    if effective_kinds(request) is None and not request.client_request_id.startswith(KEY_PREFIX):
        keys.append(request.client_request_id)
    return keys


class BatchRevisionConflict(CanvasError):
    """确认的报价不是针对当前画布内容的。"""


# ── 结果与计划 ────────────────────────────────────────────────────────────


async def node_results(
    session: AsyncSession, canvas: NodeCanvas
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """每个节点的结果 artifact id（最新在前，顺序同 node_runs：先生成结果，再上传），以及 job_id → artifact id。"""
    jobs = (
        await session.execute(
            select(GenerationJob.id, GenerationJob.params)
            .where(GenerationJob.project_id == canvas.project_id)
            .order_by(GenerationJob.created_at.desc())
        )
    ).all()
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
    by_job: dict[str, list[str]] = {}
    for artifact in artifacts:
        if artifact.job_id:
            by_job.setdefault(artifact.job_id, []).append(artifact.id)
    results: dict[str, list[str]] = {}
    for job_id, params in jobs:
        node_id = (params or {}).get("canvas_node_id")
        if node_id and by_job.get(job_id):
            results.setdefault(node_id, []).extend(by_job[job_id])
    for artifact in artifacts:
        node_id = (artifact.artifact_metadata or {}).get("canvas_node_id")
        if (
            not artifact.job_id
            and (artifact.artifact_metadata or {}).get("source") == "canvas_upload"
            and node_id
        ):
            results.setdefault(node_id, []).append(artifact.id)
    return results, by_job


def _check_acyclic(graph: dict[str, Any]) -> None:
    ids = {node["id"] for node in graph.get("nodes", [])}
    indegree = {node_id: 0 for node_id in ids}
    children: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for edge in graph.get("edges", []):
        if edge.get("source") in ids and edge.get("target") in ids:
            children[edge["source"]].append(edge["target"])
            indegree[edge["target"]] += 1
    queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    seen = 0
    while queue:
        current = queue.popleft()
        seen += 1
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if seen != len(ids):
        raise CanvasError("画布里有循环连线，无法按依赖顺序运行")


def _predecessors(graph: dict[str, Any], members: set[str]) -> dict[str, list[str]]:
    """批次内节点 → 它依赖的批次内图片 / 视频节点（文本节点只提供提示词，不算依赖）。"""
    kinds = {node["id"]: NODE_KIND.get(node["type"], node["type"]) for node in graph.get("nodes", [])}
    preds: dict[str, list[str]] = {node_id: [] for node_id in members}
    for edge in graph.get("edges", []):
        source, target = edge.get("source"), edge.get("target")
        if (
            target in members
            and source in members
            and kinds.get(source) in RUNNABLE
            and source not in preds[target]
        ):
            preds[target].append(source)
    return preds


def plan_batch(
    graph: dict[str, Any],
    request: BatchRequest,
    results: dict[str, list[str]],
) -> tuple[list[str], set[str]]:
    """返回 (拓扑顺序的节点 id, 其中被跳过的节点 id)。有环抛 CanvasError。"""
    _check_acyclic(graph)
    kinds = {node["id"]: NODE_KIND.get(node["type"], node["type"]) for node in graph.get("nodes", [])}
    children: dict[str, list[str]] = {}
    for edge in graph.get("edges", []):
        # voice 边只表示台词属于哪个镜头，不是生成依赖
        if edge.get("targetHandle") == "voice":
            continue
        if edge.get("source") in kinds and edge.get("target") in kinds:
            children.setdefault(edge["source"], []).append(edge["target"])
    runnable = set(effective_kinds(request) or RUNNABLE)
    if request.scope == "downstream":
        if request.node_id not in kinds:
            raise CanvasError("节点不存在，可能还没保存")
        wanted, stack = set(), [request.node_id]
        while stack:
            current = stack.pop()
            if current in wanted:
                continue
            wanted.add(current)
            stack.extend(children.get(current, []))
        members = {node_id for node_id in wanted if kinds[node_id] in runnable}
    else:
        members = {node_id for node_id, kind in kinds.items() if kind in runnable}
    if not members:
        raise CanvasError("没有可运行的配音节点" if runnable == {"tts"} else "没有可运行的图片 / 视频节点")

    preds = _predecessors(graph, members)
    # 稳定的拓扑序：按图里节点出现的顺序取“上游已排好”的节点
    position = {node["id"]: index for index, node in enumerate(graph.get("nodes", []))}
    remaining = sorted(members, key=lambda node_id: position[node_id])
    order: list[str] = []
    placed: set[str] = set()
    while remaining:
        ready = [node_id for node_id in remaining if all(pred in placed for pred in preds[node_id])]
        order.extend(ready)
        placed.update(ready)
        remaining = [node_id for node_id in remaining if node_id not in placed]

    # 已有成功结果的节点可跳过；但被重跑的上游（含指定的起点）会让依赖它的节点结果过期，不能跳过
    skipped: set[str] = set()
    if request.skip_succeeded:
        for node_id in order:
            stale = any(pred not in skipped for pred in preds[node_id])
            if (
                results.get(node_id)
                and not stale
                and not (request.scope == "downstream" and node_id == request.node_id)
            ):
                skipped.add(node_id)
    return order, skipped


# ── 报价 ──────────────────────────────────────────────────────────────────


async def _remaining_budget(
    session: AsyncSession, owner_id: int
) -> tuple[Optional[Decimal], Optional[Decimal]]:
    """(每日上限, 今日剩余额度)；不限额时两者都是 None。"""
    cap = budget_service.daily_cap()
    if cap is None:
        return None, None
    spent = await budget_service.spent_today(session, owner_id)
    pending = await budget_service.in_flight(session, owner_id)
    return cap, max(Decimal(0), cap - spent - pending)


async def _active_tts_nodes(session: AsyncSession, canvas: NodeCanvas) -> set[str]:
    """画布里已经有配音任务在排队 / 运行的节点 id。"""
    rows = await session.execute(
        select(GenerationJob.params["canvas_node_id"].astext).where(
            GenerationJob.project_id == canvas.project_id,
            GenerationJob.operation == "tts",
            GenerationJob.status.in_(canvas_service.ACTIVE_JOB_STATUSES),
        )
    )
    return {node_id for (node_id,) in rows.all() if node_id}


async def _quote_plan(
    session: AsyncSession,
    canvas: NodeCanvas,
    request: BatchRequest,
) -> dict[str, Any]:
    graph = copy.deepcopy(canvas.graph or {})
    results, _ = await node_results(session, canvas)
    order, skipped = plan_batch(graph, request, results)
    by_id = {node["id"]: node for node in graph.get("nodes", [])}
    # 配音节点：台词为空 / 超长的先跳过（报价里显示为不能运行，但不拦其他台词）；已经有任务在跑的也跳过，避免重复生成、重复收费
    unrunnable_audio: dict[str, list[str]] = {}
    active_tts: set[str] = set()
    if any(by_id[node_id]["type"] == "audio" for node_id in order):
        active_tts = await _active_tts_nodes(session, canvas)
        for node_id in order:
            if by_id[node_id]["type"] != "audio" or node_id in skipped:
                continue
            _, audio_blockers = canvas_service.resolve_node_request(
                graph, node_id, results, canvas.aspect_ratio
            )
            if audio_blockers:
                unrunnable_audio[node_id] = audio_blockers
        skipped = (
            skipped
            | set(unrunnable_audio)
            | {node_id for node_id in order if node_id in active_tts and by_id[node_id]["type"] == "audio"}
        )
    # 还要生成的节点，其结果在报价时用占位 id 代替，这样依赖它的下游也能估价
    quote_results = {**results}
    for node_id in order:
        if node_id not in skipped:
            quote_results[node_id] = [f"{canvas_service.PENDING_PREFIX}{node_id}"]
    kinds_by_id = {node_id: NODE_KIND.get(node["type"], node["type"]) for node_id, node in by_id.items()}
    kinds = effective_kinds(request)
    preds = _predecessors(graph, set(order))
    # 不会提交、但也不拦整个批次的节点（分段运行缺批次外上游）；它的批次内下游推进时同样会被 blocked
    stalled: set[str] = set()
    items: list[dict[str, Any]] = []
    total = Decimal(0)
    billable = Decimal(0)
    for node_id in order:
        node = by_id[node_id]
        item: dict[str, Any] = {
            "node_id": node_id,
            "label": canvas_service.node_label(node),
            "kind": node["type"],
            "status": "skipped" if node_id in skipped else "pending",
            "estimated_cost": None,
            "unavailable": False,
            "reason": "",
            "blockers": [],
            "upstream_missing_only": False,
            "blocks_batch": False,
            "waits_upstream": False,
        }
        items.append(item)
        if node_id in unrunnable_audio:
            reason = "；".join(unrunnable_audio[node_id])
            item.update(
                estimated_cost=None,
                unavailable=True,
                reason=f"{reason}，先跳过",
                blockers=unrunnable_audio[node_id],
            )
            continue
        if node_id in skipped:
            item["estimated_cost"] = 0.0
            if node_id in active_tts:
                item["reason"] = "正在生成，先跳过"
            continue
        stalled_up = [up for up in preds.get(node_id, []) if up in stalled]
        if stalled_up:
            # order 是拓扑序，上游已判定过；报价用的占位结果不会真的产生
            upstream = canvas_service.node_label(by_id[stalled_up[0]])
            item.update(
                unavailable=True, waits_upstream=True, reason=f"上游「{upstream}」这次不会运行，先跳过"
            )
            stalled.add(node_id)
            continue
        missing: list[str] = []
        fields, blockers = canvas_service.resolve_node_request(
            graph, node_id, quote_results, canvas.aspect_ratio, missing
        )
        if blockers:
            # 结构化判断：每条 blocker 都来自“批次外、还没有结果的上游图片 / 视频节点”
            outside = len(missing) == len(blockers) and all(
                up not in order and kinds_by_id.get(up) in RUNNABLE for up in missing
            )
            # 分段运行时，缺的只是批次外上游结果的节点自己先跳过（blocked），不拦整个批次
            blocks_batch = not (kinds and outside)
            item.update(
                unavailable=True,
                reason="；".join(blockers),
                blockers=blockers,
                upstream_missing_only=outside,
                blocks_batch=blocks_batch,
            )
            if not blocks_batch:
                stalled.add(node_id)
            continue
        try:
            quote, cloud = await canvas_service.quote_node_with_budget(
                session, canvas, NodeRunRequest(**fields), quote_only=True
            )
        except (CanvasError, ValidationError) as error:
            # 路由 / 参数问题：这个节点实际也跑不起来，和输入缺失一样当作不能运行
            message = str(error).splitlines()[0]
            item.update(unavailable=True, reason=message, blockers=[message], blocks_batch=True)
            continue
        item["model"] = quote.get("model")
        if quote["estimated_cost"] is None:
            item.update(unavailable=True, reason="未配置价格")
            continue
        item["estimated_cost"] = quote["estimated_cost"]
        total += Decimal(str(quote["estimated_cost"]))
        billable += cloud
    pending = [item for item in items if item["status"] == "pending"]
    will_run = [item for item in pending if not item["blockers"] and not item["waits_upstream"]]
    cap, remaining = await _remaining_budget(session, canvas.owner_id)
    return {
        "graph": graph,
        "revision": canvas.revision,
        "order": order,
        "skipped": skipped,
        "nodes": items,
        "estimated_total": float(total),
        "billable_total": float(billable),
        # 真正会提交任务的节点数；缺上游先跳过的、拦住整个批次的分别计数
        "runnable_count": len(will_run),
        "waiting_count": sum(1 for item in pending if item["node_id"] in stalled),
        "blocking_count": sum(1 for item in pending if item["blocks_batch"]),
        "unavailable_nodes": [
            {
                "node_id": item["node_id"],
                "label": item["label"],
                "reason": item["reason"],
                # runs=True：只是价格未知，照常运行；blocks_batch=True：要先处理才能运行整个批次
                "runs": not item["blockers"] and not item["waits_upstream"],
                "blocks_batch": item["blocks_batch"],
            }
            for item in items
            if item["unavailable"]
        ],
        "budget": {
            "cap": float(cap) if cap is not None else None,
            "remaining": float(remaining) if remaining is not None else None,
        },
        "exceeds_budget": remaining is not None and billable > remaining,
        "_billable": billable,
        "_total": total,
        "_remaining": remaining,
        "_cap": cap,
    }


def _public(quote: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in quote.items()
        if not key.startswith("_") and key not in ("order", "skipped", "graph")
    }


async def batch_quote(session: AsyncSession, canvas: NodeCanvas, request: BatchRequest) -> dict[str, Any]:
    return _public(await _quote_plan(session, canvas, request))


# ── 创建 / 查询 ───────────────────────────────────────────────────────────


def _counts(batch: CanvasBatchRun) -> dict[str, int]:
    counts: dict[str, int] = {}
    for state in batch.nodes.values():
        counts[state["status"]] = counts.get(state["status"], 0) + 1
    return counts


def batch_kinds(batch: CanvasBatchRun) -> Optional[list[str]]:
    """批次限定的节点类型；存在图快照的保留键里（不新增列 / 迁移），None = 全部。"""
    return (batch.graph or {}).get(KINDS_KEY)


def batch_detail(batch: CanvasBatchRun) -> dict[str, Any]:
    counts = _counts(batch)
    return {
        "id": batch.id,
        "canvas_id": batch.canvas_id,
        "status": batch.status,
        "scope": batch.scope,
        "kinds": batch_kinds(batch),
        "root_node_id": batch.root_node_id,
        "client_request_id": batch.client_request_id,
        "node_ids": list(batch.node_ids),
        "nodes": batch.nodes,
        "counts": counts,
        "total": len(batch.node_ids),
        "completed": counts.get("succeeded", 0) + counts.get("skipped", 0),
        "failed": counts.get("failed", 0) + counts.get("blocked", 0),
        "estimated_total": float(batch.estimated_total),
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "updated_at": batch.updated_at.isoformat() if batch.updated_at else None,
    }


async def get_batch(session: AsyncSession, canvas: NodeCanvas, batch_id: str) -> CanvasBatchRun:
    batch = await session.get(CanvasBatchRun, batch_id)
    if batch is None or batch.canvas_id != canvas.id:
        raise LookupError("批量运行不存在")
    return batch


async def list_batches(session: AsyncSession, canvas: NodeCanvas, limit: int = 10) -> list[CanvasBatchRun]:
    rows = await session.execute(
        select(CanvasBatchRun)
        .where(CanvasBatchRun.canvas_id == canvas.id)
        .order_by(CanvasBatchRun.created_at.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())


async def create_batch(session: AsyncSession, canvas: NodeCanvas, request: BatchRunRequest) -> CanvasBatchRun:
    """创建批次并立即提交输入已就绪的节点。同一 client_request_id 返回同一个批次。"""
    key = _idempotency_key(request)
    await canvas_service.advisory_lock(session, f"canvas-batch:{canvas.id}:{key}")
    existing = (
        (
            await session.execute(
                select(CanvasBatchRun)
                .where(
                    CanvasBatchRun.canvas_id == canvas.id,
                    CanvasBatchRun.client_request_id.in_(_lookup_keys(request)),
                )
                .order_by(CanvasBatchRun.created_at)
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing

    if request.quoted_revision is not None and request.quoted_revision != canvas.revision:
        raise BatchRevisionConflict("画布在报价后被修改，请按最新内容重新报价后再确认")
    # 与 budget_service.reserve 同一把用户锁：汇总额度检查到首批任务创建之间不会被并发批次插入
    await budget_service.lock_user(session, canvas.owner_id)
    plan = await _quote_plan(session, canvas, request)
    kinds = effective_kinds(request)
    blocked = [item for item in plan["nodes"] if item["blocks_batch"]]
    if blocked:
        first = blocked[0]
        raise CanvasError(f"节点「{first['label']}」无法运行：{first['reason']}")
    if plan["runnable_count"] == 0 and plan["waiting_count"] > 0:
        raise CanvasError("没有可以运行的节点：它们的上游图片都还没有结果，先生成画面")
    if plan["exceeds_budget"]:
        raise CanvasError(
            f"批量运行预计云端花费 ¥{plan['_billable']:.2f}，超出今日剩余额度 ¥{plan['_remaining']:.2f}"
            f"（上限 ¥{plan['_cap']}）。可改用本地预览档，或减少运行的节点"
        )
    batch = CanvasBatchRun(
        canvas_id=canvas.id,
        owner_id=canvas.owner_id,
        client_request_id=key,
        scope=request.scope,
        root_node_id=request.node_id if request.scope == "downstream" else None,
        skip_succeeded=request.skip_succeeded,
        status="running",
        node_ids=plan["order"],
        nodes={
            node_id: {
                "status": "skipped" if node_id in plan["skipped"] else "waiting",
                "job_id": None,
                "reason": "",
            }
            for node_id in plan["order"]
        },
        estimated_total=plan["_total"],
        graph={**plan["graph"], KINDS_KEY: kinds} if kinds else plan["graph"],
        aspect_ratio=canvas.aspect_ratio,
    )
    session.add(batch)
    await session.flush()
    await advance_batch(session, batch.id)
    return batch


# ── 推进 ──────────────────────────────────────────────────────────────────


def _sync_from_jobs(nodes: dict[str, dict[str, Any]], jobs: dict[str, GenerationJob]) -> None:
    for state in nodes.values():
        if state["status"] != "running":
            continue
        job = jobs.get(state["job_id"])
        if job is None:
            continue
        if job.status == "succeeded":
            state["status"] = "succeeded"
        elif job.status == "failed":
            state.update(status="failed", reason=(job.error or "生成失败")[:300])
        elif job.status == "canceled":
            state.update(status="canceled", reason="任务已取消")


async def advance_batch(
    session: AsyncSession, batch_id: str, *, skip_locked: bool = False
) -> Optional[CanvasBatchRun]:
    """同步已提交任务的状态，提交上游都已完成的节点，全部结束时收尾。调用方负责 commit。

    行锁串行化 worker 的 finalize 与周期兜底，避免同一节点被提交两次（另有幂等 key 兜底）。
    """
    batch = (
        await session.execute(
            select(CanvasBatchRun)
            .where(CanvasBatchRun.id == batch_id)
            .with_for_update(skip_locked=skip_locked)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if batch is None:
        return None
    nodes = copy.deepcopy(batch.nodes)
    job_ids = [state["job_id"] for state in nodes.values() if state.get("job_id")]
    jobs: dict[str, GenerationJob] = {}
    if job_ids:
        rows = await session.execute(
            select(GenerationJob)
            .where(GenerationJob.id.in_(job_ids))
            .execution_options(populate_existing=True)
        )
        jobs = {job.id: job for job in rows.scalars().all()}
    _sync_from_jobs(nodes, jobs)

    if batch.status == "running":
        canvas = await session.get(NodeCanvas, batch.canvas_id)
        if canvas is not None:
            await _submit_ready(session, batch, canvas, nodes, jobs)
        if not any(state["status"] in OPEN_NODE_STATES for state in nodes.values()):
            batch.status = _final_status(nodes)
            batch.nodes = nodes
            await session.flush()
            await _log_finished(session, batch, jobs)
            return batch
    batch.nodes = nodes
    await session.flush()
    return batch


def _final_status(nodes: dict[str, dict[str, Any]]) -> str:
    states = {state["status"] for state in nodes.values()}
    if states & {"failed", "blocked"}:
        return "failed"
    if "canceled" in states:
        return "canceled"
    return "succeeded"


async def _submit_ready(
    session: AsyncSession,
    batch: CanvasBatchRun,
    canvas: NodeCanvas,
    nodes: dict[str, dict[str, Any]],
    jobs: dict[str, GenerationJob],
) -> None:
    graph = batch.graph or {}
    preds = _predecessors(graph, set(batch.node_ids))
    results, by_job = await node_results(session, canvas)
    # 本批次已重新生成的节点，下游一律用本次的结果，而不是用户之前点选的旧图
    resolve_graph = copy.deepcopy(graph)
    fresh = {
        node_id: by_job[state["job_id"]][0]
        for node_id, state in nodes.items()
        if state["status"] == "succeeded" and by_job.get(state.get("job_id") or "")
    }
    for node in resolve_graph.get("nodes", []):
        if node["id"] in fresh:
            node.setdefault("data", {})["selected"] = fresh[node["id"]]
    for node_id in batch.node_ids:
        state = nodes[node_id]
        if state["status"] != "waiting":
            continue
        upstream = [nodes[pred]["status"] for pred in preds.get(node_id, [])]
        if any(status in FAILED_NODE_STATES for status in upstream):
            state.update(status="blocked", reason="上游节点未成功")
            continue
        if any(status not in ("succeeded", "skipped") for status in upstream):
            continue
        try:
            fields, blockers = canvas_service.resolve_node_request(
                resolve_graph, node_id, results, batch.aspect_ratio
            )
        except CanvasError as error:
            state.update(status="blocked", reason=str(error))
            continue
        if blockers:
            state.update(status="blocked", reason="；".join(blockers))
            continue
        try:
            request = NodeRunRequest(**fields, client_request_id=f"batch:{batch.id}")
            # 保存点：提交失败（预算不足等）只回滚这一个节点的写入
            async with session.begin_nested():
                job, _ = await canvas_service._create_node_job(
                    session, canvas, node_id, request, {"batch_run_id": batch.id}, graph
                )
        except (CanvasError, ValidationError) as error:
            state.update(status="failed", reason=str(error).splitlines()[0][:300])
            continue
        state.update(status="running", job_id=job.id, reason="")
        jobs[job.id] = job


async def _log_finished(session: AsyncSession, batch: CanvasBatchRun, jobs: dict[str, GenerationJob]) -> None:
    """批次结束时写一条结构化日志，便于按 batch_id 对账。"""
    counts = _counts(batch)
    actual = await session.scalar(
        select(
            func.coalesce(func.sum(func.coalesce(UsageEvent.actual_cost, UsageEvent.estimated_cost)), 0)
        ).where(UsageEvent.job_id.in_(list(jobs) or [""]))
    )
    record = {
        "batch_id": batch.id,
        "canvas_id": batch.canvas_id,
        "status": batch.status,
        "node_count": len(batch.node_ids),
        "succeeded": counts.get("succeeded", 0),
        "failed": counts.get("failed", 0) + counts.get("blocked", 0),
        "skipped": counts.get("skipped", 0),
        "canceled": counts.get("canceled", 0),
        "estimated_total": str(batch.estimated_total),
        "actual_total": str(Decimal(str(actual or 0))),
    }
    logger.info(
        "canvas_batch_finished %s", json.dumps(record, ensure_ascii=False), extra={"canvas_batch": record}
    )


async def advance_for_job(session: AsyncSession, job: GenerationJob) -> None:
    """worker 的 job 终态入口：任务属于某个批次时推进它。失败只记日志，不影响任务本身的收尾。"""
    batch_id = (job.params or {}).get("batch_run_id")
    if not batch_id:
        return
    try:
        async with session.begin_nested():
            await advance_batch(session, batch_id)
    except Exception:
        logger.warning("推进画布批量运行失败（周期兜底会重试）：batch=%s", batch_id, exc_info=True)


async def sweep_running(session: AsyncSession) -> int:
    """worker 周期兜底：推进所有 running 的批次，返回处理个数。"""
    ids = (
        (
            await session.execute(
                select(CanvasBatchRun.id).where(CanvasBatchRun.status == "running").limit(SWEEP_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    for batch_id in ids:
        try:
            async with session.begin_nested():
                await advance_batch(session, batch_id, skip_locked=True)
        except Exception:
            logger.warning("兜底推进画布批量运行失败：batch=%s", batch_id, exc_info=True)
        await session.commit()
    return len(ids)


# ── 取消 ──────────────────────────────────────────────────────────────────


async def cancel_batch(session: AsyncSession, batch_id: str) -> list[str]:
    """取消批次：未开始的节点直接标 canceled，批次置 canceled；返回仍需取消的进行中 job id。调用方 commit 后逐个取消 job。"""
    batch = (
        await session.execute(
            select(CanvasBatchRun)
            .where(CanvasBatchRun.id == batch_id)
            .with_for_update()
            # 路由层可能已加载过该批次；必须在锁下重读，否则会覆盖 worker 刚提交的节点状态
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if batch.status != "running":
        return []
    nodes = copy.deepcopy(batch.nodes)
    running: list[str] = []
    for state in nodes.values():
        if state["status"] == "waiting":
            state.update(status="canceled", reason="批量运行已取消")
        elif state["status"] == "running" and state.get("job_id"):
            running.append(state["job_id"])
    batch.nodes = nodes
    batch.status = "canceled"
    await session.flush()
    return running
