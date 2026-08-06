"""系列剧高层领域工具：Agent 编排短剧创作工作流的入口。

- 工具为 async（agent 经 astream 执行，langchain @tool 支持协程）。
- session 用 app.db.database.SessionLocal 自开；user_id/canvas_id 取 execution_context。
- 返回紧凑文本，控制 token 消耗。
"""

import json
import logging
from typing import Any, Optional

from langchain_core.tools import tool
from sqlalchemy import func, select

from app.db.database import SessionLocal
from app.db.models import (
    Episode,
    GenerationJob,
    SeriesProject,
    WorkflowRun,
    WorkflowStep,
)
from app.services import (
    approval_service,
    asset_service,
    cost_service,
    job_planner,
    planner_service,
    series_service,
    storyboard_service,
    workflow_service,
)
from app.services.execution_context import get_execution_context

logger = logging.getLogger(__name__)


def _ctx_ids() -> tuple[Optional[int], Optional[str]]:
    ctx = get_execution_context()
    user_id = int(ctx.user_id) if ctx.user_id else None
    return user_id, ctx.canvas_id


async def _get_owned_project(session, project_id: str) -> SeriesProject:
    user_id, _ = _ctx_ids()
    project = await series_service.get_project(session, project_id, user_id)
    if project is None:
        raise ValueError(f"项目 {project_id} 不存在")
    return project


