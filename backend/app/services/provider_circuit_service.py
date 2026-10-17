"""持久化 Provider 熔断器，支持超时后的 half-open 恢复探测。"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProviderCircuit
from app.services.job_service import _utcnow


async def allow_request(session: AsyncSession, provider: str, deployment: str = "") -> bool:
    row = await session.scalar(
        select(ProviderCircuit).where(
            ProviderCircuit.provider == provider, ProviderCircuit.deployment == deployment
        )
    )
    if row is None or row.state == "closed":
        return True
    if row.probe_after and row.probe_after <= _utcnow():
        row.state = "half_open"
        await session.flush()
        return True
    return False


async def record_success(session: AsyncSession, provider: str, deployment: str = "") -> None:
    row = await session.scalar(
        select(ProviderCircuit).where(
            ProviderCircuit.provider == provider, ProviderCircuit.deployment == deployment
        )
    )
    if row:
        row.state, row.failure_count, row.last_error = "closed", 0, ""
        row.opened_at = row.probe_after = None
        await session.flush()


async def record_failure(
    session: AsyncSession,
    provider: str,
    deployment: str = "",
    *,
    error: str = "",
    threshold: int = 5,
    recovery_seconds: int = 60,
) -> ProviderCircuit:
    row = await session.scalar(
        select(ProviderCircuit).where(
            ProviderCircuit.provider == provider, ProviderCircuit.deployment == deployment
        )
    )
    if row is None:
        row = ProviderCircuit(provider=provider, deployment=deployment)
        session.add(row)
    row.failure_count = int(row.failure_count or 0) + 1
    row.last_error = error[:1000]
    if row.failure_count >= threshold or row.state == "half_open":
        row.state = "open"
        row.opened_at = _utcnow()
        row.probe_after = row.opened_at + timedelta(seconds=recovery_seconds)
    await session.flush()
    return row
