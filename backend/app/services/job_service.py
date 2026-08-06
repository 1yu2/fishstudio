"""GenerationJob 生命周期服务：创建（幂等）、租约、状态流转、租约回收。"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GenerationJob, WorkflowRun

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
        status="pending",
    )
    session.add(job)
    await session.flush()
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
    stmt = (
        select(GenerationJob)
        .where(
            or_(
                # pending 且 (无 lease_until 或退避时间已到 — lease_until 复用为 run_after 语义)
                (GenerationJob.status == "pending")
                & or_(GenerationJob.lease_until.is_(None), GenerationJob.lease_until <= now),
                (GenerationJob.status == "leased") & (GenerationJob.lease_until < now),
            )
        )
        .where(
            or_(
                GenerationJob.workflow_run_id.is_(None),
                GenerationJob.workflow_run_id.notin_(blocked_run_ids),
            )
        )
        .order_by(GenerationJob.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    if operations:
        stmt = stmt.where(GenerationJob.operation.in_(operations))
    result = await session.execute(stmt)
    jobs = list(result.scalars().all())
    for job in jobs:
        job.status = "leased"
        job.lease_until = now + timedelta(seconds=lease_seconds)
        job.attempt += 1
    await session.flush()
    return jobs


async def mark_running(session: AsyncSession, job: GenerationJob) -> None:
    job.status = "running"
    job.started_at = job.started_at or _utcnow()
    await session.flush()


async def mark_succeeded(session: AsyncSession, job: GenerationJob, result: dict) -> None:
    job.status = "succeeded"
    job.progress = 100
    job.result = result
    job.error_code = ""
    job.error = ""
    job.finished_at = _utcnow()
    await session.flush()


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
    await session.flush()


async def mark_canceled(session: AsyncSession, job: GenerationJob) -> None:
    job.status = "canceled"
    job.finished_at = _utcnow()
    await session.flush()


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
    """外部任务未完成，重新入队等下一轮轮询。

    状态轮询不算一次执行尝试，把 lease 时加的 attempt 减回，避免烧光 max_attempts。
    lease_until 同样复用为 run_after，控制轮询间隔。
    """
    job.status = "pending"
    job.attempt = max(0, job.attempt - 1)
    job.lease_until = _utcnow() + timedelta(seconds=delay_seconds)
    await session.flush()


async def recover_expired_leases(session: AsyncSession) -> int:
    """把租约过期的 leased/running job 重置为 pending（worker 崩溃回收）。返回回收数。"""
    now = _utcnow()
    stmt = select(GenerationJob).where(
        GenerationJob.status.in_(["leased", "running"]),
        GenerationJob.lease_until.is_not(None),
        GenerationJob.lease_until < now,
    )
    result = await session.execute(stmt)
    jobs = list(result.scalars().all())
    for job in jobs:
        job.status = "pending"
        job.lease_until = None
    await session.flush()
    if jobs:
        logger.info(f"回收过期租约 job: {len(jobs)} 个")
    return len(jobs)
