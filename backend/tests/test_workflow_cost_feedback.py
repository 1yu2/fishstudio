"""P1/P2 修复的服务层测试：finalize 回推 / cancel 回推 / retry 新 job / 费用报价与
累计预算 / render final 缺镜头 / feedback 选中 / 个人资产复制 / LLM Planner 回退 /
迁移 0004 stub op 验证。

与 test_workflow.py 同一策略：无真 DB，service 层用 monkeypatch/stub（JSONB 与
SQLite 不兼容，见 conftest）。
"""

import asyncio
import importlib.util
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db.models import AssetCard
from app.providers import pricing
from app.services import (
    asset_service,
    cost_service,
    feedback_service,
    job_planner,
    planner_service,
    timeline_renderer,
    workflow_service,
)
from app.worker import generation_worker as worker_mod

BACKEND_DIR = Path(__file__).resolve().parent.parent


class _FakeSession:
    """最小 AsyncSession stub：get/add/flush/commit/refresh/scalar/execute。"""

    def __init__(self, get_map=None):
        self.get_map = get_map or {}
        self.added = []
        self.commits = 0
        self.scalars: list = []
        self.execute_counts: list = []

    async def get(self, model, key):
        name = getattr(model, "__name__", str(model))
        return self.get_map.get((name, key))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj, **kwargs):
        # 真 DB 下 flush/refresh 会应用 Column default；stub 里手动套 Python 侧默认值
        table = getattr(obj, "__table__", None)
        if table is None:
            return
        for col in table.columns:
            if getattr(obj, col.name, None) is None and col.default is not None:
                default = col.default
                if getattr(default, "is_scalar", False):
                    setattr(obj, col.name, default.arg)
                elif getattr(default, "is_callable", False):
                    setattr(obj, col.name, default.arg(None))

    async def rollback(self):
        pass

    async def scalar(self, stmt):
        return self.scalars.pop(0) if self.scalars else None

    async def execute(self, stmt):
        # on_job_finished 风格：返回 job_rows（按 get_map 中 WorkflowStep.attempt 过滤，
        # 模拟 SQL WHERE step_attempt == step.attempt）
        if hasattr(self, "job_rows"):
            attempt = 0
            for (name, _key), obj in self.get_map.items():
                if name == "WorkflowStep":
                    attempt = getattr(obj, "attempt", 0)
            rows = [
                j for j in self.job_rows
                if getattr(j, "step_attempt", 0) == attempt
            ]
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: rows, first=lambda: rows[0] if rows else None),
                scalar_one=lambda: len(rows),
            )
        value = self.execute_counts.pop(0) if self.execute_counts else 0
        return SimpleNamespace(scalar_one=lambda: value)


