"""Agent 生成类工具的共用逻辑：从 ExecutionContext 取用户/画布 → 提交 job → 异步等待结果。"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import chat_generation_service as chat_jobs
from app.services.execution_context import get_execution_context

Submit = Callable[..., Awaitable[Any]]


async def submit_and_wait(submit: Submit, *, timeout: float) -> dict[str, Any]:
    """提交一个聊天来源的 job 并轮询到终态。

    返回 wait_for_job 的结果；预算超限 / 参数错误抛 ChatGenerationError（此时没有建 job）。
    只通过数据库读 job 状态，Worker 在本进程还是独立进程都可以。
    """
    from app.db.database import SessionLocal

    ctx = get_execution_context()
    if not ctx.user_id:
        raise chat_jobs.ChatGenerationError("无法确定当前用户，不能提交生成任务")
    session: AsyncSession
    async with SessionLocal() as session:
        job = await submit(session, user_id=int(ctx.user_id), canvas_id=ctx.canvas_id)
        job_id = job.id
    return await chat_jobs.wait_for_job(job_id, timeout=timeout)


def timeout_payload(outcome: dict[str, Any], label: str) -> str:
    job_id = outcome["job_id"]
    return json.dumps(
        {
            "job_id": job_id,
            "status": "running",
            "message": f"{label}任务仍在进行，job_id={job_id}，稍后可在画布/任务列表查看结果",
        },
        ensure_ascii=False,
    )
