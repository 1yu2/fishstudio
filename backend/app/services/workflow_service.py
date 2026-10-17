"""Workflow 服务：模板 seed、Run 创建/推进、Step 生命周期、Job 完成回推。

推进语义（advance_run）：
- 某 step 的全部 upstream succeeded 且自身 status in (draft, ready, waiting_approval) 时才被考虑。
- requires_approval 节点：审批/费用 Gate 未过 → waiting_approval，停止推进该分支；
  Gate 在后续 advance（如 confirm-cost / 审批提交触发）时重新判定，已过则放行。
- creates_jobs 节点：Gate 过后经 job_planner 展开 GenerationJob → status=running，
  等 worker 经 on_job_finished 回推终态。
- 纯编排节点：project.initialize 自动 succeeded；outline.generate /
  episode.script.generate / storyboard.generate / asset.character.generate 是创意节点，不自动执行，
  置 waiting_approval，由高层工具（save_storyboard / create_character_asset）或
  人工 API 调 complete_step 标记完成。
"""

import logging
from typing import Any, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AssetCard,
    DirectorPlanRevision,
    Episode,
    GenerationJob,
    ScriptRevision,
    SeriesBibleRevision,
    SeriesProject,
    SkillManifest,
    StoryboardRevision,
    StyleBibleRevision,
    TimelineRevision,
    WorkflowRun,
    WorkflowStep,
    WorkflowStepDependency,
    WorkflowTemplate,
)
from app.services import approval_service, cost_service, job_planner
from app.services.workflow_nodes import (
    NODE_TYPES,
    SHORT_DRAMA_TEMPLATE_DEFAULT_POLICY,
    SHORT_DRAMA_TEMPLATE_GRAPH,
    get_node_spec,
    toposort,
)

logger = logging.getLogger(__name__)

BUILTIN_TEMPLATE_KEY = "short-drama"
BUILTIN_TEMPLATE_VERSION = 5

# Planner 提交参数的硬上限（与模板 default_policy 的 max_shots_per_episode 共同生效）
MAX_EPISODES = 32
MAX_SHOTS_PER_EPISODE = 64


# ─────────────────────────────────────────
# seed
# ─────────────────────────────────────────


async def seed_builtin_templates(session: AsyncSession) -> WorkflowTemplate:
    """upsert 内置 short-drama 模板与 SkillManifest（按 key+version 幂等）。"""
    stmt = select(WorkflowTemplate).where(
        WorkflowTemplate.key == BUILTIN_TEMPLATE_KEY,
        WorkflowTemplate.version == BUILTIN_TEMPLATE_VERSION,
    )
    template = (await session.execute(stmt)).scalars().first()
    if template is None:
        template = WorkflowTemplate(
            key=BUILTIN_TEMPLATE_KEY,
            name="内置短剧工作流",
            version=BUILTIN_TEMPLATE_VERSION,
            project_type="short_drama",
            graph_schema=SHORT_DRAMA_TEMPLATE_GRAPH,
            default_policy=SHORT_DRAMA_TEMPLATE_DEFAULT_POLICY,
            status="active",
        )
        session.add(template)
    else:
        template.graph_schema = SHORT_DRAMA_TEMPLATE_GRAPH
        template.default_policy = SHORT_DRAMA_TEMPLATE_DEFAULT_POLICY

    stmt = select(SkillManifest).where(SkillManifest.key == "short-drama", SkillManifest.version == "4.0.0")
    if (await session.execute(stmt)).scalars().first() is None:
        session.add(
            SkillManifest(
                key="short-drama",
                name="内置短剧创作技能",
                version="4.0.0",
                input_schema={"goal": "str", "episodes": "int", "shots_per_episode": "int"},
                output_schema={"run_id": "str"},
                required_capabilities=list(NODE_TYPES.keys()),
                source="builtin",
                status="active",
            )
        )
    await session.commit()
    await session.refresh(template)
    return template


# ─────────────────────────────────────────
# plan 校验（纯函数）
# ─────────────────────────────────────────