def _fake_job(**over):
    base = dict(
        id="j1", project_id="p1", shot_id="sh1",
        workflow_run_id="r1", workflow_step_id="st1",
        operation="generate_video", provider="minimax", model="MiniMax-H3",
        params={"step_id": "st1", "run_id": "r1"},
        idempotency_key="video:r1:node:sh1:attempt0",
        external_task_id="", status="pending", progress=0,
        attempt=1, max_attempts=3, error_code="", error="",
        step_attempt=0, logical_job_key="generate_video:r1:node:sh1", superseded_by="",
        lease_until=None, result={}, started_at=None, finished_at=None,
        created_at=None, updated_at=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


# ─────────────────────────────────────────
# P1-8 finalize_job 统一终态
# ─────────────────────────────────────────


class TestFinalizeJob:
    def test_terminal_failure_pushes_workflow(self, monkeypatch):
        calls = []

        async def _fake_on_finished(session, job):
            calls.append(job.id)

        monkeypatch.setattr(workflow_service, "on_job_finished", _fake_on_finished)
        session = _FakeSession()
        job = _fake_job()
        asyncio.run(
            worker_mod.finalize_job(
                session, job, success=False, error_code="no_provider", error="x"
            )
        )
        assert job.status == "failed"
        assert job.error_code == "no_provider"
        assert calls == ["j1"]  # 终态回推
        assert session.commits >= 1

    def test_success_pushes_workflow_and_merges_artifact(self, monkeypatch):
        calls = []

        async def _fake_on_finished(session, job):
            calls.append(job.id)

        monkeypatch.setattr(workflow_service, "on_job_finished", _fake_on_finished)
        session = _FakeSession()
        job = _fake_job()
        asyncio.run(
            worker_mod.finalize_job(
                session, job, success=True, result={"model": "m"},
                artifact={"artifact_id": "a1", "url": "/api/artifacts/a1/download"},
            )
        )
        assert job.status == "succeeded"
        assert job.result["artifact_id"] == "a1"
        assert job.result["model"] == "m"
        assert calls == ["j1"]

    def test_retryable_failure_not_terminal_no_push(self, monkeypatch):
        calls = []

        async def _fake_on_finished(session, job):
            calls.append(job.id)

        monkeypatch.setattr(workflow_service, "on_job_finished", _fake_on_finished)
        session = _FakeSession()
        job = _fake_job(attempt=1, max_attempts=3)
        asyncio.run(
            worker_mod.finalize_job(
                session, job, success=False, error_code="submit_failed",
                error="boom", retryable=True,
            )
        )
        assert job.status == "pending"  # 重置回 pending 等退避重试
        assert calls == []  # 非终态不回推


# ─────────────────────────────────────────
# P1-9 cancel 回推 + 聚合语义
# ─────────────────────────────────────────


class TestCancelAndAggregation:
    def test_cancel_endpoint_pushes_workflow(self, monkeypatch):
        from app.routers import jobs as jobs_router

        job = _fake_job(status="pending")
        pushed = []

        async def _fake_checked(session, job_id, user):
            return job

        async def _fake_cancel(session, job_id):
            job.status = "canceled"
            return True

        async def _fake_on_finished(session, j):
            pushed.append(j.id)

        monkeypatch.setattr(jobs_router, "_get_job_checked", _fake_checked)
        monkeypatch.setattr(jobs_router.job_service, "cancel_job", _fake_cancel)
        monkeypatch.setattr(jobs_router.workflow_service, "on_job_finished", _fake_on_finished)

        session = _FakeSession()
        res = asyncio.run(
            jobs_router.cancel_job("j1", session=session, user=SimpleNamespace(id=1))
        )
        assert res["status"] == "canceled"
        assert pushed == ["j1"]

    def test_atomic_cancel_terminal_returns_false(self):
        """原子取消：UPDATE 影响行数 0（已终态/不存在）→ False，不再读改写。"""
        from app.services import job_service

        class _ZeroRowSession(_FakeSession):
            async def execute(self, stmt):
                return SimpleNamespace(rowcount=0)

        ok = asyncio.run(job_service.cancel_job(_ZeroRowSession(), "j1"))
        assert ok is False

        class _OneRowSession(_FakeSession):
            async def execute(self, stmt):
                return SimpleNamespace(rowcount=1)

        ok = asyncio.run(job_service.cancel_job(_OneRowSession(), "j1"))
        assert ok is True

    def _run_aggregation(self, monkeypatch, jobs):
        """jobs：当前 attempt 代际的 job 列表（SimpleNamespace，带 status/superseded_by）。"""
        step = SimpleNamespace(
            id="st1", run_id="r1", status="running", attempt=0, error_code="", error=""
        )
        session = _FakeSession(get_map={("WorkflowStep", "st1"): step})
        session.job_rows = jobs  # on_job_finished 查询的 GenerationJob 行

        async def _noop_advance(session, run_id):
            return None

        monkeypatch.setattr(workflow_service, "advance_run", _noop_advance)
        job = _fake_job()
        asyncio.run(workflow_service.on_job_finished(session, job))
        return step

    @staticmethod
    def _row(status, superseded_by="", step_attempt=0):
        return SimpleNamespace(
            status=status, superseded_by=superseded_by, step_attempt=step_attempt
        )

    def test_all_succeeded(self, monkeypatch):
        step = self._run_aggregation(monkeypatch, [self._row("succeeded")])
        assert step.status == "succeeded"

    def test_partially_canceled(self, monkeypatch):
        step = self._run_aggregation(
            monkeypatch, [self._row("succeeded"), self._row("canceled")]
        )
        assert step.status == "failed"
        assert step.error_code == "partially_canceled"

    def test_job_failed(self, monkeypatch):
        step = self._run_aggregation(
            monkeypatch, [self._row("failed"), self._row("canceled")]
        )
        assert step.status == "failed"
        assert step.error_code == "job_failed"

    def test_remaining_jobs_no_change(self, monkeypatch):
        step = self._run_aggregation(
            monkeypatch, [self._row("running"), self._row("succeeded")]
        )
        assert step.status == "running"

    def test_superseded_failed_job_ignored(self, monkeypatch):
        """job 级 retry：旧 failed（superseded_by 非空）不影响新一代 succeeded 的聚合。"""
        step = self._run_aggregation(
            monkeypatch,
            [self._row("failed", superseded_by="j2"), self._row("succeeded")],
        )
        assert step.status == "succeeded"

    def test_old_attempt_jobs_ignored(self, monkeypatch):
        """step retry 后（attempt=1）：旧 attempt=0 的 failed 不影响当前聚合。"""
        step = SimpleNamespace(
            id="st1", run_id="r1", status="running", attempt=1, error_code="", error=""
        )
        session = _FakeSession(get_map={("WorkflowStep", "st1"): step})
        # _FakeSession 按 step.attempt 过滤 job_rows（模拟 SQL WHERE step_attempt == attempt）
        session.job_rows = [
            self._row("failed", step_attempt=0),
            self._row("succeeded", step_attempt=1),
        ]

        async def _noop_advance(session, run_id):
            return None

        monkeypatch.setattr(workflow_service, "advance_run", _noop_advance)
        asyncio.run(workflow_service.on_job_finished(session, _fake_job()))
        assert step.status == "succeeded"


# ─────────────────────────────────────────
# P1-6 retry_step 恢复 run
# ─────────────────────────────────────────


class TestRetryStep:
    def _setup(self, monkeypatch, step_status, run_status):
        step = SimpleNamespace(
            id="st1", run_id="r1", status=step_status, attempt=1,
            error="e", error_code="job_failed",
        )
        run = SimpleNamespace(id="r1", status=run_status, finished_at="2026-08-04")
        session = _FakeSession(
            get_map={("WorkflowStep", "st1"): step, ("WorkflowRun", "r1"): run}
        )

        async def _noop_advance(session, run_id):
            return None

        monkeypatch.setattr(workflow_service, "advance_run", _noop_advance)
        return session, step, run

    def test_failed_step_recovers_failed_run(self, monkeypatch):
        session, step, run = self._setup(monkeypatch, "failed", "failed")
        asyncio.run(workflow_service.retry_step(session, "st1"))
        assert step.status == "ready"
        assert step.attempt == 2
        assert step.error_code == ""
        assert run.status == "ready"
        assert run.finished_at is None

    def test_failed_step_recovers_paused_run(self, monkeypatch):
        session, step, run = self._setup(monkeypatch, "failed", "paused")
        asyncio.run(workflow_service.retry_step(session, "st1"))
        assert run.status == "ready"

    def test_canceled_step_retryable(self, monkeypatch):
        session, step, run = self._setup(monkeypatch, "canceled", "failed")
        asyncio.run(workflow_service.retry_step(session, "st1"))
        assert step.status == "ready"

    def test_non_failed_step_rejected(self, monkeypatch):
        session, _, _ = self._setup(monkeypatch, "running", "running")
        with pytest.raises(ValueError):
            asyncio.run(workflow_service.retry_step(session, "st1"))

    def test_terminal_run_rejected(self, monkeypatch):
        session, _, _ = self._setup(monkeypatch, "failed", "succeeded")
        with pytest.raises(ValueError):
            asyncio.run(workflow_service.retry_step(session, "st1"))


# ─────────────────────────────────────────
# P1-7 retry 创建新 job
# ─────────────────────────────────────────


class TestRetryJobCreatesNew:
    def _setup(self, monkeypatch, old_job):
        from app.routers import jobs as jobs_router

        async def _fake_checked(session, job_id, user):
            return old_job

        async def _reserve(session, run_id, amount):
            return True

        monkeypatch.setattr(jobs_router, "_get_job_checked", _fake_checked)
        monkeypatch.setattr(cost_service, "reserve_budget", _reserve)
        return jobs_router

    def test_retry_budget_exceeded_409(self, monkeypatch):
        from fastapi import HTTPException

        old = _fake_job(status="failed")
        jobs_router = self._setup(monkeypatch, old)

        async def _deny(session, run_id, amount):
            return False

        monkeypatch.setattr(cost_service, "reserve_budget", _deny)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                jobs_router.retry_job("j1", session=_FakeSession(), user=SimpleNamespace(id=1))
            )
        assert exc.value.status_code == 409
        assert "budget_exceeded" in exc.value.detail

    def test_retry_clones_into_new_job(self, monkeypatch):
        old = _fake_job(status="failed", external_task_id="ext-123", error="x")
        jobs_router = self._setup(monkeypatch, old)
        session = _FakeSession()
        session.scalars = [None]  # retry:{key} 不存在
        res = asyncio.run(
            jobs_router.retry_job("j1", session=session, user=SimpleNamespace(id=1))
        )
        new_job = session.added[0]
        assert new_job.id != old.id
        assert new_job.idempotency_key == f"retry:{old.idempotency_key}"
        assert new_job.params["retry_of_job_id"] == old.id
        assert new_job.external_task_id == ""  # 不带入旧外部任务
        assert new_job.workflow_run_id == old.workflow_run_id
        assert new_job.workflow_step_id == old.workflow_step_id
        assert new_job.provider == old.provider and new_job.model == old.model
        # 重试代际字段：logical_job_key/step_attempt 继承，旧 job 回填 superseded_by（单事务内完成）
        assert new_job.logical_job_key == old.logical_job_key
        assert new_job.step_attempt == old.step_attempt
        assert old.superseded_by is not None and old.superseded_by != ""
        assert old.status == "failed"  # 旧 job 保持 failed 不动
        assert res["status"] == "pending"

    def test_retry_key_collision_appends_suffix(self, monkeypatch):
        old = _fake_job(status="failed")
        jobs_router = self._setup(monkeypatch, old)
        session = _FakeSession()
        session.scalars = ["existing-id", None]  # 第一次撞键，第二次可用
        asyncio.run(jobs_router.retry_job("j1", session=session, user=SimpleNamespace(id=1)))
        new_job = session.added[0]
        assert new_job.idempotency_key == f"retry:{old.idempotency_key}:2"


