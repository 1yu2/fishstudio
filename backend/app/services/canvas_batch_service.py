"""画布批量运行：按依赖顺序在服务端推进整张图（或某节点及其下游）。

- 报价：每个节点复用 quote_node 的口径，合计 = 各节点报价之和；未知价格的节点单独列出，不当作 0。
- 创建：同一画布内 client_request_id 幂等；合计（云端部分）超出今日剩余额度直接拒绝，之后每个任务仍各自预留预算。
- 推进：advance_batch 提交“上游都已完成”的节点；job 结束时由 worker 的 finalize 调用，
  worker 周期循环再兜底扫一遍 running 的批次，防止丢事件。批次状态不依赖浏览器开着。
- 上游失败 / 取消 → 下游 blocked，其余分支继续，批次最终 failed。失败节点由用户用单节点运行重试。
"""

from __future__ import annotations

import copy
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

RUNNABLE = ("image", "video")
# 节点状态：waiting 等上游；running 已提交任务；skipped 已有成功结果而跳过
OPEN_NODE_STATES = ("waiting", "running")
FAILED_NODE_STATES = ("failed", "blocked", "canceled")
SWEEP_LIMIT = 50


class BatchRequest(BaseModel):
    scope: Literal["all", "downstream"] = "all"
    node_id: Optional[str] = Field(default=None, max_length=64)
    skip_succeeded: bool = True


class BatchRunRequest(BatchRequest):
    client_request_id: str = Field(min_length=1, max_length=64)
    # 报价时的画布版本；与当前不一致说明确认前画布被改过，价格不再有效
    quoted_revision: Optional[int] = None


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
    kinds = {node["id"]: node["type"] for node in graph.get("nodes", [])}
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
    kinds = {node["id"]: node["type"] for node in graph.get("nodes", [])}
    children: dict[str, list[str]] = {}
    for edge in graph.get("edges", []):
        if edge.get("source") in kinds and edge.get("target") in kinds:
            children.setdefault(edge["source"], []).append(edge["target"])
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
        members = {node_id for node_id in wanted if kinds[node_id] in RUNNABLE}
    else:
        members = {node_id for node_id, kind in kinds.items() if kind in RUNNABLE}
    if not members:
        raise CanvasError("没有可运行的图片 / 视频节点")

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


async def _quote_plan(
    session: AsyncSession,
    canvas: NodeCanvas,
    request: BatchRequest,
) -> dict[str, Any]:
    graph = copy.deepcopy(canvas.graph or {})
    results, _ = await node_results(session, canvas)
    order, skipped = plan_batch(graph, request, results)
    # 还要生成的节点，其结果在报价时用占位 id 代替，这样依赖它的下游也能估价
    quote_results = {**results}
    for node_id in order:
        if node_id not in skipped:
            quote_results[node_id] = [f"{canvas_service.PENDING_PREFIX}{node_id}"]
    by_id = {node["id"]: node for node in graph.get("nodes", [])}
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
        }
        items.append(item)
        if node_id in skipped:
            item["estimated_cost"] = 0.0
            continue
        fields, blockers = canvas_service.resolve_node_request(
            graph, node_id, quote_results, canvas.aspect_ratio
        )
        if blockers:
            item.update(unavailable=True, reason="；".join(blockers), blockers=blockers)
            continue
        try:
            quote, cloud = await canvas_service.quote_node_with_budget(
                session, canvas, NodeRunRequest(**fields), quote_only=True
            )
        except (CanvasError, ValidationError) as error:
            # 路由 / 参数问题：这个节点实际也跑不起来，和输入缺失一样当作不能运行
            message = str(error).splitlines()[0]
            item.update(unavailable=True, reason=message, blockers=[message])
            continue
        item["model"] = quote.get("model")
        if quote["estimated_cost"] is None:
            item.update(unavailable=True, reason="未配置价格")
            continue
        item["estimated_cost"] = quote["estimated_cost"]
        total += Decimal(str(quote["estimated_cost"]))
        billable += cloud
    cap, remaining = await _remaining_budget(session, canvas.owner_id)
    return {
        "graph": graph,
        "revision": canvas.revision,
        "order": order,
        "skipped": skipped,
        "nodes": items,
        "estimated_total": float(total),
        "billable_total": float(billable),
        "unavailable_nodes": [
            {"node_id": item["node_id"], "label": item["label"], "reason": item["reason"]}
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


def batch_detail(batch: CanvasBatchRun) -> dict[str, Any]:
    counts = _counts(batch)
    return {
        "id": batch.id,
        "canvas_id": batch.canvas_id,
        "status": batch.status,
        "scope": batch.scope,
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
    await canvas_service.advisory_lock(session, f"canvas-batch:{canvas.id}:{request.client_request_id}")
    existing = (
        await session.execute(
            select(CanvasBatchRun).where(
                CanvasBatchRun.canvas_id == canvas.id,
                CanvasBatchRun.client_request_id == request.client_request_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    if request.quoted_revision is not None and request.quoted_revision != canvas.revision:
        raise BatchRevisionConflict("画布在报价后被修改，请按最新内容重新报价后再确认")
    # 与 budget_service.reserve 同一把用户锁：汇总额度检查到首批任务创建之间不会被并发批次插入
    await budget_service.lock_user(session, canvas.owner_id)
    plan = await _quote_plan(session, canvas, request)
    blocked = [item for item in plan["nodes"] if item["blockers"]]
    if blocked:
        first = blocked[0]
        raise CanvasError(f"节点「{first['label']}」无法运行：{first['reason']}")
    if plan["exceeds_budget"]:
        raise CanvasError(
            f"批量运行预计云端花费 ¥{plan['_billable']:.2f}，超出今日剩余额度 ¥{plan['_remaining']:.2f}"
            f"（上限 ¥{plan['_cap']}）。可改用本地预览档，或减少运行的节点"
        )
    batch = CanvasBatchRun(
        canvas_id=canvas.id,
        owner_id=canvas.owner_id,
        client_request_id=request.client_request_id,
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
        graph=plan["graph"],
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
