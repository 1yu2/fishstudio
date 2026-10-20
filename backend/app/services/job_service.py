"""GenerationJob 生命周期服务：创建（幂等）、租约、状态流转、租约回收。"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import GenerationJob, SeriesProject, WorkflowRun

logger = logging.getLogger(__name__)

TERMINAL_STATES = ("succeeded", "failed", "canceled")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def create_job(
    session: AsyncSession,
    *,
    operation: str,
    project_id: str,
    shot_id: Optional[str] = None,
    params: dict,
    idempotency_key: str,
    max_attempts: int = 3,
    provider: str = "",
    model: str = "",
    priority: int = 0,
    worker_class: str = "media",
) -> GenerationJob:
    """按 idempotency_key 幂等创建 job。已存在则直接返回已有 job。"""
    existing = await session.execute(
        select(GenerationJob).where(GenerationJob.idempotency_key == idempotency_key)
    )
    job = existing.scalar_one_or_none()
    if job is not None:
        return job
    job = GenerationJob(
        operation=operation,
        project_id=project_id,
        shot_id=shot_id,
        params=params,
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
        provider=provider,
        model=model,
        priority=priority,
        worker_class=worker_class,
        status="pending",
    )
    session.add(job)
    await session.flush()
    from app.services import job_event_service

    await job_event_service.emit(session, job, "job.queued")
    await job_event_service.notify_ready(job.id)
    return job


async def get_job(session: AsyncSession, job_id: str) -> Optional[GenerationJob]:
    result = await session.execute(select(GenerationJob).where(GenerationJob.id == job_id))
    return result.scalar_one_or_none()


async def list_jobs(
    session: AsyncSession,
    project_id: Optional[str] = None,
    shot_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> list[GenerationJob]:
    stmt = select(GenerationJob).order_by(GenerationJob.created_at.desc()).limit(limit)
    if project_id:
        stmt = stmt.where(GenerationJob.project_id == project_id)
    if shot_id:
        stmt = stmt.where(GenerationJob.shot_id == shot_id)
    if status:
        stmt = stmt.where(GenerationJob.status == status)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def lease_jobs(
    session: AsyncSession,
    *,
    operations: Optional[Sequence[str]] = None,
    limit: int = 5,
    lease_seconds: int = 300,
    worker_id: str = "worker",
    worker_classes: Optional[Sequence[str]] = None,
    max_user_active: int = 10,
    max_project_active: int = 5,
    max_deployment_active: int = 3,
) -> list[GenerationJob]:
    """领取待执行 job（SKIP LOCKED，多 worker 安全）。

    可领取: status=pending 且未到退避时间，或 status=leased 且租约过期（崩溃回收）。
    attempt+=1 表示一次"执行尝试"；轮询重入队的 job 由 worker 把 attempt 减回。

    P1-10：排除 paused/canceled run 下的 job（run 恢复后自然重新可领）；
    workflow_run_id 为空的旧 job（非 workflow 来源）不受影响。
    注意不能用 OUTER JOIN + FOR UPDATE（PG 报 FeatureNotSupportedError），
    改用 NOT IN 子查询。
    """
    now = _utcnow()
    blocked_run_ids = select(WorkflowRun.id).where(WorkflowRun.status.in_(["paused", "canceled"]))
    active_states = ["leased", "running"]
    project_job = aliased(GenerationJob)
    deployment_job = aliased(GenerationJob)
    user_job = aliased(GenerationJob)
    user_project = aliased(SeriesProject)
    active_for_project = (
        select(func.count(project_job.id))
        .where(project_job.project_id == GenerationJob.project_id, project_job.status.in_(active_states))
        .correlate(GenerationJob)
        .scalar_subquery()
    )
    active_for_deployment = (
        select(func.count(deployment_job.id))
        .where(
            deployment_job.provider == GenerationJob.provider,
            deployment_job.model == GenerationJob.model,
            deployment_job.status.in_(active_states),
        )
        .correlate(GenerationJob)
        .scalar_subquery()
    )
    active_for_user = (
        select(func.count(user_job.id))
        .join(user_project, user_project.id == user_job.project_id)
        .where(user_project.user_id == SeriesProject.user_id, user_job.status.in_(active_states))
        .correlate(SeriesProject)
        .scalar_subquery()
    )
    eligible = (
        select(
            GenerationJob.id.label("job_id"),
            func.row_number()
            .over(
                partition_by=SeriesProject.user_id,
                order_by=(GenerationJob.priority.desc(), GenerationJob.created_at),
            )
            .label("user_rank"),
            GenerationJob.priority.label("priority"),
            GenerationJob.created_at.label("created_at"),
        )
        .join(SeriesProject, SeriesProject.id == GenerationJob.project_id)
        .where(
            or_(
                and_(
                    or_(
                        # pending 且 (无 lease_until 或退避时间已到 — lease_until 复用为 run_after 语义)
                        (GenerationJob.status == "pending")
                        & or_(GenerationJob.lease_until.is_(None), GenerationJob.lease_until <= now),
                        (GenerationJob.status == "leased") & (GenerationJob.lease_until < now),
                    ),
                    active_for_user < max_user_active,
                    active_for_project < max_project_active,
                    active_for_deployment < max_deployment_active,
                ),
                # 已提交给供应商、到了下次轮询时间的任务：已经占着并发名额，不再受限，否则会互相卡死
                (GenerationJob.status == "running")
                & (GenerationJob.leased_by == "")
                & (GenerationJob.lease_until <= now),
            )
        )
        .where(
            or_(
                GenerationJob.workflow_run_id.is_(None),
                GenerationJob.workflow_run_id.notin_(blocked_run_ids),
            )
        )
    )
    if operations:
        eligible = eligible.where(GenerationJob.operation.in_(operations))
    if worker_classes:
        eligible = eligible.where(GenerationJob.worker_class.in_(worker_classes))
    candidates = eligible.subquery()
    stmt = (
        select(GenerationJob)
        .join(candidates, candidates.c.job_id == GenerationJob.id)
        .order_by(candidates.c.user_rank, candidates.c.priority.desc(), candidates.c.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True, of=GenerationJob)
    )
    result = await session.execute(stmt)
    jobs = list(result.scalars().all())
    for job in jobs:
        # 继续轮询的任务保持 running，界面不会在“生成中”和“准备中”之间跳
        if job.status != "running":
            job.status = "leased"
        job.lease_until = now + timedelta(seconds=lease_seconds)
        job.heartbeat_at = now
        job.leased_by = worker_id
        job.attempt += 1
    await session.flush()
    return jobs


async def mark_running(session: AsyncSession, job: GenerationJob) -> None:
    already_running = job.status == "running"
    job.status = "running"
    job.started_at = job.started_at or _utcnow()
    job.heartbeat_at = _utcnow()
    await session.flush()
    if already_running:
        return  # 轮询中每轮都会调用，只在第一次进入 running 时记事件
    from app.services import job_event_service

    await job_event_service.emit(session, job, "job.running")


async def mark_succeeded(session: AsyncSession, job: GenerationJob, result: dict) -> None:
    job.status = "succeeded"
    job.progress = 100
    job.result = result
    job.error_code = ""
    job.error = ""
    job.finished_at = _utcnow()
    job.lease_until = None
    await session.flush()
    from app.services import job_event_service

    await job_event_service.emit(session, job, "job.succeeded", payload={"result": result})


async def mark_failed(
    session: AsyncSession,
    job: GenerationJob,
    *,
    error_code: str,
    error: str,
    retryable: bool = False,
) -> None:
    """失败处理：可重试且未达 max_attempts → 重置 pending + 指数退避；否则终态 failed。

    退避复用 lease_until 作为 run_after 语义：pending 且 lease_until > now 的 job
    不会被 lease_jobs 领到，时间到后自然可领。
    """
    job.error_code = error_code
    job.error = error[:2000]
    if retryable and job.attempt < job.max_attempts:
        backoff_minutes = 2**job.attempt  # 指数退避：2^attempt 分钟
        job.status = "pending"
        job.lease_until = _utcnow() + timedelta(minutes=backoff_minutes)
    else:
        job.status = "failed"
        job.finished_at = _utcnow()
        job.dead_lettered_at = _utcnow()
    await session.flush()
    from app.services import job_event_service

    event = "job.retry_scheduled" if job.status == "pending" else "job.dead_lettered"
    await job_event_service.emit(session, job, event, payload={"error_code": error_code})
    if job.status == "pending":
        await job_event_service.notify_ready(job.id)


async def mark_canceled(session: AsyncSession, job: GenerationJob) -> None:
    job.status = "canceled"
    job.finished_at = _utcnow()
    await session.flush()
    from app.services import job_event_service

    await job_event_service.emit(session, job, "job.canceled")


async def cancel_job(session: AsyncSession, job_id: str) -> bool:
    """原子取消（P1-9/P1-10 并发保护）：UPDATE ... WHERE status IN (pending/leased/running)。

    影响行数 0（job 不存在或已终态）返回 False——防止 worker 已标
    succeeded/failed 后又被覆盖成 canceled（反之亦然）。成功时同步已加载的
    ORM 对象状态（synchronize_session="fetch"）。
    """
    stmt = (
        update(GenerationJob)
        .where(GenerationJob.id == job_id)
        .where(GenerationJob.status.in_(["pending", "leased", "running"]))
        .values(status="canceled", finished_at=_utcnow())
        .execution_options(synchronize_session="fetch")
    )
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount > 0


async def requeue_for_poll(session: AsyncSession, job: GenerationJob, delay_seconds: float = 3.0) -> None:
    """外部任务未完成，释放租约，等下一轮轮询。

    已提交给供应商的任务保持 running（只是没有 Worker 持有：leased_by 为空），
    界面和统计看到的就是“生成中”；还没提交的任务（如熔断时）回到 pending。
    状态轮询不算一次执行尝试，把 lease 时加的 attempt 减回，避免烧光 max_attempts。
    lease_until 复用为下次轮询时间。
    """
    job.status = "running" if job.external_task_id else "pending"
    job.leased_by = ""
    job.attempt = max(0, job.attempt - 1)
    job.lease_until = _utcnow() + timedelta(seconds=delay_seconds)
    await session.flush()


async def recover_expired_leases(session: AsyncSession) -> int:
    """回收 Worker 崩溃留下的过期租约。返回回收数。

    已提交给供应商的 running 任务不回到 pending：释放租约后保持 running，下一轮直接继续轮询；
    其余回到 pending 重新执行。等待轮询的 running（leased_by 为空）不是租约，不在此列。
    """
    now = _utcnow()
    stmt = select(GenerationJob).where(
        GenerationJob.status.in_(["leased", "running"]),
        GenerationJob.leased_by != "",
        GenerationJob.lease_until.is_not(None),
        GenerationJob.lease_until < now,
    )
    result = await session.execute(stmt)
    jobs = list(result.scalars().all())
    for job in jobs:
        waiting = job.status == "running" and bool(job.external_task_id)
        job.status = "running" if waiting else "pending"
        job.lease_until = now if waiting else None
        job.leased_by = ""
    await session.flush()
    if jobs:
        logger.info(f"回收过期租约 job: {len(jobs)} 个")
    return len(jobs)


async def heartbeat(session: AsyncSession, job_id: str, worker_id: str, *, lease_seconds: int = 300) -> bool:
    """只有当前 lease owner 能续租，防止旧 Worker 复活覆盖新租约。"""
    now = _utcnow()
    result = await session.execute(
        update(GenerationJob)
        .where(
            GenerationJob.id == job_id,
            GenerationJob.leased_by == worker_id,
            GenerationJob.status.in_(["leased", "running"]),
        )
        .values(heartbeat_at=now, lease_until=now + timedelta(seconds=lease_seconds))
    )
    await session.flush()
    return result.rowcount > 0