# ─────────────────────────────────────────
# P1-4 / P1-5 费用报价与累计预算
# ─────────────────────────────────────────


class TestCostQuoteAndBudget:
    def test_check_budget_cumulative(self):
        run = SimpleNamespace(cost_limit=Decimal("10"), estimated_cost=Decimal("8"))
        assert cost_service.check_budget(Decimal("2"), run) is True
        assert cost_service.check_budget(Decimal("3"), run) is False

    def test_check_budget_no_limit(self):
        run = SimpleNamespace(cost_limit=None, estimated_cost=Decimal("100"))
        assert cost_service.check_budget(Decimal("999"), run) is True

    def test_estimate_run_cost_items(self, monkeypatch):
        steps = [
            SimpleNamespace(
                node_key="shot.video.generate:ep_e1", node_type="shot.video.generate",
                entity_id="e1",
            ),
            SimpleNamespace(
                node_key="shot.image.generate:ep_e1", node_type="shot.image.generate",
                entity_id="e1",
            ),
            SimpleNamespace(
                node_key="episode.render:ep_e1", node_type="episode.render", entity_id="e1",
            ),
        ]
        shots = [
            SimpleNamespace(id="sh1", duration_ms=5000, dialogue_tracks=[], narration_tracks=[]),
            SimpleNamespace(id="sh2", duration_ms=5000, dialogue_tracks=[], narration_tracks=[]),
        ]

        async def _fake_steps(session, run):
            return steps

        async def _fake_shots(session, episode_id):
            return shots

        monkeypatch.setattr(cost_service, "_run_pending_job_steps", _fake_steps)
        monkeypatch.setattr(cost_service, "_run_all_job_steps", _fake_steps)
        monkeypatch.setattr(cost_service.job_planner, "_current_revision_shots", _fake_shots)

        run = SimpleNamespace(id="r1", input_snapshot={"default_policy": {}})
        quote = asyncio.run(cost_service.estimate_run_cost(_FakeSession(), run))

        by_op = {i.operation: i for i in quote.items}
        assert by_op["generate_video"].unit_count == 2  # 2 shots (R7 per-shot)
        assert Decimal(by_op["generate_video"].subtotal) == Decimal("5.000000")
        assert by_op["generate_image"].unit_count == 2
        assert by_op["render_episode"].subtotal == "0"
        assert Decimal(quote.total) == Decimal("5.400000")
        assert quote.price_table_version == pricing.PRICE_TABLE_VERSION
        assert quote.quote_id

    def test_planner_cost_limit_marks_step_failed(self, monkeypatch):
        step = SimpleNamespace(status="ready", error_code="", error="")
        run = SimpleNamespace(id="r1", cost_limit=Decimal("10"), estimated_cost=Decimal("8"))

        async def _deny(session, run_id, amount):
            return False

        monkeypatch.setattr(cost_service, "reserve_budget", _deny)
        session = _FakeSession()
        ok = asyncio.run(
            job_planner._reserve_budget_or_fail(session, run, step, Decimal("3"))
        )
        assert ok is False
        assert step.status == "failed"
        assert step.error_code == "cost_limit_exceeded"

    def test_validate_cost_quote_version(self, monkeypatch):
        async def _hash(session, run):
            return "h1"

        monkeypatch.setattr(cost_service, "compute_input_hash", _hash)
        future = "2999-01-01T00:00:00+00:00"
        session = _FakeSession()

        run = SimpleNamespace(input_snapshot={})
        ok, _ = asyncio.run(cost_service.validate_cost_quote(session, run, "q1"))
        assert ok is False  # 无 quote

        run = SimpleNamespace(
            input_snapshot={"cost_quote": {"quote_id": "q1", "expires_at": future}}
        )
        ok, reason = asyncio.run(cost_service.validate_cost_quote(session, run, "q1"))
        assert ok is False and "过期" in reason  # 价格表版本不一致

        run = SimpleNamespace(
            input_snapshot={
                "cost_quote": {
                    "quote_id": "q1",
                    "expires_at": future,
                    "price_table_version": pricing.PRICE_TABLE_VERSION,
                    "input_hash": "h1",
                }
            }
        )
        ok, _ = asyncio.run(cost_service.validate_cost_quote(session, run, "q1"))
        assert ok is True

    def test_validate_cost_quote_binding(self, monkeypatch):
        """复审 0.6：quote_id 不匹配 / 过期 / input_hash 变化 → 失效。"""
        future = "2999-01-01T00:00:00+00:00"
        quote = {
            "quote_id": "q1",
            "expires_at": future,
            "price_table_version": pricing.PRICE_TABLE_VERSION,
            "input_hash": "h1",
        }

        async def _hash(session, run):
            return "h1"

        monkeypatch.setattr(cost_service, "compute_input_hash", _hash)
        session = _FakeSession()
        run = SimpleNamespace(input_snapshot={"cost_quote": quote})

        # quote_id 必填
        ok, reason = asyncio.run(cost_service.validate_cost_quote(session, run, ""))
        assert ok is False and "quote_id" in reason
        # quote_id 不匹配
        ok, reason = asyncio.run(cost_service.validate_cost_quote(session, run, "q2"))
        assert ok is False and "不匹配" in reason
        # 已过期
        expired = {**quote, "expires_at": "2000-01-01T00:00:00+00:00"}
        run2 = SimpleNamespace(input_snapshot={"cost_quote": expired})
        ok, reason = asyncio.run(cost_service.validate_cost_quote(session, run2, "q1"))
        assert ok is False and "过期" in reason
        # input_hash 变化（报价后分镜/参数变化）
        async def _hash_changed(session, run):
            return "h2"

        monkeypatch.setattr(cost_service, "compute_input_hash", _hash_changed)
        ok, reason = asyncio.run(cost_service.validate_cost_quote(session, run, "q1"))
        assert ok is False and "失效" in reason