def validate_plan(plan: dict[str, Any]) -> tuple[bool, list[str]]:
    """校验 Planner/手动提交的节点图 + 参数。返回 (ok, errors)。纯函数。"""
    errors: list[str] = []
    graph = plan.get("graph") or SHORT_DRAMA_TEMPLATE_GRAPH
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    node_keys = [n.get("node_key") for n in nodes]
    if len(node_keys) != len(set(node_keys)):
        errors.append("node_key 重复")
    for n in nodes:
        nt = n.get("node_type")
        if nt not in NODE_TYPES:
            errors.append(f"未知 node_type: {nt}")
    known = set(node_keys)
    for a, b in edges:
        if a not in known or b not in known:
            errors.append(f"edge 引用未知节点: {a} -> {b}")
    if not any("edge 引用未知节点" in e for e in errors):
        try:
            toposort(node_keys, edges)
        except ValueError:
            errors.append("DAG 存在环")

    params = plan.get("params") or {}
    episodes = params.get("episodes", 1)
    shots = params.get("shots_per_episode", 1)
    if not isinstance(episodes, int) or not (1 <= episodes <= MAX_EPISODES):
        errors.append(f"episodes 超出范围 1..{MAX_EPISODES}: {episodes}")
    if not isinstance(shots, int) or not (1 <= shots <= MAX_SHOTS_PER_EPISODE):
        errors.append(f"shots_per_episode 超出范围 1..{MAX_SHOTS_PER_EPISODE}: {shots}")
    cost_limit = params.get("cost_limit")
    if cost_limit is not None and (not isinstance(cost_limit, (int, float)) or cost_limit < 0):
        errors.append(f"cost_limit 非法: {cost_limit}")
    return (len(errors) == 0, errors)


# ─────────────────────────────────────────
# Run 创建（模板图按集展开）
# ─────────────────────────────────────────

# extra_nodes 作为 final render 的附加前置约束；保留 episode.render node_key 兼容旧 Planner。
_EXTRA_NODE_DEFAULT_UPSTREAM = "shot.audio.generate"
_EXTRA_NODE_RENDER_SINK = "episode.render"


def apply_extra_nodes(graph: dict[str, Any], extra_nodes: list[Any]) -> dict[str, Any]:
    """把 Planner 校验过的额外节点作为 final render 的附加前置链。纯函数。

    落地语义（受控最小实现，见 P2-1）：
    - 元素为 str(node_type) 或 {"node_type": ..., "upstream": ...}（dict 形式可
      指定上游 node_key，缺省接前一个额外节点，首个缺省接 shot.audio.generate）；
    - node_type 必须已注册（plan_with_llm 已校验，这里防御性再查）；
    - node_key 唯一化：`extra.<node_type>`，冲突时追加 `#2`、`#3`…；
    - 拆除 shot.audio.generate → episode.render 直连，额外节点按顺序串入尾部链，
      最后一个额外节点 → episode.render（render 不存在时只挂上上游边）。
    """
    nodes = [dict(n) for n in (graph.get("nodes") or [])]
    edges = [list(e) for e in (graph.get("edges") or [])]
    known = {n["node_key"] for n in nodes}
    if not extra_nodes:
        return {**graph, "nodes": nodes, "edges": edges}

    prev = _EXTRA_NODE_DEFAULT_UPSTREAM
    if prev in known:
        edges = [e for e in edges if not (e[0] == prev and e[1] == _EXTRA_NODE_RENDER_SINK)]
    for item in extra_nodes:
        if isinstance(item, str):
            node_type, upstream = item, None
        else:
            node_type, upstream = item.get("node_type"), item.get("upstream")
        get_node_spec(node_type)  # 未注册抛 ValueError
        key = f"extra.{node_type}"
        i = 2
        while key in known:
            key = f"extra.{node_type}#{i}"
            i += 1
        known.add(key)
        nodes.append({"node_key": key, "node_type": node_type})
        up = upstream or prev
        if up not in known:
            raise ValueError(f"extra node upstream 未知: {up}")
        edges.append([up, key])
        prev = key
    if _EXTRA_NODE_RENDER_SINK in known:
        edges.append([prev, _EXTRA_NODE_RENDER_SINK])
    return {**graph, "nodes": nodes, "edges": edges}


