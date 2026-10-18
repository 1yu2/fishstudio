"""GenerationJob / Usage 查询与管理 API。"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import GenerationJob, SeriesProject, User
from app.model_runtime.media import get_media_runtime
from app.services import (
    cost_service,
    job_event_service,
    job_service,
    usage_service,
    workflow_service,
)
from app.services.ownership import check_project_access

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])

# provider cancel（best-effort）用的路由表；与 worker 共用同一路由配置
_media_runtime = get_media_runtime()
_media_runtime.video_router()


class JobResponse(BaseModel):
    id: str
    project_id: str
    shot_id: Optional[str]
    operation: str
    provider: str
    model: str
    params: dict
    idempotency_key: str
    external_task_id: str
    status: str
    progress: int
    attempt: int
    max_attempts: int
    priority: int = 0
    worker_class: str = "media"
    leased_by: str = ""
    heartbeat_at: Optional[datetime] = None
    dead_lettered_at: Optional[datetime] = None
    error_code: str
    error: str
    result: dict
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    finished_at: Optional[datetime]

    @classmethod
    def from_job(cls, job: GenerationJob) -> "JobResponse":
        return cls(
            id=job.id,
            project_id=job.project_id,
            shot_id=job.shot_id,
            operation=job.operation,
            provider=job.provider,
            model=job.model,
            params=job.params or {},
            idempotency_key=job.idempotency_key,
            external_task_id=job.external_task_id,
            status=job.status,
            progress=job.progress,
            attempt=job.attempt,
            max_attempts=job.max_attempts,
            priority=getattr(job, "priority", 0),
            worker_class=getattr(job, "worker_class", "media"),
            leased_by=getattr(job, "leased_by", ""),
            heartbeat_at=getattr(job, "heartbeat_at", None),
            dead_lettered_at=getattr(job, "dead_lettered_at", None),
            error_code=job.error_code,
            error=job.error,
            result=job.result or {},
            created_at=job.created_at,
            updated_at=job.updated_at,
            finished_at=job.finished_at,
        )


async def _check_project_access(session: AsyncSession, project_id: str, user: User) -> SeriesProject:
    """project 归属校验（统一走 ownership.check_project_access，含无主项目拒绝）；
    不存在或无权 → 404（不泄露存在性）。"""
    project = await session.get(SeriesProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        check_project_access(project, user)
    except PermissionError:
        raise HTTPException(status_code=404, detail="project not found") from None
    return project


async def _get_job_checked(session: AsyncSession, job_id: str, user: User) -> GenerationJob:
    job = await job_service.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    await _check_project_access(session, job.project_id, user)
    return job


@router.get("/jobs")
async def list_jobs(
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if project_id:
        await _check_project_access(session, project_id, user)
        jobs = await job_service.list_jobs(session, project_id=project_id, status=status, limit=limit)
    else:
        # 不带 project_id 时不做全量暴露：要求调用方给 project_id
        raise HTTPException(status_code=400, detail="project_id is required")
    return {"jobs": [JobResponse.from_job(j).model_dump(mode="json") for j in jobs]}


@router.get("/jobs/events")
async def list_job_events(
    project_id: str,
    after: int = 0,
    limit: int = 200,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """断线可续传的事件游标接口；Redis 负责低延迟通知，DB 提供一致回放。"""
    await _check_project_access(session, project_id, user)
    rows = await job_event_service.list_events(session, project_id, after=max(0, after), limit=limit)
    return {"events": [job_event_service.event_payload(row) for row in rows]}


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    job = await _get_job_checked(session, job_id, user)
    return JobResponse.from_job(job).model_dump(mode="json")


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """重试 = 创建**新 job**（P1-7），不复用旧记录。

    复制原 job 的 operation/params/project_id/shot_id/workflow_run_id/
    workflow_step_id/provider/model 快照；新 idempotency_key=retry:{原key}
    （已存在则追加 :2/:3...）；params.retry_of_job_id 记录血缘；旧 job 保持
    failed 不动，但回填 superseded_by=新 job.id；logical_job_key/step_attempt
    继承原 job；external_task_id 不带入新 job（worker 会重新 submit，
    而不是继续轮询已失败的外部任务）。

    R0.7/R1.6：FOR UPDATE 锁定旧 Job，锁内检查 superseded_by 防止并发双击；
    预算预留 → 创建新 job → flush → 回填 superseded_by → 只提交一次。
    """
    job = await _get_job_checked(session, job_id, user)
    if job.status != "failed":
        raise HTTPException(status_code=409, detail=f"只有 failed 状态的 job 可重试（当前: {job.status}）")

    # P1-2：FOR UPDATE 行锁 + populate_existing 刷新 identity map + 显式 refresh
    from sqlalchemy import select as sa_select

    lock_stmt = (
        sa_select(GenerationJob)
        .where(GenerationJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    lock_result = await session.execute(lock_stmt)
    try:
        locked_row = lock_result.scalars().first()
    except AttributeError:
        locked_row = None  # 测试替身兼容
    if locked_row is not None and hasattr(locked_row, "superseded_by"):
        if locked_row.superseded_by:
            raise HTTPException(status_code=409, detail=f"job 已被 job={locked_row.superseded_by} 替代重试")
    elif hasattr(job, "superseded_by") and job.superseded_by:
        raise HTTPException(status_code=409, detail=f"job 已被 job={job.superseded_by} 替代重试")

    # 锁内 refresh 确保后续写 superseded_by 不会基于过期 identity map
    await session.refresh(job)

    # 重试预算（P1-5）：原 job 有估算费用时先原子预留，不够 → 409 budget_exceeded
    if job.workflow_run_id:
        retry_cost = cost_service.estimate_job_retry_cost(job)
        if retry_cost > 0 and not await cost_service.reserve_budget(session, job.workflow_run_id, retry_cost):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"budget_exceeded: 重试估算费用 ¥{retry_cost} 超出 run 预算上限，"
                    "请提高 cost_limit 或放弃重试"
                ),
            )

    base_key = f"retry:{job.idempotency_key}"[:240]  # idempotency_key 列上限 255
    key = base_key
    n = 2
    while await session.scalar(select(GenerationJob.id).where(GenerationJob.idempotency_key == key)):
        key = f"{base_key}:{n}"
        n += 1

    new_job = GenerationJob(
        project_id=job.project_id,
        shot_id=job.shot_id,
        workflow_run_id=job.workflow_run_id,
        workflow_step_id=job.workflow_step_id,
        operation=job.operation,
        provider=job.provider,
        model=job.model,
        params={**(job.params or {}), "retry_of_job_id": job.id},
        idempotency_key=key,
        status="pending",
        logical_job_key=job.logical_job_key or "",
        step_attempt=job.step_attempt or 0,
        priority=getattr(job, "priority", 0),
        worker_class=getattr(job, "worker_class", "media"),
    )
    session.add(new_job)
    await session.flush()
    await session.refresh(new_job)  # 应用 ORM 默认值（含 id）
    job.superseded_by = new_job.id
    await job_event_service.emit(session, new_job, "job.queued", payload={"retry_of": job.id})
    await job_event_service.notify_ready(new_job.id)
    await session.commit()
    await session.refresh(job)
    return JobResponse.from_job(new_job).model_dump(mode="json")


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """取消 job（原子条件更新，P1-9/P1-10 并发保护）。

    UPDATE ... WHERE status IN (pending/leased/running)，已终态 → 409，
    防止 worker 并发标 succeeded 后又被覆盖成 canceled。
    若 job 已提交外部任务（external_task_id 非空）且 provider 支持 cancel，
    先 best-effort 调 provider.cancel（失败不阻塞本地取消）。
    canceled 是终态，必须回推 workflow（P1-9）：同 step 下其余 job 全部终态后，
    step 聚合为 failed(partially_canceled)，用户可 retry_step 重新展开
    （见 workflow_service.on_job_finished 注释）。
    """
    job = await _get_job_checked(session, job_id, user)
    # best-effort 取消外部任务：不阻塞本地取消，失败仅记日志
    if job.external_task_id and job.provider:
        deployment = _media_runtime.get("video", job.provider)
        provider = deployment.provider if deployment else None
        if provider is not None:
            try:
                await provider.cancel(job.external_task_id)
            except Exception:
                logger.warning(
                    f"provider cancel 失败（忽略，继续本地取消）: job={job.id}",
                    exc_info=True,
                )
    ok = await job_service.cancel_job(session, job_id)
    if not ok:
        raise HTTPException(status_code=409, detail=f"已终态不可取消: {job.status}")
    await session.refresh(job)
    await job_event_service.emit(session, job, "job.canceled")
    await workflow_service.on_job_finished(session, job)
    await session.commit()
    return JobResponse.from_job(job).model_dump(mode="json")


@router.get("/usage/summary")
async def usage_summary(
    group_by: str = "provider",
    project_id: Optional[str] = None,
    since: Optional[datetime] = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if project_id:
        await _check_project_access(session, project_id, user)
    # project 维度已做归属校验，不再叠 user 过滤（worker 侧事件 user_id 可空）；
    # 无 project_id 时按当前用户隔离
    user_id = None if project_id else user.id
    try:
        rows = await usage_service.aggregate_usage(
            session, group_by=group_by, user_id=user_id, project_id=project_id, since=since
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"group_by": group_by, "rows": rows}


@router.get("/usage/report")
async def usage_report(
    project_id: str,
    episode_id: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """项目/单集成本报告：总计、估算 vs 实际偏差，以及按用途/Deployment/档位/镜头/自建与云端拆分。"""
    from app.services import cost_report_service

    await _check_project_access(session, project_id, user)
    return await cost_report_service.cost_report(session, project_id=project_id, episode_id=episode_id)