# ─────────────────────────────────────────
# 复审 0.6：费用 Gate（requires_cost_confirm）与原子预算预留
# ─────────────────────────────────────────


class TestCostGate:
    def test_requires_cost_confirm_flags(self):
        from app.services.workflow_nodes import NODE_TYPES

        assert NODE_TYPES["shot.image.generate"].requires_cost_confirm is True
        assert NODE_TYPES["shot.video.generate"].requires_cost_confirm is True
        assert NODE_TYPES["shot.audio.generate"].requires_cost_confirm is True
        # episode.render 是本地 ffmpeg 免费节点
        assert NODE_TYPES["episode.render"].requires_cost_confirm is False
        # 图片节点此前没有 requires_approval 也能被 Gate 拦截（修正复审 0.6）
        assert NODE_TYPES["shot.image.generate"].creates_jobs is True

    def test_gate_blocks_without_confirm(self, monkeypatch):
        run = SimpleNamespace(input_snapshot={"default_policy": {}})
        ok = asyncio.run(workflow_service._cost_gate_passed(_FakeSession(), run))
        assert ok is False

    def test_gate_blocks_invalid_quote(self, monkeypatch):
        async def _deny(session, run, quote_id):
            return False, "报价已过期"

        monkeypatch.setattr(cost_service, "validate_cost_quote", _deny)
        run = SimpleNamespace(
            input_snapshot={"cost_confirmed": True, "cost_quote": {"quote_id": "q1"}}
        )
        ok = asyncio.run(workflow_service._cost_gate_passed(_FakeSession(), run))
        assert ok is False

    def test_gate_passes_with_valid_quote(self, monkeypatch):
        async def _allow(session, run, quote_id):
            return True, ""

        monkeypatch.setattr(cost_service, "validate_cost_quote", _allow)
        run = SimpleNamespace(
            input_snapshot={"cost_confirmed": True, "cost_quote": {"quote_id": "q1"}}
        )
        ok = asyncio.run(workflow_service._cost_gate_passed(_FakeSession(), run))
        assert ok is True