async def create_run(
    session: AsyncSession,
    *,
    project: SeriesProject,
    template_key: str = BUILTIN_TEMPLATE_KEY,
    trigger_type: str = "manual",
    input_snapshot: Optional[dict[str, Any]] = None,
    created_by: Optional[int] = None,
    planner_model: str = "",
    extra_nodes: Optional[list[Any]] = None,
) -> WorkflowRun:
    """加载模板（锁版本）→ 校验 → 创建 Run + 按 graph_schema 展开 Steps + Dependencies。

    extra_nodes（P2-1）：LLM Planner 返回的额外节点（已校验为注册类型），经
    apply_extra_nodes 插入模板 DAG 尾部链（episode.render 之前）后一并展开。
    """
    stmt = (
        select(WorkflowTemplate)
        .where(WorkflowTemplate.key == template_key, WorkflowTemplate.status == "active")
        .order_by(WorkflowTemplate.version.desc())
        .limit(1)
    )
    template = (await session.execute(stmt)).scalars().first()
    if template is None:
        raise ValueError(f"workflow template not found: {template_key}")

    snapshot = dict(input_snapshot or {})
    default_policy = dict(template.default_policy or {})

    # 复审 0.10：Planner (LLM/规则) 输出的策略参数规范化到 default_policy，
    # 确保报价、Job Planner 和前端读同一份快照
    provider_policy = snapshot.pop("provider_policy", None) or default_policy.get(
        "provider_policy", "balanced"
    )
    planner_provider_map = {"fast": "cheapest", "cheap": "cheapest"}
    provider_policy = planner_provider_map.get(provider_policy, provider_policy)
    default_policy["provider_policy"] = provider_policy
    default_policy["resolution"] = snapshot.pop("resolution", None) or default_policy.get(
        "resolution", "768P"
    )
    default_policy["aspect_ratio"] = snapshot.pop("aspect_ratio", None) or default_policy.get(
        "aspect_ratio", "9:16"
    )
    shots_per_episode = snapshot.pop("shots_per_episode", None)
    if shots_per_episode is not None:
        default_policy["shots_per_episode"] = int(shots_per_episode)
    # R0.11：candidate_count 限制 1~4
    cc = int(snapshot.pop("candidate_count", None) or default_policy.get("candidate_count", 1))
    default_policy["candidate_count"] = max(1, min(cc, 4))
    snapshot["default_policy"] = default_policy

    max_shots = int(default_policy.get("max_shots_per_episode", 16))
    shots_per_episode_val = int(default_policy.get("shots_per_episode", 1))
    if shots_per_episode_val > max_shots:
        raise ValueError(f"shots_per_episode {shots_per_episode_val} > max {max_shots}")

    episodes = (
        (await session.execute(select(Episode).where(Episode.project_id == project.id))).scalars().all()
    )
    if not episodes:
        raise ValueError("project has no episodes")

    # 复审 0.10：plan.episodes > 已有集数时自动补齐
    planned_episodes = int(snapshot.get("episodes", len(episodes)))
    if planned_episodes > len(episodes):
        existing_nos = {e.episode_no for e in episodes}
        for n in range(1, planned_episodes + 1):
            if n not in existing_nos:
                session.add(
                    Episode(
                        project_id=project.id,
                        episode_no=n,
                        title=f"{project.title} 第{n}集",
                    )
                )
        await session.flush()
        episodes = (
            (await session.execute(select(Episode).where(Episode.project_id == project.id))).scalars().all()
        )

    run = WorkflowRun(
        project_id=project.id,
        template_id=template.id,
        template_version=template.version,
        planner_model=planner_model,
        status="ready",
        trigger_type=trigger_type,
        input_snapshot=snapshot,
        created_by=created_by,
    )
    if snapshot.get("cost_limit") is not None:
        run.cost_limit = snapshot["cost_limit"]
    session.add(run)
    await session.flush()

    graph = template.graph_schema or SHORT_DRAMA_TEMPLATE_GRAPH
    if extra_nodes:
        graph = apply_extra_nodes(graph, extra_nodes)
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    # 展开：project 作用域节点每 run 一个；episode 作用域节点每集一个
    step_by_ref: dict[tuple[str, str], WorkflowStep] = {}  # (node_key, episode_id|"") -> step
    order = toposort([n["node_key"] for n in nodes], edges)
    node_map = {n["node_key"]: n for n in nodes}
    sort_idx = 0
    for node_key in order:
        spec = get_node_spec(node_map[node_key]["node_type"])
        targets = [("", None)] if spec.scope == "project" else [(ep.id, ep) for ep in episodes]
        for ep_id, _ep in targets:
            full_key = node_key if not ep_id else f"{node_key}:ep_{ep_id}"
            step = WorkflowStep(
                run_id=run.id,
                node_key=full_key,
                node_type=spec.node_type,
                entity_type="episode" if ep_id else "project",
                entity_id=ep_id or project.id,
                status="draft",
                sort_order=sort_idx,
            )
            session.add(step)
            step_by_ref[(node_key, ep_id)] = step
            sort_idx += 1
    await session.flush()

    for a, b in edges:
        spec_a = get_node_spec(node_map[a]["node_type"])
        spec_b = get_node_spec(node_map[b]["node_type"])
        ep_ids_a = [""] if spec_a.scope == "project" else [ep.id for ep in episodes]
        ep_ids_b = [""] if spec_b.scope == "project" else [ep.id for ep in episodes]
        # project→episode：连每集；episode→project：每集都连；同作用域：同集互连
        for ea in ep_ids_a:
            for eb in ep_ids_b:
                if ea and eb and ea != eb:
                    continue
                session.add(
                    WorkflowStepDependency(
                        run_id=run.id,
                        upstream_step_id=step_by_ref[(a, ea)].id,
                        downstream_step_id=step_by_ref[(b, eb)].id,
                    )
                )

    await session.commit()
    await session.refresh(run)
    return run


