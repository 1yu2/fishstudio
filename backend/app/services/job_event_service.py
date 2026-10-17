"""持久化 Job 事件与可选 Redis 通知。"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GenerationJob, JobEvent

CHANNEL = "fishstudio:job-events"
WAKE_CHANNEL = "fishstudio:job-ready"
_local_waiters: set[asyncio.Future[None]] = set()


def event_payload(row: JobEvent) -> dict[str, Any]:
    return {
        "sequence": row.id,
        "event": row.event,
        "project_id": row.project_id,
        "run_id": row.run_id,
        "step_id": row.step_id,
        "job_id": row.job_id,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "status": row.status,
        "progress": row.progress,
        "payload": row.payload or {},
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
    }


async def _publish(channel: str, payload: dict[str, Any]) -> None:
    for waiter in list(_local_waiters):
        if not waiter.done():
            waiter.set_result(None)
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        return
    try:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_url, decode_responses=True)
        try:
            await client.publish(channel, json.dumps(payload, ensure_ascii=False))
        finally:
            await client.aclose()
    except Exception:
        # Redis 是通知层；不可用时数据库仍是任务真相源。
        return


async def emit(
    session: AsyncSession, job: GenerationJob, event: str, *, payload: Optional[dict[str, Any]] = None
) -> JobEvent:
    row = JobEvent(
        event=event,
        project_id=job.project_id,
        run_id=job.workflow_run_id or "",
        step_id=job.workflow_step_id or "",
        job_id=job.id,
        entity_type="shot" if job.shot_id else "project",
        entity_id=job.shot_id or job.project_id,
        status=job.status,
        progress=job.progress,
        payload=payload or {},
    )
    if not hasattr(session, "add"):
        return row
    session.add(row)
    await session.flush()
    await _publish(CHANNEL, event_payload(row))
    return row


async def notify_ready(job_id: str) -> None:
    await _publish(WAKE_CHANNEL, {"job_id": job_id})


async def wait_for_work(timeout: float) -> None:
    """Redis 可用时订阅唤醒；否则用进程内事件并保留超时 DB 扫描。"""
    redis_url = os.getenv("REDIS_URL", "").strip()
    if redis_url:
        try:
            from redis.asyncio import Redis

            client = Redis.from_url(redis_url, decode_responses=True)
            pubsub = client.pubsub()
            await pubsub.subscribe(WAKE_CHANNEL)
            try:
                await pubsub.get_message(ignore_subscribe_messages=True, timeout=timeout)
            finally:
                await pubsub.unsubscribe(WAKE_CHANNEL)
                await pubsub.aclose()
                await client.aclose()
            return
        except Exception:
            pass
    waiter = asyncio.get_running_loop().create_future()
    _local_waiters.add(waiter)
    try:
        await asyncio.wait_for(waiter, timeout=timeout)
    except TimeoutError:
        pass
    finally:
        _local_waiters.discard(waiter)


async def list_events(
    session: AsyncSession, project_id: str, *, after: int = 0, limit: int = 200
) -> list[JobEvent]:
    rows = await session.execute(
        select(JobEvent)
        .where(JobEvent.project_id == project_id, JobEvent.id > after)
        .order_by(JobEvent.id)
        .limit(min(limit, 500))
    )
    return list(rows.scalars())


async def emit_batch_progress(session: AsyncSession, job: GenerationJob) -> JobEvent:
    filters = [GenerationJob.project_id == job.project_id]
    if job.workflow_run_id:
        filters.append(GenerationJob.workflow_run_id == job.workflow_run_id)
    # 轻量 session 适配器（单元测试/离线执行器）没有查询能力；仍发出当前任务进度，
    # 正式 AsyncSession 则从数据库聚合完整批次。
    if not hasattr(session, "execute"):
        counts = {job.status: 1}
        return await emit(
            session,
            job,
            "batch.progress",
            payload={
                "completed": int(job.status in {"succeeded", "failed", "canceled"}),
                "total": 1,
                "counts": counts,
            },
        )
    rows = await session.execute(
        select(GenerationJob.status, func.count(GenerationJob.id))
        .where(*filters)
        .group_by(GenerationJob.status)
    )
    try:
        counts = {status: count for status, count in rows}
    except TypeError:
        counts = {job.status: 1}
    total = sum(counts.values())
    completed = sum(counts.get(status, 0) for status in ("succeeded", "failed", "canceled"))
    return await emit(
        session,
        job,
        "batch.progress",
        payload={"completed": completed, "total": total, "counts": counts},
    )