class _ReserveSession(_FakeSession):
    """reserve_budget stub：execute 返回指定 run 行。"""

    def __init__(self, run):
        super().__init__()
        self._run = run

    async def execute(self, stmt):
        run = self._run
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(first=lambda: run, all=lambda: [run])
        )


class TestReserveBudget:
    def test_reserve_within_limit(self):
        run = SimpleNamespace(id="r1", cost_limit=Decimal("10"), estimated_cost=Decimal("8"))
        ok = asyncio.run(cost_service.reserve_budget(_ReserveSession(run), "r1", Decimal("2")))
        assert ok is True
        assert run.estimated_cost == Decimal("10")

    def test_reserve_over_limit_denied(self):
        run = SimpleNamespace(id="r1", cost_limit=Decimal("10"), estimated_cost=Decimal("8"))
        ok = asyncio.run(cost_service.reserve_budget(_ReserveSession(run), "r1", Decimal("3")))
        assert ok is False
        assert run.estimated_cost == Decimal("8")  # 不累加

    def test_reserve_no_limit(self):
        run = SimpleNamespace(id="r1", cost_limit=None, estimated_cost=None)
        ok = asyncio.run(cost_service.reserve_budget(_ReserveSession(run), "r1", Decimal("99")))
        assert ok is True
        assert run.estimated_cost == Decimal("99")

    def test_retry_cost_estimation(self):
        job = SimpleNamespace(
            operation="generate_video", provider="minimax", model="MiniMax-H3",
            params={"duration_ms": 6000, "resolution": "768P"},
        )
        assert cost_service.estimate_job_retry_cost(job) == Decimal("3.000000")
        img = SimpleNamespace(
            operation="generate_image", provider="", model="",
            params={"candidate_count": 2},
        )
        assert cost_service.estimate_job_retry_cost(img) == Decimal("0.400000")
        render = SimpleNamespace(operation="render_episode", provider="", model="", params={})
        assert cost_service.estimate_job_retry_cost(render) == Decimal("0")


# ─────────────────────────────────────────
# P1-11 render final 缺镜头（纯逻辑部分）
# ─────────────────────────────────────────


class TestRenderMode:
    def test_find_missing_shot_videos(self, tmp_path):
        existing = tmp_path / "videos" / "a.mp4"
        existing.parent.mkdir()
        existing.write_bytes(b"x")
        shots = [SimpleNamespace(id="s1"), SimpleNamespace(id="s2"), SimpleNamespace(id="s3")]
        artifacts = [
            SimpleNamespace(storage_key="videos/a.mp4"),  # 文件存在
            None,  # 无产物
            SimpleNamespace(storage_key="videos/gone.mp4"),  # 文件缺失
        ]
        missing = timeline_renderer.find_missing_shot_videos(shots, artifacts, tmp_path)
        assert missing == ["s2", "s3"]

    def test_invalid_render_mode_rejected(self):
        with pytest.raises(ValueError):
            asyncio.run(
                timeline_renderer.render_episode(
                    None, project_id="p", episode_id="e", render_mode="bogus"
                )
            )


# ─────────────────────────────────────────
# P2-2 feedback 写入选中
# ─────────────────────────────────────────