# ─────────────────────────────────────────
# 推进器
# ─────────────────────────────────────────

# 纯编排且 MVP 自动完成的节点
_AUTO_COMPLETE_TYPES = {"project.initialize"}
# LLM 创意/登记节点：MVP 不自动执行，等工具/人工 complete_step
_WAIT_EXTERNAL_TYPES = {
    "series.bible.generate",
    "style.bible.generate",
    "outline.generate",
    "episode.script.generate",
    "episode.script.review",
    "director.plan.generate",
    "storyboard.generate",
    "storyboard.continuity.check",
    "asset.character.generate",
    "timeline.plan",
    "episode.director.review",
    "episode.final.qc",
    "publish.package.generate",
}

# waiting_approval 也要参与重估：审批/费用 Gate 在审批提交或 confirm-cost 后的
# 下一次 advance 必须能重新判定并放行（E2E 发现的卡死问题）；状态未变时不得
# 重复置 progressed，否则 while 循环永不退出。
_STEP_ADVANCE_STATUSES = {"draft", "ready", "waiting_approval"}


def upstream_all_succeeded(
    step_id: str, steps: list[WorkflowStep], deps: list[WorkflowStepDependency]
) -> bool:
    """依赖判定纯函数（拆出便于单测）。"""
    status_by_id = {s.id: s.status for s in steps}
    for d in deps:
        if d.downstream_step_id == step_id and status_by_id.get(d.upstream_step_id) not in {
            "succeeded",
            "skipped",
        }:
            return False
    return True


def _find_step(steps: list[WorkflowStep], step_id: str) -> Optional[WorkflowStep]:
    for s in steps:
        if s.id == step_id:
            return s
    return None


