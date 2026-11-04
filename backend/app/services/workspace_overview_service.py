"""工作台总览：当前用户所有节点画布的进度、在跑任务、待确认与失败项（只读聚合）。

数据来自 NodeCanvas（图 JSON）、GenerationJob（params 里的 canvas_id / canvas_node_id）、
Artifact、UsageEvent 与 budget_service；全部按“批量一次查询”取出后在内存里分组，不对项目 N+1。

定义：
- running：状态 pending / queued / leased / running 的画布任务（一个任务一条）。
- failed：节点**最新**一个任务失败；之后又成功（或又在跑）的不算。
- needs_review：图片 / 视频节点最新任务成功，且节点没有 selected（no_selection），
  或最新成功结果比当前 selected 的产物更新（new_result）。
- finished_this_week：最近 7 天成功的渲染类任务（operation 以 render 开头，如 render_episode）。
- 管理员也只看自己的数据（owner_id == 当前用户）。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, NamedTuple, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Artifact, GenerationJob, NodeCanvas, SeriesProject, UsageEvent, User
from app.services import budget_service, canvas_script_service, character_library_service
from app.services.cost_report_service import _effective_cost

MAX_PROJECTS = 20
MAX_LIST = 20
RUNNING_STATUSES = ("pending", "queued", "leased", "running")
CANVAS_OPERATIONS = ("generate_image", "generate_video", "tts")
NODE_KIND_LABEL = {"text": "文本", "image": "图片", "video": "视频", "audio": "配音"}
STEP_LABELS = (
    ("script", "剧本"),
    ("characters", "角色"),
    ("storyboard", "分镜"),
    ("voice", "配音"),
    ("final", "成片"),
)


def _money(value: Any) -> str:
    return f"{Decimal(str(value or 0)):.2f}"


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _node_label(node: dict[str, Any]) -> str:
    label = str((node.get("data") or {}).get("label") or "").strip()
    return label or NODE_KIND_LABEL.get(node.get("type"), "节点")


def _readable_error(error: str) -> str:
    """任务错误取第一行并截断；没有错误文本时给通用提示。"""
    first = (error or "").strip().splitlines()[0:1]
    text = first[0].strip() if first else ""
    return text[:120] or "生成失败，请重试"


def _display_status(status: str, external_task_id: str) -> str:
    """pending + 外部任务 ID 是已提交给供应商的任务，按生成中展示。"""
    if status in ("leased", "running") or (status == "pending" and external_task_id):
        return "running"
    return "queued"


def _steps(
    shots_done: int,
    shots_total: int,
    has_script: bool,
    characters: Optional[list[dict[str, Any]]] = None,
    voice: Optional[tuple[int, int]] = None,
) -> list[dict[str, str]]:
    """五步进度。characters 是各角色的 {name, final}；剧本看文本节点，分镜看镜头完成数，其余待后续任务补全。

    voice 是 (配音节点数, 已有结果的配音节点数)：有配音节点 → 全部有结果才完成；
    没有配音节点（0, 0）→ 分镜完成就算完成（配音可跳过）。不传表示不统计配音，配音永远不算完成（旧口径）。

    角色步骤：没有角色 → 有剧本即算完成（可跳过）；有角色且全部定稿 → 完成；否则进行中（不要求剧本完成）。
    进行中的顺序：剧本 → 角色 → 分镜 → 配音。
    """
    characters = characters or []
    characters_pending = any(not item["final"] for item in characters)
    done = {
        "script": has_script,
        "characters": (not characters_pending) if characters else has_script,
        "storyboard": shots_total > 0 and shots_done >= shots_total,
        "voice": False,
        "final": False,
    }
    storyboard_done = done["storyboard"]
    if voice is not None:
        voice_total, voice_done = voice
        done["voice"] = voice_done >= voice_total if voice_total else storyboard_done
    # 成片目前没有数据；
    # 有镜头：分镜未完成 → 分镜进行中，分镜完成 → 配音进行中（配音完成或可跳过 → 成片进行中）；没有镜头：先剧本再分镜；角色待定稿时角色优先（不要求剧本）
    if shots_total > 0:
        active = ("final" if done["voice"] else "voice") if storyboard_done else "storyboard"
    else:
        active = "storyboard" if has_script else "script"
    # 有未定稿的角色时，角色一定先于分镜 / 配音；剧本没写（直接做镜头）不强制回到剧本步骤
    if characters_pending:
        active = "characters"
    steps: list[dict[str, str]] = []
    for key, label in STEP_LABELS:
        status = "done" if done[key] else "active" if key == active else "todo"
        text = f"{label} {shots_done}/{shots_total}" if key == "storyboard" and shots_total else label
        steps.append({"key": key, "status": status, "label": text})
    return steps


def _next_hint(
    failed: int,
    review: int,
    shots_done: int,
    shots_total: int,
    pending_characters: Optional[list[str]] = None,
    voice_pending: int = 0,
) -> str:
    if pending_characters:
        names = "、".join(pending_characters[:3])
        return f"还有 {len(pending_characters)} 个角色没定稿：{names}"
    if failed:
        return f"处理 {failed} 个失败镜头"
    if review:
        return f"为 {review} 个镜头选图"
    if shots_total == 0:
        return "添加第一个镜头"
    if shots_done < shots_total:
        return "继续生成剩余镜头"
    if voice_pending:
        return f"还有 {voice_pending} 句台词没配音"
    return "去配音"


_CANVAS_COLUMNS = (
    NodeCanvas.id,
    NodeCanvas.project_id,
    NodeCanvas.title,
    NodeCanvas.aspect_ratio,
    NodeCanvas.updated_at,
    NodeCanvas.graph,
)


class _Collected(NamedTuple):
    """一批画布的聚合结果（overview 与单项目接口共用同一份规则）。"""

    jobs: list[Any]
    costs: dict[str, Decimal]
    stats: dict[str, dict[str, Any]]
    review_by_project: dict[str, list[dict[str, Any]]]
    failed_by_project: dict[str, list[dict[str, Any]]]


async def _collect(session: AsyncSession, canvases: list[Any]) -> _Collected:
    """按给定画布一次性查出 jobs / artifacts / cost，并在内存里算出各项目的镜头统计、失败与待确认。"""
    project_ids = [canvas.project_id for canvas in canvases]
    # 有结构化剧本（至少一块非空）的画布，一次批量查出
    scripted = await canvas_script_service.scripted_canvas_ids(session, [canvas.id for canvas in canvases])

    jobs: list[Any] = []
    artifacts: list[Any] = []
    costs: dict[str, Decimal] = {}
    if project_ids:
        node_id = GenerationJob.params["canvas_node_id"].astext
        jobs = (
            await session.execute(
                select(
                    GenerationJob.id,
                    GenerationJob.project_id,
                    GenerationJob.status,
                    GenerationJob.progress,
                    GenerationJob.error,
                    GenerationJob.external_task_id,
                    GenerationJob.operation,
                    GenerationJob.created_at,
                    node_id.label("node_id"),
                )
                .where(
                    GenerationJob.project_id.in_(project_ids),
                    GenerationJob.operation.in_(CANVAS_OPERATIONS),
                    node_id.isnot(None),
                )
                .order_by(GenerationJob.created_at.desc())
            )
        ).all()
        artifacts = (
            await session.execute(
                select(
                    Artifact.id,
                    Artifact.project_id,
                    Artifact.job_id,
                    Artifact.type,
                    Artifact.created_at,
                    Artifact.artifact_metadata["canvas_node_id"].astext.label("upload_node_id"),
                ).where(Artifact.project_id.in_(project_ids), Artifact.type.in_(("image", "video", "audio")))
            )
        ).all()
        costs = {
            row.project_id: Decimal(row.total or 0)
            for row in (
                await session.execute(
                    select(UsageEvent.project_id, func.sum(_effective_cost()).label("total"))
                    .where(UsageEvent.project_id.in_(project_ids))
                    .group_by(UsageEvent.project_id)
                )
            ).all()
        }

    # 任务按创建时间倒序，首个即该节点最新任务
    latest_job: dict[tuple[str, str], Any] = {}
    job_node: dict[str, tuple[str, str]] = {}
    for job in jobs:
        key = (job.project_id, job.node_id)
        latest_job.setdefault(key, job)
        job_node[job.id] = key
    succeeded_jobs = {job.id for job in jobs if job.status == "succeeded"}

    artifact_time = {artifact.id: artifact.created_at for artifact in artifacts}
    # 节点的成功结果：成功任务的产物 + 上传到该节点的素材
    node_results: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for artifact in artifacts:
        if artifact.job_id in succeeded_jobs:
            node_results[job_node[artifact.job_id]].append(artifact)
        elif not artifact.job_id and artifact.upload_node_id:
            node_results[(artifact.project_id, artifact.upload_node_id)].append(artifact)

    images_by_project: dict[str, list[Any]] = defaultdict(list)
    for artifact in artifacts:
        if artifact.type == "image":
            images_by_project[artifact.project_id].append(artifact)

    review_by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
    failed_by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stats: dict[str, dict[str, Any]] = {}
    for canvas in canvases:
        nodes = (canvas.graph or {}).get("nodes", [])
        media = [node for node in nodes if node.get("type") in ("image", "video")]
        graph_edges = (canvas.graph or {}).get("edges", [])
        # 角色图不是镜头，也不进“待选图”（角色定稿看角色步骤）
        character_ids = {
            n["id"] for n in media if character_library_service.is_character_node(n, graph_edges)
        }
        # 配音节点：台词为空的（自己没写、也没连文本节点）不计入配音步骤，也不是批量可运行的
        texts = {n["id"]: str((n.get("data") or {}).get("text") or "").strip() for n in nodes}
        linked: dict[str, str] = defaultdict(str)
        for edge in graph_edges:
            if edge.get("targetHandle") == "prompt" and texts.get(edge.get("source")):
                linked[edge["target"]] += texts[edge["source"]]
        audio_nodes = [
            node
            for node in nodes
            if node.get("type") == "audio" and (linked.get(node["id"]) or texts[node["id"]])
        ]
        videos = [node for node in media if node["type"] == "video"]
        shot_nodes = videos or [n for n in media if n["type"] == "image" and n["id"] not in character_ids]
        has_script = canvas.id in scripted or any(
            node.get("type") == "text" and str((node.get("data") or {}).get("text") or "").strip()
            for node in nodes
        )
        selected_ids: list[str] = []
        for node in media:
            key = (canvas.project_id, node["id"])
            label = _node_label(node)
            latest = latest_job.get(key)
            selected = (node.get("data") or {}).get("selected")
            if selected in artifact_time:
                selected_ids.append(selected)
            if latest is None:
                continue
            if latest.status == "failed":
                failed_by_project[canvas.project_id].append(
                    {
                        "job_id": latest.id,
                        "canvas_id": canvas.id,
                        "project_title": canvas.title,
                        "node_label": label,
                        "error": _readable_error(latest.error),
                    }
                )
            elif latest.status == "succeeded" and node["id"] not in character_ids:
                generated = [a for a in node_results.get(key, []) if a.job_id]
                if not generated:
                    continue
                newest = max(a.created_at for a in generated)
                reason = None
                if selected not in artifact_time:
                    reason = "no_selection"
                elif newest > artifact_time[selected]:
                    reason = "new_result"
                if reason:
                    review_by_project[canvas.project_id].append(
                        {
                            "canvas_id": canvas.id,
                            "project_title": canvas.title,
                            "node_id": node["id"],
                            "node_label": label,
                            "reason": reason,
                        }
                    )
        # 封面：优先最近的选中图，其次最近一张成功生成的图片
        images = {a.id: a.created_at for a in images_by_project[canvas.project_id]}
        chosen = [aid for aid in selected_ids if aid in images]
        generated = [a.id for a in images_by_project[canvas.project_id] if a.job_id in succeeded_jobs]
        pool = chosen or generated
        cover = max(pool, key=lambda aid: images[aid]) if pool else None
        done = sum(1 for node in shot_nodes if node_results.get((canvas.project_id, node["id"])))
        stats[canvas.project_id] = {
            "cover": cover,
            "shots_total": len(shot_nodes),
            "shots_done": done,
            "has_script": has_script,
            # (配音节点数, 已有结果的配音节点数)
            "voice": (
                len(audio_nodes),
                sum(1 for node in audio_nodes if node_results.get((canvas.project_id, node["id"]))),
            ),
            "characters": character_library_service.character_stats(
                canvas.graph or {},
                {
                    node["id"]: {a.id for a in node_results.get((canvas.project_id, node["id"]), [])}
                    for node in media
                    if node["id"] in character_ids
                },
            ),
        }

    return _Collected(jobs, costs, stats, review_by_project, failed_by_project)


def _project_entry(canvas: Any, data: _Collected) -> dict[str, Any]:
    """单个项目的概览条目（工作台列表与项目页顶栏共用）。"""
    info = data.stats[canvas.project_id]
    failed = len(data.failed_by_project.get(canvas.project_id, []))
    review = len(data.review_by_project.get(canvas.project_id, []))
    steps = _steps(
        info["shots_done"], info["shots_total"], info["has_script"], info["characters"], info["voice"]
    )
    characters_active = any(step["key"] == "characters" and step["status"] == "active" for step in steps)
    pending = [item["name"] for item in info["characters"] if not item["final"]] if characters_active else []
    return {
        "canvas_id": canvas.id,
        "project_id": canvas.project_id,
        "title": canvas.title,
        "aspect_ratio": canvas.aspect_ratio,
        "updated_at": _iso(canvas.updated_at),
        "cover_artifact_id": info["cover"],
        "total_cost": _money(data.costs.get(canvas.project_id)),
        "shots_total": info["shots_total"],
        "shots_done": info["shots_done"],
        "steps": steps,
        "next_hint": _next_hint(
            failed,
            review,
            info["shots_done"],
            info["shots_total"],
            pending,
            info["voice"][0] - info["voice"][1],
        ),
    }


async def _budget(session: AsyncSession, user_id: int) -> dict[str, Optional[str]]:
    budget = await budget_service.status(session, user_id)
    return {
        "cap": _money(budget["cap"]) if budget["cap"] is not None else None,
        "spent_today": _money(budget["spent"]),
        "in_flight": _money(budget["in_flight"]),
        "remaining": _money(budget["remaining"]) if budget["remaining"] is not None else None,
    }


async def project_summary(session: AsyncSession, user: User, canvas_id: str) -> Optional[dict[str, Any]]:
    """项目页顶栏 / 步骤条 / 底栏所需数据；不是自己的画布或不存在都返回 None（由路由转 404）。"""
    canvas = (
        await session.execute(
            select(*_CANVAS_COLUMNS).where(NodeCanvas.id == canvas_id, NodeCanvas.owner_id == user.id)
        )
    ).first()
    if canvas is None:
        return None
    data = await _collect(session, [canvas])
    entry = _project_entry(canvas, data)
    return {
        "canvas_id": entry["canvas_id"],
        "project_id": entry["project_id"],
        "title": entry["title"],
        "aspect_ratio": entry["aspect_ratio"],
        "total_cost": entry["total_cost"],
        "shots_total": entry["shots_total"],
        "shots_done": entry["shots_done"],
        "steps": entry["steps"],
        "active_step": next((step["key"] for step in entry["steps"] if step["status"] == "active"), None),
        "next_hint": entry["next_hint"],
        "running_count": sum(1 for job in data.jobs if job.status in RUNNING_STATUSES),
        "budget": await _budget(session, user.id),
    }


async def overview(session: AsyncSession, user: User) -> dict[str, Any]:
    # 统计覆盖该用户全部画布；只取需要的列，避免载入整行
    canvases = (
        await session.execute(
            select(*_CANVAS_COLUMNS)
            .where(NodeCanvas.owner_id == user.id)
            .order_by(NodeCanvas.updated_at.desc())
        )
    ).all()
    by_project = {canvas.project_id: canvas for canvas in canvases}
    data = await _collect(session, canvases)
    jobs = data.jobs
    review_by_project = data.review_by_project
    failed_by_project = data.failed_by_project

    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    finished_this_week = (
        await session.scalar(
            select(func.count())
            .select_from(GenerationJob)
            .join(SeriesProject, SeriesProject.id == GenerationJob.project_id)
            .where(
                SeriesProject.user_id == user.id,
                GenerationJob.operation.like("render%"),
                GenerationJob.status == "succeeded",
                func.coalesce(GenerationJob.finished_at, GenerationJob.updated_at) >= week_ago,
            )
        )
        or 0
    )

    projects = [_project_entry(canvas, data) for canvas in canvases[:MAX_PROJECTS]]

    running_jobs = [job for job in jobs if job.status in RUNNING_STATUSES]
    node_labels = {
        (canvas.project_id, node["id"]): (_node_label(node), node.get("type", ""))
        for canvas in canvases
        for node in (canvas.graph or {}).get("nodes", [])
    }
    running = []
    for job in reversed(running_jobs):  # jobs 按创建时间倒序，反过来 = 先提交的在前
        canvas = by_project[job.project_id]
        label, kind = node_labels.get((job.project_id, job.node_id), ("节点", ""))
        running.append(
            {
                "job_id": job.id,
                "canvas_id": canvas.id,
                "project_title": canvas.title,
                "node_label": label,
                "kind": {"generate_video": "video", "tts": "audio"}.get(job.operation, "image"),
                "status": _display_status(job.status, job.external_task_id),
                "progress": max(0, min(100, job.progress or 0)),
                "created_at": _iso(job.created_at),
            }
        )

    # 列表按画布最近更新顺序合并
    order = [canvas.project_id for canvas in canvases]
    needs_review = [item for pid in order for item in review_by_project.get(pid, [])]
    failed_items = [item for pid in order for item in failed_by_project.get(pid, [])]

    return {
        "budget": await _budget(session, user.id),
        "counts": {
            "running": len(running),
            "needs_review": len(needs_review),
            "finished_this_week": int(finished_this_week),
            "failed": len(failed_items),
        },
        "projects": projects,
        "running": running[:MAX_LIST],
        "needs_review": needs_review[:MAX_LIST],
        "failed": failed_items[:MAX_LIST],
    }