class TestFeedback:
    @staticmethod
    def _session(*, step=None, run=None, job=None, shot=None):
        get_map = {}
        if step is not None:
            get_map[("WorkflowStep", step.id)] = step
        if run is not None:
            get_map[("WorkflowRun", run.id)] = run
        if job is not None:
            get_map[("GenerationJob", job.id)] = job
        if shot is not None:
            get_map[("Shot", shot.id)] = shot
        return _FakeSession(get_map=get_map)

    def test_selected_updates_step(self):
        step = SimpleNamespace(id="st1", run_id="r1", selected_artifact_id=None)
        run = SimpleNamespace(id="r1", project_id="p1")
        job = SimpleNamespace(
            id="j1", workflow_step_id="st1", shot_id=None, project_id="p1"
        )
        session = self._session(step=step, run=run, job=job)
        artifact = SimpleNamespace(id="a1", project_id="p1", job_id="j1")
        feedback = asyncio.run(
            feedback_service.record_feedback(
                session, artifact=artifact, user_id=1,
                action="selected", step_id="st1", reason="最好的一条",
            )
        )
        assert step.selected_artifact_id == "a1"
        assert feedback.action == "selected"
        assert session.added[0] is feedback

    def test_selected_overwrites_existing(self):
        # 简单语义：直接覆盖（注释在 service docstring）
        step = SimpleNamespace(id="st1", run_id="r1", selected_artifact_id="old")
        run = SimpleNamespace(id="r1", project_id="p1")
        job = SimpleNamespace(
            id="j1", workflow_step_id="st1", shot_id=None, project_id="p1"
        )
        session = self._session(step=step, run=run, job=job)
        artifact = SimpleNamespace(id="a2", project_id="p1", job_id="j1")
        asyncio.run(
            feedback_service.record_feedback(
                session, artifact=artifact, user_id=1, action="selected", step_id="st1"
            )
        )
        assert step.selected_artifact_id == "a2"

    def test_invalid_action_rejected(self):
        session = _FakeSession()
        artifact = SimpleNamespace(id="a1", project_id="p1", job_id=None)
        with pytest.raises(ValueError):
            asyncio.run(
                feedback_service.record_feedback(
                    session, artifact=artifact, user_id=1, action="liked"
                )
            )

    # ── review 0.3：step_id 跨项目/伪造/不匹配校验 ──

    def test_cross_project_step_rejected(self):
        step = SimpleNamespace(id="st1", run_id="r1", selected_artifact_id=None)
        run = SimpleNamespace(id="r1", project_id="p2")  # 属于别的项目
        job = SimpleNamespace(
            id="j1", workflow_step_id="st1", shot_id=None, project_id="p1"
        )
        session = self._session(step=step, run=run, job=job)
        artifact = SimpleNamespace(id="a1", project_id="p1", job_id="j1")
        with pytest.raises(PermissionError):
            asyncio.run(
                feedback_service.record_feedback(
                    session, artifact=artifact, user_id=1, action="selected", step_id="st1"
                )
            )

    def test_fake_step_id_rejected(self):
        session = self._session()  # step st-ghost 不存在
        artifact = SimpleNamespace(id="a1", project_id="p1", job_id=None)
        with pytest.raises(ValueError):
            asyncio.run(
                feedback_service.record_feedback(
                    session, artifact=artifact, user_id=1,
                    action="selected", step_id="st-ghost",
                )
            )

    def test_artifact_not_from_step_rejected(self):
        step = SimpleNamespace(id="st1", run_id="r1", selected_artifact_id=None)
        run = SimpleNamespace(id="r1", project_id="p1")
        job = SimpleNamespace(
            id="j1", workflow_step_id="st2", shot_id=None, project_id="p1"  # 别的 step 的 job
        )
        session = self._session(step=step, run=run, job=job)
        artifact = SimpleNamespace(id="a1", project_id="p1", job_id="j1")
        with pytest.raises(PermissionError):
            asyncio.run(
                feedback_service.record_feedback(
                    session, artifact=artifact, user_id=1, action="selected", step_id="st1"
                )
            )

    # ── review 0.3：shot 级选择 ──

    def test_shot_level_selection_with_step_writes_both(self):
        step = SimpleNamespace(id="st1", run_id="r1", selected_artifact_id=None)
        run = SimpleNamespace(id="r1", project_id="p1")
        job = SimpleNamespace(
            id="j1", workflow_step_id="st1", shot_id="sh1", project_id="p1"
        )
        shot = SimpleNamespace(id="sh1", selected_image_artifact_id=None, selected_video_artifact_id=None)
        session = self._session(step=step, run=run, job=job, shot=shot)
        artifact = SimpleNamespace(id="a1", project_id="p1", job_id="j1", type="video")
        asyncio.run(
            feedback_service.record_feedback(
                session, artifact=artifact, user_id=1, action="selected", step_id="st1"
            )
        )
        assert step.selected_artifact_id == "a1"
        assert shot.selected_video_artifact_id == "a1"

    def test_shot_level_selection_without_step(self):
        # 前端按 shot 选择时 step_id 可空：仅写 Shot 级
        job = SimpleNamespace(
            id="j1", workflow_step_id=None, shot_id="sh1", project_id="p1"
        )
        shot = SimpleNamespace(id="sh1", selected_image_artifact_id=None, selected_video_artifact_id=None)
        session = self._session(job=job, shot=shot)
        artifact = SimpleNamespace(id="a1", project_id="p1", job_id="j1", type="video")
        feedback = asyncio.run(
            feedback_service.record_feedback(
                session, artifact=artifact, user_id=1, action="selected"
            )
        )
        assert shot.selected_video_artifact_id == "a1"
        assert feedback.step_id is None

    def test_shot_not_written_when_job_project_mismatch(self):
        # artifact 的 job 属于别的项目：不写 shot 级选择
        job = SimpleNamespace(
            id="j1", workflow_step_id=None, shot_id="sh1", project_id="p2"
        )
        shot = SimpleNamespace(id="sh1", selected_image_artifact_id=None, selected_video_artifact_id=None)
        session = self._session(job=job, shot=shot)
        artifact = SimpleNamespace(id="a1", project_id="p1", job_id="j1", type="video")
        asyncio.run(
            feedback_service.record_feedback(
                session, artifact=artifact, user_id=1, action="selected"
            )
        )
        assert shot.selected_video_artifact_id is None


# ─────────────────────────────────────────
# P2-3 个人资产库
# ─────────────────────────────────────────


class TestPersonalAsset:
    def test_personal_asset_allows_null_project(self):
        session = _FakeSession()
        card = asyncio.run(
            asset_service.create_asset_card(
                session, None, 1, type="character", name="主角", scope="personal"
            )
        )
        assert card.project_id is None
        assert card.scope == "personal"

    def test_personal_asset_requires_user(self):
        with pytest.raises(ValueError):
            asyncio.run(
                asset_service.create_asset_card(
                    _FakeSession(), None, None, type="character", name="x", scope="personal"
                )
            )

    def test_project_scope_requires_project(self):
        with pytest.raises(ValueError):
            asyncio.run(
                asset_service.create_asset_card(
                    _FakeSession(), None, 1, type="character", name="x", scope="project"
                )
            )

    def test_copy_personal_asset_to_project(self):
        src = AssetCard(
            id="a1", project_id=None, user_id=1, scope="personal",
            type="character", name="主角", version=3, description="d",
        )
        session = _FakeSession(get_map={("AssetCard", "a1"): src})
        card = asyncio.run(
            asset_service.copy_personal_asset_to_project(session, "a1", "proj1", 1)
        )
        assert card.scope == "project"
        assert card.project_id == "proj1"
        assert card.version == 1  # 锁定版本语义：项目内从 v1 重新演进
        assert card.source == "a1"
        assert card.name == "主角"

    def test_copy_rejects_non_personal(self):
        src = AssetCard(id="a2", project_id="p1", user_id=1, scope="project", type="character")
        session = _FakeSession(get_map={("AssetCard", "a2"): src})
        with pytest.raises(ValueError):
            asyncio.run(asset_service.copy_personal_asset_to_project(session, "a2", "p2", 1))

    def test_copy_rejects_other_users_asset(self):
        src = AssetCard(id="a3", project_id=None, user_id=1, scope="personal", type="character")
        session = _FakeSession(get_map={("AssetCard", "a3"): src})
        with pytest.raises(PermissionError):
            asyncio.run(asset_service.copy_personal_asset_to_project(session, "a3", "p2", 2))