async def _approval_passed(session: AsyncSession, run: WorkflowRun, step: WorkflowStep) -> bool:
    """requires_approval 节点的 Gate 判定。"""
    if step.node_type in {"series.bible.approve", "style.bible.approve"}:
        is_series = step.node_type == "series.bible.approve"
        model = SeriesBibleRevision if is_series else StyleBibleRevision
        target_type = "series_bible" if is_series else "style_bible"
        revision = (
            (
                await session.execute(
                    select(model)
                    .where(model.project_id == run.project_id)
                    .order_by(model.revision.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if revision is None:
            return False
        return await approval_service.is_approved(session, target_type, run.project_id, revision.revision)
    if step.node_type == "episode.script.approve":
        stmt = (
            select(ScriptRevision)
            .where(ScriptRevision.episode_id == step.entity_id)
            .order_by(ScriptRevision.revision.desc())
            .limit(1)
        )
        revision = (await session.execute(stmt)).scalars().first()
        if revision is None:
            return False
        return await approval_service.is_approved(session, "script", step.entity_id, revision.revision)
    if step.node_type == "director.plan.approve":
        revision = (
            (
                await session.execute(
                    select(DirectorPlanRevision)
                    .where(DirectorPlanRevision.episode_id == step.entity_id)
                    .order_by(DirectorPlanRevision.revision.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if revision is None:
            return False
        return await approval_service.is_approved(session, "director_plan", step.entity_id, revision.revision)
    if step.node_type == "storyboard.approve":
        stmt = (
            select(StoryboardRevision)
            .where(StoryboardRevision.episode_id == step.entity_id)
            .order_by(StoryboardRevision.revision.desc())
            .limit(1)
        )
        revision = (await session.execute(stmt)).scalars().first()
        if revision is None:
            return False
        return await approval_service.is_approved(session, "storyboard", step.entity_id, revision.revision)
    if step.node_type == "asset.version.approve":
        stmt = select(AssetCard).where(
            AssetCard.project_id == run.project_id,
            AssetCard.type == "character",
            AssetCard.status != "retired",
        )
        cards = (await session.execute(stmt)).scalars().all()
        # 没有角色资产时视为无需审批（不阻塞）；有则全部 approved 才过
        return all(c.status == "approved" for c in cards)
    if step.node_type == "shot.video.generate":
        # 收费 Gate：分镜+资产审批齐备，且用户已确认费用
        ok, _reasons = await approval_service.check_generation_gate(session, run.project_id, step.entity_id)
        if not ok:
            return False
        return bool((run.input_snapshot or {}).get("cost_confirmed"))
    if step.node_type in {"episode.render", "episode.render.final"}:
        revision = (
            (
                await session.execute(
                    select(TimelineRevision)
                    .where(
                        TimelineRevision.episode_id == step.entity_id,
                        TimelineRevision.status == "approved",
                    )
                    .order_by(TimelineRevision.revision.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        return revision is not None
    return True


async def _cost_gate_passed(session: AsyncSession, run: WorkflowRun) -> bool:
    """requires_cost_confirm 节点的费用 Gate（复审 0.6/P1-4）：

    run.input_snapshot.cost_confirmed == True 且快照 quote 仍有效（quote_id
    已在 confirm-cost 时绑定，这里复核未过期/价格表/input_hash），否则停
    waiting_approval——收费 job 不得在费用确认前创建。
    """
    snapshot = run.input_snapshot or {}
    if snapshot.get("cost_confirmed") is not True:
        return False
    quote = snapshot.get("cost_quote") or {}
    ok, _reason = await cost_service.validate_cost_quote(session, run, quote.get("quote_id", ""))
    return ok


async def _expand_jobs(session: AsyncSession, run: WorkflowRun, step: WorkflowStep) -> int:
    """creates_jobs 节点展开 Job，返回创建/复用的 job 数。"""
    if step.node_type == "shot.video.generate":
        jobs = await job_planner.plan_shot_video_jobs(session, run=run, step=step, episode_id=step.entity_id)
        return len(jobs)
    if step.node_type == "shot.image.generate":
        jobs = await job_planner.plan_shot_image_jobs(session, run=run, step=step, episode_id=step.entity_id)
        return len(jobs)
    if step.node_type == "shot.audio.generate":
        jobs = await job_planner.plan_shot_audio_jobs(session, run=run, step=step, episode_id=step.entity_id)
        return len(jobs)
    if step.node_type in {"episode.render", "episode.render.final"}:
        await job_planner.plan_episode_render_job(session, run=run, step=step, episode_id=step.entity_id)
        return 1
    raise ValueError(f"node_type {step.node_type} does not create jobs")


async def advance_run(session: AsyncSession, run_id: str) -> WorkflowRun:
    """核心推进器：反复扫描直到无可推进的分支。"""
    run = await session.get(WorkflowRun, run_id)
    if run is None:
        raise ValueError(f"run {run_id} not found")
    if run.status in {"paused", "canceled", "succeeded", "failed"}:
        return run

    steps = list(
        (await session.execute(select(WorkflowStep).where(WorkflowStep.run_id == run_id))).scalars().all()
    )
    deps = list(
        (await session.execute(select(WorkflowStepDependency).where(WorkflowStepDependency.run_id == run_id)))
        .scalars()
        .all()
    )

    progressed = True
    while progressed:
        progressed = False
        for step in sorted(steps, key=lambda s: s.sort_order):
            if step.status not in _STEP_ADVANCE_STATUSES:
                continue
            if not upstream_all_succeeded(step.id, steps, deps):
                continue
            spec = get_node_spec(step.node_type)

            if spec.requires_approval and not await _approval_passed(session, run, step):
                if step.status != "waiting_approval":
                    step.status = "waiting_approval"
                    progressed = True  # 状态变了，允许后续重扫；但此分支停在这里
                continue

            # 费用 Gate（复审 0.6）：所有产生外部费用的节点统一拦截
            if spec.requires_cost_confirm and not await _cost_gate_passed(session, run):
                if step.status != "waiting_approval":
                    step.status = "waiting_approval"
                    progressed = True
                continue

            if step.node_type in _AUTO_COMPLETE_TYPES:
                step.status = "succeeded"
                progressed = True
                continue

            if step.node_type in _WAIT_EXTERNAL_TYPES:
                if step.status != "waiting_approval":
                    step.status = "waiting_approval"
                    progressed = True
                continue

            if spec.creates_jobs:
                job_count = await _expand_jobs(session, run, step)
                if step.status == "failed":  # cost_limit_exceeded 等
                    progressed = True
                    continue
                # 复审 0.9：零 Job 的收费节点跳过——无对白/旁白时 TTS 无 Job，
                # 不能标 running 导致永久卡住；标记 skipped 让上游推进。
                if job_count == 0:
                    step.status = "skipped"
                    step.output_snapshot = {
                        **(step.output_snapshot or {}),
                        "job_count": 0,
                        "skip_reason": "no_jobs_created",
                    }
                    progressed = True
                    continue
                step.status = "running"
                progressed = True
                continue

            # 其余审批节点已通过 Gate → 直接成功
            step.status = "succeeded"
            progressed = True

    # run 汇总状态
    statuses = {s.status for s in steps}
    if statuses == {"succeeded"} or statuses <= {"succeeded", "skipped"}:
        run.status = "succeeded"
    elif "failed" in statuses:
        run.status = "failed"
    elif "running" in statuses or "queued" in statuses:
        run.status = "running"
    elif "waiting_approval" in statuses:
        run.status = "waiting_approval"
    await session.commit()
    await session.refresh(run)
    return run


# ─────────────────────────────────────────
# Step 生命周期
# ─────────────────────────────────────────


async def complete_step(
    session: AsyncSession, step_id: str, output_snapshot: Optional[dict[str, Any]] = None
) -> WorkflowStep:
    """标记 step succeeded（人工/工具推进编排节点）→ advance_run。"""
    step = await session.get(WorkflowStep, step_id)
    if step is None:
        raise ValueError(f"step {step_id} not found")
    step.status = "succeeded"
    if output_snapshot:
        step.output_snapshot = output_snapshot
    await session.commit()
    await advance_run(session, step.run_id)
    await session.refresh(step)
    return step


async def fail_step(
    session: AsyncSession, step_id: str, error: str = "", error_code: str = ""
) -> WorkflowStep:
    step = await session.get(WorkflowStep, step_id)
    if step is None:
        raise ValueError(f"step {step_id} not found")
    step.status = "failed"
    step.error = error
    step.error_code = error_code
    await session.commit()
    await advance_run(session, step.run_id)
    await session.refresh(step)
    return step


async def retry_step(session: AsyncSession, step_id: str) -> WorkflowStep:
    """重试 step：failed/canceled → ready，attempt+1（attempt 进入幂等键，重试展开的是新 Job）。

    P1-6：同一事务里把 run 从 failed/paused 恢复为 ready 并清 finished_at，
    否则 advance_run 会因 run 终态/暂停直接返回，step 永远不会重新展开。

    允许重试的组合：
    - step.status in (failed, canceled)——部分 job 被取消的 step 也会聚合为
      failed（partially_canceled），走同一入口；
    - run.status in (failed, paused, running, ready)——succeeded/canceled 的
      run 不再允许重试其中的 step。
    """
    step = await session.get(WorkflowStep, step_id)
    if step is None:
        raise ValueError(f"step {step_id} not found")
    if step.status not in {"failed", "canceled"}:
        raise ValueError(f"step status is {step.status}, only failed/canceled can retry")
    run = await session.get(WorkflowRun, step.run_id)
    if run is None:
        raise ValueError(f"run {step.run_id} not found")
    if run.status not in {"failed", "paused", "running", "ready"}:
        raise ValueError(f"run status is {run.status}, cannot retry step")
    step.status = "ready"
    step.attempt += 1
    step.error = ""
    step.error_code = ""
    if run.status in {"failed", "paused"}:
        run.status = "ready"
        run.finished_at = None
    await session.commit()
    await advance_run(session, step.run_id)
    await session.refresh(step)
    return step


async def pause_run(session: AsyncSession, run_id: str) -> WorkflowRun:
    """暂停 run（P1-10 语义）：只阻止**未提交**的 job 被 worker 领取
    （lease_jobs 排除 paused/canceled run）；已提交到外部 provider 的任务
    不主动取消（Provider cancel 能力后续迭代接入）。"""
    run = await session.get(WorkflowRun, run_id)
    if run is None:
        raise ValueError(f"run {run_id} not found")
    run.status = "paused"
    await session.commit()
    await session.refresh(run)
    return run


async def resume_run(session: AsyncSession, run_id: str) -> WorkflowRun:
    run = await session.get(WorkflowRun, run_id)
    if run is None:
        raise ValueError(f"run {run_id} not found")
    if run.status != "paused":
        raise ValueError(f"run status is {run.status}, only paused can resume")
    run.status = "ready"
    await session.commit()
    return await advance_run(session, run_id)


# ─────────────────────────────────────────
# 查询
# ─────────────────────────────────────────


async def get_run_with_steps(session: AsyncSession, run_id: str) -> dict[str, Any]:
    """DAG 结构输出：run + steps（含 entity 绑定）+ dependencies。"""
    run = await session.get(WorkflowRun, run_id)
    if run is None:
        raise ValueError(f"run {run_id} not found")
    steps = (await session.execute(select(WorkflowStep).where(WorkflowStep.run_id == run_id))).scalars().all()
    deps = (
        (await session.execute(select(WorkflowStepDependency).where(WorkflowStepDependency.run_id == run_id)))
        .scalars()
        .all()
    )
    return {
        "run": {
            "id": run.id,
            "project_id": run.project_id,
            "template_id": run.template_id,
            "template_version": run.template_version,
            "status": run.status,
            "trigger_type": run.trigger_type,
            "input_snapshot": run.input_snapshot,
            "planner_model": run.planner_model,
            "estimated_cost": str(run.estimated_cost) if run.estimated_cost is not None else None,
            "cost_limit": str(run.cost_limit) if run.cost_limit is not None else None,
        },
        "steps": [
            {
                "id": s.id,
                "node_key": s.node_key,
                "node_type": s.node_type,
                "entity_type": s.entity_type,
                "entity_id": s.entity_id,
                "status": s.status,
                "attempt": s.attempt,
                "error_code": s.error_code,
                "error": s.error,
                "selected_artifact_id": s.selected_artifact_id,
                "sort_order": s.sort_order,
                "input_snapshot": s.input_snapshot,
                "output_snapshot": s.output_snapshot,
            }
            for s in sorted(steps, key=lambda x: x.sort_order)
        ],
        "dependencies": [
            {
                "upstream_step_id": d.upstream_step_id,
                "downstream_step_id": d.downstream_step_id,
                "dependency_type": d.dependency_type,
            }
            for d in deps
        ],
    }


async def list_runs(session: AsyncSession, project_id: str) -> list[WorkflowRun]:
    stmt = (
        select(WorkflowRun)
        .where(WorkflowRun.project_id == project_id)
        .order_by(WorkflowRun.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


# ─────────────────────────────────────────
# Job 完成回推（worker 后续调用）
# ─────────────────────────────────────────


async def on_job_finished(session: AsyncSession, job: GenerationJob) -> None:
    """Job 终态回推：该 step **当前 attempt 代际**的 jobs 全部终态 → step 终态 → advance。

    step 关联优先用 job.workflow_step_id（P1-10 新列），兼容 params.step_id JSON。

    当前代际聚合（复审 0.7 / P1-6 / P1-7）：
    - 只统计 step_attempt == step.attempt 的 job——旧 attempt 的 failed job
      不再影响当前聚合；
    - 同一 logical_job_key 多代（job 级 retry 产生新 job、旧 job 回填
      superseded_by）只取最新一代：superseded_by 为空的 job；
    - 兼容：存量 job step_attempt=0 且 step.attempt=0 时行为与旧版一致。

    聚合语义（P1-9）：
    - 当前代 job 全部 succeeded → step succeeded；
    - 有 failed → step failed, error_code=job_failed；
    - 无 failed 但有 canceled（部分取消，其余终态）→ step failed,
      error_code=partially_canceled，用户可 retry_step 重新展开；
    - 否则仍有 job 在跑，不改变 step 状态。
    """
    step_id = job.workflow_step_id or (job.params or {}).get("step_id")
    if not step_id:
        return
    step = await session.get(WorkflowStep, step_id)
    if step is None:
        return
    step_filter = or_(
        GenerationJob.workflow_step_id == step_id,
        GenerationJob.params["step_id"].as_string() == step_id,
    )
    stmt = select(GenerationJob).where(
        step_filter,
        GenerationJob.step_attempt == step.attempt,
    )
    attempt_jobs = list((await session.execute(stmt)).scalars().all())
    # 同 logical_job_key 多代只取最新一代（未被 superseded 的）
    current = [j for j in attempt_jobs if not j.superseded_by]
    if not current:
        return
    statuses = [j.status for j in current]
    if any(s not in job_planner.JOB_TERMINAL_STATUSES for s in statuses):
        return  # 当前代还有 job 在跑
    if "failed" in statuses:
        step.status = "failed"
        step.error_code = "job_failed"
    elif "canceled" in statuses:
        step.status = "failed"
        step.error_code = "partially_canceled"
    else:
        step.status = "succeeded"
    # job 级 retry 恢复（复审 0.7）：run 已 failed 时 step 重新到达终态（尤其是
    # succeeded）必须把 run 恢复为 ready 再 advance，否则 advance_run 因 run
    # 终态直接返回，job 级重试永远无法让 run 恢复推进（与 retry_step 的 P1-6
    # 恢复逻辑同源）。
    run = await session.get(WorkflowRun, step.run_id)
    if run is not None and run.status == "failed":
        run.status = "ready"
        run.finished_at = None
    await session.commit()
    await advance_run(session, step.run_id)
