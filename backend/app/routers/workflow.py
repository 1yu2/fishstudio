"""Workflow 路由：Run 查询/控制、Step 人工推进、按目标创建 Run。

错误映射约定同 series 路由：PermissionError → 403；不存在 → 404；参数错误 → 400。
"""

from dataclasses import replace
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import User, WorkflowRun, WorkflowStep
from app.services import cost_service, planner_service, series_service, workflow_service
from app.services.execution_context import (
    get_execution_context,
    reset_execution_context,
    set_execution_context,
)

router = APIRouter(dependencies=[Depends(get_current_user)])


async def _owned_run(session: AsyncSession, run_id: str, user: User) -> WorkflowRun:
    run = await session.get(WorkflowRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run 不存在")
    try:
        project = await series_service.get_project(session, run.project_id, user.id, is_admin=user.is_admin)
    except PermissionError:
        # 无权与不存在统一 404，不泄露存在性
        raise HTTPException(status_code=404, detail="项目不存在") from None
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return run


async def _owned_step(session: AsyncSession, step_id: str, user: User) -> WorkflowStep:
    step = await session.get(WorkflowStep, step_id)
    if step is None:
        raise HTTPException(status_code=404, detail="Step 不存在")
    await _owned_run(session, step.run_id, user)
    return step


def _400(e: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


@router.get("/workflow/runs")
async def list_runs(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        project = await series_service.get_project(session, project_id, user.id, is_admin=user.is_admin)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e) or "无权访问")
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    runs = await workflow_service.list_runs(session, project_id)
    return [
        {
            "id": r.id,
            "project_id": r.project_id,
            "status": r.status,
            "trigger_type": r.trigger_type,
            "template_version": r.template_version,
            "estimated_cost": str(r.estimated_cost) if r.estimated_cost is not None else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in runs
    ]


@router.get("/workflow/runs/{run_id}")
async def get_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await _owned_run(session, run_id, user)
    return await workflow_service.get_run_with_steps(session, run_id)


@router.get("/workflow/runs/{run_id}/trace")
async def get_run_trace(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """调用树：步骤 → 任务 → 模型调用 → 用量与成本。"""
    from app.services import cost_report_service

    run = await _owned_run(session, run_id, user)
    return await cost_report_service.run_trace(session, run)


@router.post("/workflow/runs/{run_id}/pause")
async def pause_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await _owned_run(session, run_id, user)
    run = await workflow_service.pause_run(session, run_id)
    return {"id": run.id, "status": run.status}


@router.post("/workflow/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await _owned_run(session, run_id, user)
    try:
        run = await workflow_service.resume_run(session, run_id)
    except ValueError as e:
        raise _400(e)
    return {"id": run.id, "status": run.status}


@router.post("/workflow/steps/{step_id}/retry")
async def retry_step(
    step_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await _owned_step(session, step_id, user)
    try:
        step = await workflow_service.retry_step(session, step_id)
    except ValueError as e:
        raise _400(e)
    return {"id": step.id, "status": step.status, "attempt": step.attempt}


class StepCompleteRequest(BaseModel):
    output_snapshot: Optional[dict[str, Any]] = None


@router.post("/workflow/steps/{step_id}/complete")
async def complete_step(
    step_id: str,
    body: StepCompleteRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    await _owned_step(session, step_id, user)
    try:
        step = await workflow_service.complete_step(session, step_id, body.output_snapshot)
    except ValueError as e:
        raise _400(e)
    return {"id": step.id, "status": step.status}


class RunCreateRequest(BaseModel):
    project_id: str
    goal: Optional[str] = None
    # P2-1：true 时用受控 LLM Planner（plan_with_llm）；失败/超时自动回退规则版，
    # 响应 plan 中带 planner_fallback/fallback_reason/planner_model。
    use_llm_planner: bool = False


@router.post("/workflow/runs")
async def create_run(
    body: RunCreateRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        project = await series_service.get_project(session, body.project_id, user.id, is_admin=user.is_admin)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e) or "无权访问")
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        await workflow_service.seed_builtin_templates(session)
        goal = body.goal or project.title
        if body.use_llm_planner:
            ctx_token = set_execution_context(replace(get_execution_context(), project_id=project.id))
            try:
                plan = await planner_service.plan_with_llm(goal, project.settings)
            finally:
                reset_execution_context(ctx_token)
        else:
            plan = planner_service.plan_from_template(goal, project.settings)
        run = await workflow_service.create_run(
            session,
            project=project,
            trigger_type="api",
            input_snapshot={**plan.get("params", {}), "goal": body.goal or ""},
            created_by=user.id,
            planner_model=plan.get("planner_model", "rule-based"),
            extra_nodes=plan.get("extra_nodes") or None,
        )
        await workflow_service.advance_run(session, run.id)
    except ValueError as e:
        raise _400(e)
    return {"id": run.id, "status": run.status, "plan": plan}


# ─────────────────────────────────────────
# 费用报价 / 确认（P1-4 / P1-5）
# ─────────────────────────────────────────


@router.get("/workflow/runs/{run_id}/cost-quote")
async def get_cost_quote(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """生成/刷新费用报价：不创建任何 job，按未完成 creates_jobs steps × 当前
    revision 分镜 × 价格表逐项估算，快照写入 run.input_snapshot.cost_quote。"""
    await _owned_run(session, run_id, user)
    try:
        quote = await cost_service.create_cost_quote(session, run_id)
    except ValueError as e:
        raise _400(e)
    return quote.model_dump()


class ConfirmCostRequest(BaseModel):
    """confirm-cost 请求体（复审 0.6：确认必须绑定不可变报价）。

    前端契约变更：旧版无 body 的调用会 400；必须先 GET cost-quote 拿到
    quote_id，再以 {"quote_id": "..."} 确认。
    """

    quote_id: str


@router.post("/workflow/runs/{run_id}/confirm-cost")
async def confirm_cost(
    run_id: str,
    body: ConfirmCostRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """确认费用：绑定 quote_id 校验报价（存在/匹配/未过期/价格表版本/input_hash
    与当前分镜参数一致），全部通过才置 cost_confirmed=True 并推进 run。

    请求体：`{"quote_id": "<cost-quote 返回的 quote_id>"}`（必填）。

    响应：
    - 200：`{"id", "status", "cost_confirmed": true}`；
    - 400：缺少 quote_id 等参数错误；
    - 409：quote 校验失败，detail 给出具体失效原因（quote_id 不匹配 / 报价过期 /
      价格表版本变化 / 报价后分镜或参数已变化），前端应重新拉取 cost-quote。
    """
    run = await _owned_run(session, run_id, user)
    ok, reason = await cost_service.validate_cost_quote(session, run, body.quote_id)
    if not ok:
        raise HTTPException(status_code=409, detail=reason)
    snapshot = dict(run.input_snapshot or {})
    snapshot["cost_confirmed"] = True
    run.input_snapshot = snapshot
    await session.commit()
    run = await workflow_service.advance_run(session, run_id)
    return {"id": run.id, "status": run.status, "cost_confirmed": True}