# ─────────────────────────────────────────
# P2-1 LLM Planner（mock llm）
# ─────────────────────────────────────────


class _MockLLM:
    def __init__(self, content):
        self._content = content

    async def ainvoke(self, prompt):
        return SimpleNamespace(content=self._content)


class TestLLMPlanner:
    def test_valid_json_accepted(self):
        llm = _MockLLM('{"episodes": 3, "shots_per_episode": 8, "aspect_ratio": "16:9"}')
        plan = asyncio.run(planner_service.plan_with_llm("拍个短剧", {}, llm=llm))
        assert plan["params"]["episodes"] == 3
        assert plan["params"]["shots_per_episode"] == 8
        assert "planner_fallback" not in plan

    def test_invalid_json_falls_back(self):
        llm = _MockLLM("我不会输出 JSON")
        plan = asyncio.run(planner_service.plan_with_llm("2集每集4镜头", {}, llm=llm))
        assert plan["planner_fallback"] is True
        assert plan["params"]["episodes"] == 2  # 规则版解析

    def test_out_of_range_falls_back(self):
        llm = _MockLLM('{"episodes": 99, "shots_per_episode": 8}')
        plan = asyncio.run(planner_service.plan_with_llm("", {}, llm=llm))
        assert plan["planner_fallback"] is True

    def test_unknown_field_falls_back(self):
        llm = _MockLLM('{"episodes": 2, "shots_per_episode": 4, "hack": true}')
        plan = asyncio.run(planner_service.plan_with_llm("", {}, llm=llm))
        assert plan["planner_fallback"] is True

    def test_unknown_extra_node_falls_back(self):
        llm = _MockLLM(
            '{"episodes": 2, "shots_per_episode": 4, "extra_nodes": ["evil.node"]}'
        )
        plan = asyncio.run(planner_service.plan_with_llm("", {}, llm=llm))
        assert plan["planner_fallback"] is True


# ─────────────────────────────────────────
# 迁移 0004 stub op 验证（upgrade/downgrade）
# ─────────────────────────────────────────


