"""视频取回失败的重试防重复扣费：厂商任务已成功后，重试只重新取回，绝不重新 submit。"""

import pytest

from app.db.models import GenerationJob
from app.providers.base import ProviderResult
from app.services import job_service
from app.worker import generation_worker as worker

EXTERNAL_ID = "ext-task-1"


class _Session:
    async def flush(self):
        pass

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass


class _Provider:
    """已提交成功的厂商任务：状态成功，但取回产物按脚本失败。"""

    name = "fake_video"

    def __init__(self, fetch_results):
        self.fetch_results = list(fetch_results)
        self.submit_calls = 0
        self.status_calls = 0
        self.fetch_calls = 0

    async def submit(self, req, *, model=None):
        self.submit_calls += 1  # 每次 submit 都是一次新的厂商计费
        raise AssertionError("已有 external_task_id 的任务不应重新 submit")

    async def get_status(self, external_task_id, protocol=None):
        self.status_calls += 1
        return ProviderResult(success=True, provider=self.name, external_request_id=external_task_id)

    async def fetch_result(self, external_task_id, protocol=None):
        self.fetch_calls += 1
        assert external_task_id == EXTERNAL_ID
        return self.fetch_results.pop(0)


def _fail(code="network_error", retryable=True):
    return ProviderResult(
        success=False,
        provider="fake_video",
        external_request_id=EXTERNAL_ID,
        error_code=code,
        error_message="下载失败",
        retryable=retryable,
    )


def _job(max_attempts=3):
    return GenerationJob(
        id="job-1",
        operation="generate_video",
        project_id="p1",
        idempotency_key="k1",
        provider="fake_video",
        model="m",
        external_task_id=EXTERNAL_ID,
        attempt=0,
        max_attempts=max_attempts,
        status="pending",
        params={"prompt": "雨夜回头", "duration": 5, "routing_snapshot": {"estimated_total_cost": "1.5"}},
    )


@pytest.fixture
def harness(monkeypatch):
    """替掉数据库相关调用；finalize_job 改成真走 job_service.mark_failed，并记录每次的用量入参。"""
    state = {"finalized": [], "usage": []}

    async def no_abort(session, job):
        return False

    async def noop(*args, **kwargs):
        return None

    async def mark_running(session, job):
        job.status = "running"

    async def finalize(
        session, job, *, success, error_code="", error="", retryable=False, usage_fields=None, **kw
    ):
        assert not success
        if usage_fields:
            state["usage"].append(usage_fields)  # 对应一次 usage_service.record_usage（费用入账）
        await job_service.mark_failed(session, job, error_code=error_code, error=error, retryable=retryable)
        state["finalized"].append(job.status)

    monkeypatch.setattr(worker, "_should_abort_before_submit", no_abort)
    monkeypatch.setattr(worker, "_ensure_media_invocation", noop)
    monkeypatch.setattr(worker.job_service, "mark_running", mark_running)
    monkeypatch.setattr(worker, "finalize_job", finalize)

    def install(provider):
        monkeypatch.setattr(worker._router, "providers", [provider], raising=False)

    state["install"] = install
    return state


async def _run_attempts(job, rounds):
    session = _Session()
    for _ in range(rounds):
        if job.status != "pending":
            break
        job.attempt += 1  # lease 时 attempt + 1
        await worker.execute_generate_video(session, job)


async def test_fetch_failure_retries_only_refetch_never_resubmit(harness):
    provider = _Provider([_fail(), _fail(), _fail()])
    harness["install"](provider)
    job = _job(max_attempts=3)

    await _run_attempts(job, rounds=5)

    assert provider.submit_calls == 0  # 绝不重新提交（= 不会再收一次钱）
    assert provider.fetch_calls == 3  # 有限次数：用完 max_attempts 就停
    assert job.external_task_id == EXTERNAL_ID  # 任务号全程保留，重试复用同一个
    assert harness["finalized"] == ["pending", "pending", "failed"]
    assert job.dead_lettered_at is not None  # 用尽后终态失败，不再可重试


async def test_cost_recorded_once_when_retries_exhausted(harness):
    harness["install"](_Provider([_fail(), _fail(), _fail()]))
    job = _job(max_attempts=3)

    await _run_attempts(job, rounds=5)

    # 厂商已收费：只在最终失败时按估价入账一次，重试途中不重复记
    assert len(harness["usage"]) == 1
    usage = harness["usage"][0]
    assert usage["status"] == "failed" and str(usage["estimated_cost"]) == "1.5"


async def test_non_retryable_fetch_failure_fails_immediately_with_cost(harness):
    provider = _Provider([_fail(code="artifact_invalid", retryable=False)])
    harness["install"](provider)
    job = _job()

    await _run_attempts(job, rounds=3)

    assert provider.fetch_calls == 1 and provider.submit_calls == 0
    assert job.status == "failed"
    assert len(harness["usage"]) == 1


async def test_fetch_pending_is_bounded_with_increasing_backoff_and_cost(harness):
    provider = _Provider([_fail(code="pending")] * 6)
    harness["install"](provider)
    job = _job()
    delays = []
    for _ in range(6):
        if job.status == "failed":
            break
        await _run_attempts(job, 1)
        if job.status == "pending":
            delays.append((job.lease_until - job_service._utcnow()).total_seconds())
    assert provider.fetch_calls == 3 and provider.submit_calls == 0
    assert len(delays) == 2 and delays[1] > delays[0]
    assert job.status == "failed" and job.dead_lettered_at is not None
    assert len(harness["usage"]) == 1
    assert str(harness["usage"][0]["estimated_cost"]) == "1.5"


async def test_query_failure_between_download_failures_does_not_double_charge(harness):
    class QueryFailure(_Provider):
        async def get_status(self, external_task_id, protocol=None):
            result = await super().get_status(external_task_id, protocol)
            return _fail("timeout") if self.status_calls == 2 else result

    provider = QueryFailure([_fail()] * 3)
    harness["install"](provider)
    job = _job()
    await _run_attempts(job, 6)
    assert job.status == "failed"
    assert provider.submit_calls == 0
    assert len(harness["usage"]) == 1