async def _latest_run(session, project_id: str) -> Optional[WorkflowRun]:
    stmt = (
        select(WorkflowRun)
        .where(WorkflowRun.project_id == project_id)
        .order_by(WorkflowRun.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def _find_step(session, run_id: str, node_type: str, entity_id: str) -> Optional[WorkflowStep]:
    stmt = select(WorkflowStep).where(
        WorkflowStep.run_id == run_id,
        WorkflowStep.node_type == node_type,
        WorkflowStep.entity_id == entity_id,
    )
    return (await session.execute(stmt)).scalars().first()


@tool
async def create_series_project(
    title: str, project_type: str = "short_drama", use_llm_planner: bool = False
) -> str:
    """创建系列剧项目并启动内置短剧工作流（Workflow Run）。

    Args:
        title: 项目标题
        project_type: 项目类型，默认 short_drama
        use_llm_planner: 默认 False 用规则 Planner；True 用受控 LLM Planner
            （P2-1，失败自动回退规则版，不影响创建）

    Returns:
        项目与 Run 的 ID
    """
    user_id, canvas_id = _ctx_ids()
    async with SessionLocal() as session:
        try:
            project = await series_service.create_project(
                session, user_id, canvas_id=canvas_id, project_type=project_type, title=title
            )
            await workflow_service.seed_builtin_templates(session)
            input_snapshot: dict[str, Any] = {"goal": title}
            planner_model = ""
            extra_nodes = None
            if use_llm_planner:
                plan = await planner_service.plan_with_llm(title, project.settings or {})
                input_snapshot = {**plan.get("params", {}), "goal": title}
                planner_model = plan.get("planner_model", "rule-based")
                extra_nodes = plan.get("extra_nodes") or None
            run = await workflow_service.create_run(
                session,
                project=project,
                trigger_type="agent",
                input_snapshot=input_snapshot,
                created_by=user_id,
                planner_model=planner_model,
                extra_nodes=extra_nodes,
            )
            await workflow_service.advance_run(session, run.id)
            return f"✅ 已创建项目 {project.id}（{title}），工作流 Run {run.id}（状态 {run.status}）。"
        except (ValueError, PermissionError) as e:
            return f"❌ 创建项目失败：{e}"


@tool
async def save_storyboard(project_id: str, episode_id: str, storyboard: dict[str, Any]) -> str:
    """保存一集的分镜（新 revision），并推进工作流对应 storyboard.generate 节点。

    Args:
        project_id: 项目 ID
        episode_id: 集 ID
        storyboard: StoryboardPayload dict（episodes/scenes/shots 结构）

    Returns:
        revision 号与镜头数
    """
    async with SessionLocal() as session:
        try:
            project = await _get_owned_project(session, project_id)
            revision = await storyboard_service.save_storyboard(
                session, project, episode_id, storyboard, created_by="agent"
            )
            run = await _latest_run(session, project_id)
            if run is not None:
                step = await _find_step(session, run.id, "storyboard.generate", episode_id)
                if step is not None and step.status in {"draft", "ready", "waiting_approval"}:
                    await workflow_service.complete_step(
                        session, step.id, {"revision": revision.revision}
                    )
            shot_count = sum(
                len(sc.get("shots", []))
                for ep in (revision.payload or {}).get("episodes", [])
                for sc in ep.get("scenes", [])
            )
            return f"✅ 分镜已保存：episode {episode_id} revision {revision.revision}，共 {shot_count} 个镜头。"
        except (ValueError, PermissionError) as e:
            return f"❌ 保存分镜失败：{e}"


@tool
async def get_storyboard(project_id: str, episode_id: Optional[str] = None) -> str:
    """读取项目分镜的紧凑摘要（集/场景/镜头），供创作参考。

    Args:
        project_id: 项目 ID
        episode_id: 可选，只看某一集

    Returns:
        紧凑文本摘要
    """
    async with SessionLocal() as session:
        try:
            project = await _get_owned_project(session, project_id)
            data = await storyboard_service.list_episodes_with_storyboard(
                session, project.id, episode_id=episode_id
            )
            lines: list[str] = []
            for ep in data:
                lines.append(
                    f"第{ep['episode_no']}集 {ep.get('title') or ''}"
                    f"（revision {ep.get('current_revision')}, 状态 {ep.get('revision_status')}）"
                )
                for sc in ep.get("scenes", []):
                    loc = sc.get("location") or ""
                    lines.append(f"  场景{sc['scene_no']} {loc}")
                    for sh in sc.get("shots", []):
                        lines.append(
                            f"    镜头{sh['shot_no']}({sh['duration_ms']}ms,{sh['status']}): "
                            f"{(sh.get('visual_prompt') or '')[:80]}"
                        )
            return "\n".join(lines) if lines else "（还没有分镜）"
        except (ValueError, PermissionError) as e:
            return f"❌ 读取分镜失败：{e}"


@tool
async def update_shot(
    shot_id: str,
    visual_prompt: Optional[str] = None,
    duration_ms: Optional[int] = None,
    status: Optional[str] = None,
) -> str:
    """修改单个镜头（画面描述/时长/状态）。改内容会使所在集的分镜审批失效。

    Args:
        shot_id: 镜头 ID
        visual_prompt: 新的画面描述
        duration_ms: 新的时长（毫秒）
        status: 新的状态

    Returns:
        操作结果
    """
    user_id, _ = _ctx_ids()
    fields: dict[str, Any] = {}
    if visual_prompt is not None:
        fields["visual_prompt"] = visual_prompt
    if duration_ms is not None:
        fields["duration_ms"] = duration_ms
    if status is not None:
        fields["status"] = status
    async with SessionLocal() as session:
        try:
            await storyboard_service.update_shot(session, shot_id, fields, user_id)
            return f"✅ 镜头 {shot_id} 已更新（{', '.join(fields.keys()) or '无字段'}）。"
        except (ValueError, PermissionError) as e:
            return f"❌ 更新镜头失败：{e}"


@tool
async def create_character_asset(
    project_id: str, name: str, description: str, reference_image_url: str = ""
) -> str:
    """创建角色资产卡；参考图为本地 /storage/ 路径时同时建档 Artifact 并绑定。

    Args:
        project_id: 项目 ID
        name: 角色名
        description: 角色外观/人设描述
        reference_image_url: 可选参考图（本地 /storage/ 路径）

    Returns:
        资产 ID
    """
    user_id, _ = _ctx_ids()
    async with SessionLocal() as session:
        try:
            await _get_owned_project(session, project_id)
            card = await asset_service.create_asset_card(
                session,
                project_id,
                user_id,
                type="character",
                name=name,
                description=description,
                source="agent",
            )
            bound = ""
            if reference_image_url.startswith("/storage/"):
                artifact = await asset_service.create_artifact(
                    session,
                    project_id=project_id,
                    type="image",
                    storage_key=reference_image_url.removeprefix("/storage/"),
                    mime_type="image/png",
                )
                await asset_service.bind_artifact(session, card.id, artifact.id, role="reference")
                bound = "，已绑定参考图"
            return f"✅ 角色资产已创建：{name}（id {card.id}, v{card.version}）{bound}。"
        except (ValueError, PermissionError) as e:
            return f"❌ 创建角色资产失败：{e}"


@tool
async def approve(target_type: str, target_id: str, target_revision: int) -> str:
    """审批通过某个对象（分镜 revision 或角色资产版本），并推进工作流。

    Args:
        target_type: storyboard 或 asset
        target_id: storyboard 时为 episode_id，asset 时为资产 ID
        target_revision: revision 号或资产版本号

    Returns:
        操作结果
    """
    user_id, _ = _ctx_ids()
    async with SessionLocal() as session:
        try:
            if target_type == "storyboard":
                episode = await session.get(Episode, target_id)
                if episode is None:
                    return f"❌ 集 {target_id} 不存在"
                project_id = episode.project_id
            else:
                from app.db.models import AssetCard

                card = await session.get(AssetCard, target_id)
                if card is None:
                    return f"❌ 资产 {target_id} 不存在"
                project_id = card.project_id
            await _get_owned_project(session, project_id)
            await approval_service.approve(
                session,
                project_id=project_id,
                target_type=target_type,
                target_id=target_id,
                target_revision=target_revision,
                approved_by=user_id,
            )
            run = await _latest_run(session, project_id)
            if run is not None:
                await workflow_service.advance_run(session, run.id)
            return f"✅ 已审批 {target_type} {target_id} v{target_revision}。"
        except (ValueError, PermissionError) as e:
            return f"❌ 审批失败：{e}"


@tool
async def start_generation(
    project_id: str, episode_id: str, confirm_cost: bool = False, quote_id: str = ""
) -> str:
    """启动一集的镜头批量生成。收费操作：先返回费用估算请用户确认，confirm_cost=True 才真正创建生成任务。

    Args:
        project_id: 项目 ID
        episode_id: 集 ID
        confirm_cost: 用户已确认费用后才传 True
        quote_id: confirm_cost=True 时必填——上一轮返回报价中的 quote_id（确认必须绑定不可变报价）

    Returns:
        费用估算或任务创建结果
    """
    async with SessionLocal() as session:
        try:
            await _get_owned_project(session, project_id)
            ok, reasons = await approval_service.check_generation_gate(
                session, project_id, episode_id
            )
            if not ok:
                return "❌ 生成 Gate 未通过：\n" + "\n".join(f"- {r}" for r in reasons)
            run = await _latest_run(session, project_id)
            if run is None:
                return "❌ 该项目还没有工作流 Run"

            if not confirm_cost:
                # 只估算，不创建 job（P1-4）：生成逐项报价快照，step 停在 waiting_approval
                quote = await cost_service.create_cost_quote(session, run.id)
                snapshot = dict(run.input_snapshot or {})
                snapshot["cost_confirmed"] = False
                run.input_snapshot = snapshot
                await session.commit()
                await workflow_service.advance_run(session, run.id)
                lines = [f"💰 费用报价（quote {quote.quote_id}，价格表 {quote.price_table_version}）："]
                for item in quote.items:
                    lines.append(
                        f"- {item.operation}（{item.step_node_key}）：{item.unit_count} × "
                        f"¥{item.unit_price} = ¥{item.subtotal}"
                    )
                lines.append(f"合计约 ¥{quote.total}。请向用户确认费用后，再以 confirm_cost=True 调用本工具真正开始生成。")
                return "\n".join(lines)

            # P1-4：确认的必须是不可变报价——quote_id 绑定 + 过期/版本/input_hash 校验
            ok, reason = await cost_service.validate_cost_quote(session, run, quote_id)
            if not ok:
                return f"❌ 无法确认费用：{reason}"
            snapshot = dict(run.input_snapshot or {})
            snapshot["cost_confirmed"] = True
            run.input_snapshot = snapshot
            await session.commit()
            await workflow_service.advance_run(session, run.id)
            stmt = select(func.count()).where(
                GenerationJob.project_id == project_id,
                GenerationJob.params["run_id"].as_string() == run.id,
            )
            job_count = (await session.execute(stmt)).scalar_one()
            return (
                f"✅ 已启动生成：{job_count} 个任务已排队"
                f"（估算费用 ¥{run.estimated_cost}，Run 状态 {run.status}）。"
            )
        except (ValueError, PermissionError) as e:
            return f"❌ 启动生成失败：{e}"


@tool
async def get_generation_status(project_id: str) -> str:
    """查看项目工作流与各生成任务的状态摘要。

    Args:
        project_id: 项目 ID

    Returns:
        Run/Step/Job 状态紧凑摘要
    """
    async with SessionLocal() as session:
        try:
            await _get_owned_project(session, project_id)
            run = await _latest_run(session, project_id)
            if run is None:
                return "（该项目还没有工作流 Run）"
            detail = await workflow_service.get_run_with_steps(session, run.id)
            lines = [f"Run {run.id} 状态 {run.status}（估算 ¥{detail['run']['estimated_cost']}）"]
            for s in detail["steps"]:
                lines.append(f"  [{s['status']}] {s['node_key']}")
            stmt = (
                select(GenerationJob.status, func.count())
                .where(
                    GenerationJob.project_id == project_id,
                    GenerationJob.params["run_id"].as_string() == run.id,
                )
                .group_by(GenerationJob.status)
            )
            job_stats = {row[0]: row[1] for row in (await session.execute(stmt)).all()}
            if job_stats:
                lines.append("Jobs: " + ", ".join(f"{k}×{v}" for k, v in sorted(job_stats.items())))
            return "\n".join(lines)
        except (ValueError, PermissionError) as e:
            return f"❌ 查询状态失败：{e}"


@tool
async def retry_failed_shots(project_id: str, episode_id: str) -> str:
    """重试一集中失败的镜头生成任务（重置为 pending 等待 worker 重新执行）。

    Args:
        project_id: 项目 ID
        episode_id: 集 ID

    Returns:
        重置的任务数
    """
    async with SessionLocal() as session:
        try:
            await _get_owned_project(session, project_id)
            run = await _latest_run(session, project_id)
            if run is None:
                return "❌ 该项目还没有工作流 Run"
            count = await job_planner.retry_failed_shot_jobs(session, episode_id, run.id)
            await workflow_service.advance_run(session, run.id)
            return f"✅ 已重置 {count} 个失败任务为 pending。"
        except (ValueError, PermissionError) as e:
            return f"❌ 重试失败：{e}"


@tool
async def render_episode(project_id: str, episode_id: str, render_mode: str = "final") -> str:
    """整集渲染：要求该集所有镜头视频任务已成功，然后创建 render_episode 任务。

    Args:
        project_id: 项目 ID
        episode_id: 集 ID
        render_mode: final（默认，任何镜头缺视频产物即失败，产出可发布成片）或
            preview（缺产物镜头用黑场占位，仅预览不可发布）

    Returns:
        操作结果
    """
    if render_mode not in ("preview", "final"):
        return "❌ render_mode 必须是 preview 或 final"
    async with SessionLocal() as session:
        try:
            await _get_owned_project(session, project_id)
            run = await _latest_run(session, project_id)
            if run is None:
                return "❌ 该项目还没有工作流 Run"
            step = await _find_step(session, run.id, "shot.video.generate", episode_id)
            if step is not None and step.status != "succeeded":
                return f"❌ 镜头视频尚未全部生成完成（step 状态 {step.status}），不能渲染。"
            render_step = await _find_step(session, run.id, "episode.render", episode_id)
            if render_step is None:
                return "❌ 找不到 episode.render 节点"
            # render_mode 透传给 job_planner（P1-11）
            render_step.input_snapshot = {
                **(render_step.input_snapshot or {}),
                "render_mode": render_mode,
            }
            await session.commit()
            await workflow_service.advance_run(session, run.id)
            if render_step.status == "running":
                return f"✅ 整集渲染任务已创建（render_mode={render_mode}，step {render_step.id}）。"
            await session.refresh(render_step)
            return f"ℹ️ episode.render 当前状态 {render_step.status}。"
        except (ValueError, PermissionError) as e:
            return f"❌ 渲染失败：{e}"


SERIES_TOOLS = [
    create_series_project,
    save_storyboard,
    get_storyboard,
    update_shot,
    create_character_asset,
    approve,
    start_generation,
    get_generation_status,
    retry_failed_shots,
    render_episode,
]