def _load_migration_0004():
    path = BACKEND_DIR / "alembic" / "versions" / "0004_job_workflow_fk_personal_asset.py"
    spec = importlib.util.spec_from_file_location("migration_0004", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMigration0004:
    def _fake_op(self, calls):
        def _rec(name):
            def _fn(*args, **kwargs):
                calls.append((name, args, kwargs))
            return _fn

        return SimpleNamespace(
            add_column=_rec("add_column"),
            create_index=_rec("create_index"),
            create_foreign_key=_rec("create_foreign_key"),
            alter_column=_rec("alter_column"),
            drop_constraint=_rec("drop_constraint"),
            drop_index=_rec("drop_index"),
            drop_column=_rec("drop_column"),
        )

    def test_upgrade(self, monkeypatch):
        mod = _load_migration_0004()
        calls = []
        monkeypatch.setattr(mod, "op", self._fake_op(calls))
        mod.upgrade()

        added = {c[1][1].name for c in calls if c[0] == "add_column"}
        assert added == {"workflow_run_id", "workflow_step_id"}
        indexes = {c[1][0] for c in calls if c[0] == "create_index"}
        assert indexes == {
            "ix_generation_jobs_workflow_run_id",
            "ix_generation_jobs_workflow_step_id",
        }
        fks = [c for c in calls if c[0] == "create_foreign_key"]
        assert len(fks) == 2
        assert all(c[2].get("ondelete") == "SET NULL" for c in fks)
        alters = [c for c in calls if c[0] == "alter_column"]
        assert any(
            c[1][0] == "asset_cards" and c[2].get("nullable") is True for c in alters
        )

    def test_downgrade(self, monkeypatch):
        mod = _load_migration_0004()
        calls = []
        monkeypatch.setattr(mod, "op", self._fake_op(calls))
        mod.downgrade()

        dropped_cols = {c[1][1] for c in calls if c[0] == "drop_column"}
        assert dropped_cols == {"workflow_run_id", "workflow_step_id"}
        dropped_fks = {c[1][0] for c in calls if c[0] == "drop_constraint"}
        assert dropped_fks == {
            "fk_generation_jobs_workflow_run_id",
            "fk_generation_jobs_workflow_step_id",
        }
        dropped_idx = {c[1][0] for c in calls if c[0] == "drop_index"}
        assert dropped_idx == {
            "ix_generation_jobs_workflow_run_id",
            "ix_generation_jobs_workflow_step_id",
        }
        alters = [c for c in calls if c[0] == "alter_column"]
        assert any(
            c[1][0] == "asset_cards" and c[2].get("nullable") is False for c in alters
        )


# ─────────────────────────────────────────
# 重试代际字段：logical_job_key 格式
# ─────────────────────────────────────────


class TestLogicalJobKey:
    def test_format(self):
        assert (
            job_planner.make_logical_job_key("generate_video", "r1", "node:ep_e1", "sh1")
            == "generate_video:r1:node:ep_e1:sh1"
        )
        # 与幂等键的区别：不含 attempt
        key = job_planner.make_logical_job_key("tts", "r1", "node", "sh1:dialogue0")
        assert "attempt" not in key

    def test_retry_step_attempt_flows_into_new_jobs(self, monkeypatch):
        """step.attempt+1 后展开的新 job 带新 step_attempt，但 logical_job_key 不变。"""
        shots = [SimpleNamespace(id="sh1", visual_prompt="p", duration_ms=5000, aspect_ratio="9:16")]

        async def _fake_shots(session, episode_id):
            return shots

        async def _fake_first_frame(session, shot_id):
            return None

        monkeypatch.setattr(job_planner, "_current_revision_shots", _fake_shots)
        monkeypatch.setattr(job_planner, "find_first_frame_storage_key", _fake_first_frame)

        async def _reserve(session, run_id, amount):
            return True

        monkeypatch.setattr(cost_service, "reserve_budget", _reserve)

        # 本用例只验证 step_attempt / logical_job_key；路由与 Provider 能力固定为桩，
        # 不随本机 .env 中配置了哪些厂商而变化。
        from app.model_runtime import media as media_module

        def _fake_routing(provider_policy, resolution, *, duration=6, explicit_provider=""):
            return {
                "provider": "stub", "model": "stub-video", "resolution": resolution,
                "protocol": "stub", "billable_duration": duration,
                "estimated_total_cost": Decimal("0"), "price_snapshot": {},
            }

        stub_provider = SimpleNamespace(name="stub", supports=lambda req, model=None: True)
        stub_runtime = SimpleNamespace(
            video_router=lambda: None,
            get=lambda modality, name: SimpleNamespace(provider=stub_provider),
        )
        monkeypatch.setattr(pricing, "resolve_video_routing", _fake_routing)
        monkeypatch.setattr(media_module, "get_media_runtime", lambda: stub_runtime)

        class _PlanSession(_FakeSession):
            async def execute(self, stmt):
                # _get_or_create_job 查 idempotency_key：一律不存在 → 新建
                return SimpleNamespace(
                    scalars=lambda: SimpleNamespace(first=lambda: None, all=lambda: [])
                )

        from app.db.models import WorkflowRun, WorkflowStep

        run = WorkflowRun(id="r1", project_id="p1", input_snapshot={})
        step = WorkflowStep(id="st1", run_id="r1", node_key="shot.video.generate:ep_e1", attempt=0)
        session = _PlanSession()

        async def _go(attempt):
            step.attempt = attempt
            return await job_planner.plan_shot_video_jobs(
                session, run=run, step=step, episode_id="e1"
            )

        jobs0 = asyncio.run(_go(0))
        assert jobs0[0].step_attempt == 0
        assert jobs0[0].logical_job_key == "generate_video:r1:shot.video.generate:ep_e1:sh1"
        jobs1 = asyncio.run(_go(1))
        assert jobs1[0].step_attempt == 1
        assert jobs1[0].logical_job_key == jobs0[0].logical_job_key
        assert jobs1[0].idempotency_key != jobs0[0].idempotency_key


# ─────────────────────────────────────────
# 迁移 0005 stub op 验证（upgrade/downgrade）
# ─────────────────────────────────────────


def _load_migration_0005():
    path = BACKEND_DIR / "alembic" / "versions" / "0005_retry_generation_shot_selection.py"
    spec = importlib.util.spec_from_file_location("migration_0005", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMigration0005:
    def _fake_op(self, calls):
        def _rec(name):
            def _fn(*args, **kwargs):
                calls.append((name, args, kwargs))
            return _fn

        return SimpleNamespace(
            add_column=_rec("add_column"),
            create_index=_rec("create_index"),
            create_foreign_key=_rec("create_foreign_key"),
            drop_constraint=_rec("drop_constraint"),
            drop_index=_rec("drop_index"),
            drop_column=_rec("drop_column"),
        )

    def test_upgrade(self, monkeypatch):
        mod = _load_migration_0005()
        calls = []
        monkeypatch.setattr(mod, "op", self._fake_op(calls))
        mod.upgrade()

        added = {(c[1][0], c[1][1].name) for c in calls if c[0] == "add_column"}
        assert added == {
            ("generation_jobs", "step_attempt"),
            ("generation_jobs", "logical_job_key"),
            ("generation_jobs", "superseded_by"),
            ("shots", "selected_artifact_id"),
        }
        indexes = {c[1][0] for c in calls if c[0] == "create_index"}
        assert "ix_generation_jobs_logical_job_key" in indexes
        fks = [c for c in calls if c[0] == "create_foreign_key"]
        assert len(fks) == 1
        assert fks[0][1][1] == "shots" and fks[0][1][2] == "artifacts"
        assert fks[0][2].get("ondelete") == "SET NULL"

    def test_downgrade(self, monkeypatch):
        mod = _load_migration_0005()
        calls = []
        monkeypatch.setattr(mod, "op", self._fake_op(calls))
        mod.downgrade()

        dropped_cols = {(c[1][0], c[1][1]) for c in calls if c[0] == "drop_column"}
        assert dropped_cols == {
            ("generation_jobs", "step_attempt"),
            ("generation_jobs", "logical_job_key"),
            ("generation_jobs", "superseded_by"),
            ("shots", "selected_artifact_id"),
        }
        dropped_fks = {c[1][0] for c in calls if c[0] == "drop_constraint"}
        assert dropped_fks == {"fk_shots_selected_artifact_id"}
        dropped_idx = {c[1][0] for c in calls if c[0] == "drop_index"}
        assert dropped_idx == {"ix_generation_jobs_logical_job_key"}
