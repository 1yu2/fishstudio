"""Workflow 测试：validate_plan / Planner 规则解析 / DAG 推进纯逻辑 / 幂等键 / 路由错误映射。

同 test_series_domain 的限制：无真 DB（JSONB 与 SQLite 不兼容），路由层 monkeypatch
service 验证 403/404/400 映射。
"""

import asyncio

import pytest

from app.services import planner_service, workflow_service
from app.services.job_planner import make_job_idempotency_key
from app.services.workflow_nodes import NODE_TYPES, SHORT_DRAMA_TEMPLATE_GRAPH, toposort


def _valid_plan(**params) -> dict:
    base = {"episodes": 2, "shots_per_episode": 6}
    base.update(params)
    return {"template_key": "short-drama", "params": base}


# ─────────────────────────────────────────
# validate_plan
# ─────────────────────────────────────────


class TestValidatePlan:
    def test_valid_default_graph(self):
        ok, errors = workflow_service.validate_plan(_valid_plan())
        assert ok, errors

    def test_unknown_node_type_rejected(self):
        plan = _valid_plan()
        plan["graph"] = {
            "nodes": [{"node_key": "a", "node_type": "not.registered"}],
            "edges": [],
        }
        ok, errors = workflow_service.validate_plan(plan)
        assert not ok
        assert any("未知 node_type" in e for e in errors)

    def test_cycle_rejected(self):
        plan = _valid_plan()
        plan["graph"] = {
            "nodes": [
                {"node_key": "a", "node_type": "project.initialize"},
                {"node_key": "b", "node_type": "outline.generate"},
            ],
            "edges": [["a", "b"], ["b", "a"]],
        }
        ok, errors = workflow_service.validate_plan(plan)
        assert not ok
        assert any("环" in e for e in errors)

    def test_duplicate_node_key_rejected(self):
        plan = _valid_plan()
        plan["graph"] = {
            "nodes": [
                {"node_key": "a", "node_type": "project.initialize"},
                {"node_key": "a", "node_type": "outline.generate"},
            ],
            "edges": [],
        }
        ok, errors = workflow_service.validate_plan(plan)
        assert not ok
        assert any("重复" in e for e in errors)

    def test_dangling_edge_rejected(self):
        plan = _valid_plan()
        plan["graph"] = {
            "nodes": [{"node_key": "a", "node_type": "project.initialize"}],
            "edges": [["a", "ghost"]],
        }
        ok, errors = workflow_service.validate_plan(plan)
        assert not ok
        assert any("未知节点" in e for e in errors)

    def test_shots_over_limit_rejected(self):
        ok, errors = workflow_service.validate_plan(_valid_plan(shots_per_episode=999))
        assert not ok
        assert any("shots_per_episode" in e for e in errors)

    def test_episodes_over_limit_rejected(self):
        ok, errors = workflow_service.validate_plan(_valid_plan(episodes=0))
        assert not ok
        assert any("episodes" in e for e in errors)

    def test_cost_limit_negative_rejected(self):
        ok, errors = workflow_service.validate_plan(_valid_plan(cost_limit=-1))
        assert not ok
        assert any("cost_limit" in e for e in errors)

    def test_cost_limit_ok(self):
        ok, errors = workflow_service.validate_plan(_valid_plan(cost_limit=99.5))
        assert ok, errors


# ─────────────────────────────────────────
# 内置模板图与注册表
# ─────────────────────────────────────────


class TestBuiltinGraph:
    def test_all_node_types_registered(self):
        for node in SHORT_DRAMA_TEMPLATE_GRAPH["nodes"]:
            assert node["node_type"] in NODE_TYPES

    def test_builtin_graph_acyclic(self):
        order = toposort(
            [n["node_key"] for n in SHORT_DRAMA_TEMPLATE_GRAPH["nodes"]],
            SHORT_DRAMA_TEMPLATE_GRAPH["edges"],
        )
        assert order.index("project.initialize") < order.index("episode.render")
        assert (
            order.index("series.bible.generate")
            < order.index("series.bible.approve")
            < order.index("outline.generate")
        )
        assert (
            order.index("style.bible.generate")
            < order.index("style.bible.approve")
            < order.index("outline.generate")
        )
        assert (
            order.index("episode.script.generate")
            < order.index("episode.script.approve")
            < order.index("storyboard.generate")
        )
        # shot.image 汇合两条审批分支
        assert order.index("storyboard.approve") < order.index("shot.image.generate")
        assert order.index("asset.version.approve") < order.index("shot.image.generate")

    def test_toposort_cycle_raises(self):
        with pytest.raises(ValueError):
            toposort(["a", "b"], [["a", "b"], ["b", "a"]])


# ─────────────────────────────────────────
# Planner 规则解析
# ─────────────────────────────────────────


class TestPlanFromTemplate:
    def test_parse_episodes_and_shots(self):
        plan = planner_service.plan_from_template("做一个3集每集5镜头的短剧", {})
        assert plan["template_key"] == "short-drama"
        assert plan["params"]["episodes"] == 3
        assert plan["params"]["shots_per_episode"] == 5

    def test_defaults(self):
        plan = planner_service.plan_from_template("随便做个短剧", {})
        assert plan["params"]["episodes"] == 1
        assert plan["params"]["shots_per_episode"] == 6

    def test_settings_aspect_ratio_wins(self):
        plan = planner_service.plan_from_template("3集", {"aspect_ratio": "16:9"})
        assert plan["params"]["aspect_ratio"] == "16:9"

    def test_parse_aspect_from_goal(self):
        assert planner_service.parse_goal("16:9 画面 2集")["aspect_ratio"] == "16:9"

    def test_invalid_plan_raises(self):
        with pytest.raises(ValueError):
            planner_service.plan_from_template("999集", {})

    def test_plan_with_llm_falls_back(self):
        # mock LLM 返回非法 JSON → 回退规则版并标记 planner_fallback
        class _BadLLM:
            async def ainvoke(self, prompt):
                return "not json at all"

        plan = asyncio.run(planner_service.plan_with_llm("2集每集4镜头", {}, llm=_BadLLM()))
        assert plan["params"]["episodes"] == 2
        assert plan["planner_fallback"] is True


# ─────────────────────────────────────────
# DAG 推进纯逻辑
# ─────────────────────────────────────────


class _FakeStep:
    def __init__(self, id, status):
        self.id = id
        self.status = status


class _FakeDep:
    def __init__(self, up, down):
        self.upstream_step_id = up
        self.downstream_step_id = down


class TestUpstreamAllSucceeded:
    def test_no_upstream_is_ready(self):
        steps = [_FakeStep("a", "ready")]
        assert workflow_service.upstream_all_succeeded("a", steps, [])

    def test_upstream_succeeded(self):
        steps = [_FakeStep("a", "succeeded"), _FakeStep("b", "ready")]
        deps = [_FakeDep("a", "b")]
        assert workflow_service.upstream_all_succeeded("b", steps, deps)

    def test_upstream_skipped_is_non_blocking(self):
        steps = [_FakeStep("a", "skipped"), _FakeStep("b", "ready")]
        deps = [_FakeDep("a", "b")]
        assert workflow_service.upstream_all_succeeded("b", steps, deps)

    def test_upstream_not_succeeded(self):
        steps = [_FakeStep("a", "waiting_approval"), _FakeStep("b", "ready")]
        deps = [_FakeDep("a", "b")]
        assert not workflow_service.upstream_all_succeeded("b", steps, deps)

    def test_multiple_upstreams_all_required(self):
        steps = [
            _FakeStep("a", "succeeded"),
            _FakeStep("b", "running"),
            _FakeStep("c", "draft"),
        ]
        deps = [_FakeDep("a", "c"), _FakeDep("b", "c")]
        assert not workflow_service.upstream_all_succeeded("c", steps, deps)


# ─────────────────────────────────────────
# 幂等键稳定性
# ─────────────────────────────────────────


class TestIdempotencyKey:
    def test_format_stable(self):
        key = make_job_idempotency_key("video", "run1", "shot.video.generate:ep_e1", "s1", 0)
        assert key == "video:run1:shot.video.generate:ep_e1:s1:attempt0"

    def test_attempt_changes_key(self):
        k1 = make_job_idempotency_key("image", "r", "n", "s", 0)
        k2 = make_job_idempotency_key("image", "r", "n", "s", 1)
        assert k1 != k2


# ─────────────────────────────────────────
# 路由错误映射（monkeypatch service，验证 403/404/400）
# ─────────────────────────────────────────


class TestWorkflowRouterErrorMapping:
    def test_list_runs_forbidden(self, client, monkeypatch):
        async def fake_get_project(session, project_id, user_id=None, is_admin=False):
            raise PermissionError("project belongs to another user")

        monkeypatch.setattr(
            "app.routers.workflow.series_service.get_project", fake_get_project
        )
        resp = client.get("/api/workflow/runs", params={"project_id": "p1"})
        assert resp.status_code == 403

    def test_list_runs_not_found(self, client, monkeypatch):
        async def fake_get_project(session, project_id, user_id=None, is_admin=False):
            return None

        monkeypatch.setattr(
            "app.routers.workflow.series_service.get_project", fake_get_project
        )
        resp = client.get("/api/workflow/runs", params={"project_id": "ghost"})
        assert resp.status_code == 404

    def test_get_run_not_found(self, client, monkeypatch):
        from fastapi import HTTPException

        async def fake_owned_run(session, run_id, user):
            raise HTTPException(status_code=404, detail="Run 不存在")

        monkeypatch.setattr("app.routers.workflow._owned_run", fake_owned_run)
        resp = client.get("/api/workflow/runs/run-x")
        assert resp.status_code == 404

    def test_create_run_forbidden(self, client, monkeypatch):
        async def fake_get_project(session, project_id, user_id=None, is_admin=False):
            raise PermissionError("project belongs to another user")

        monkeypatch.setattr(
            "app.routers.workflow.series_service.get_project", fake_get_project
        )
        resp = client.post("/api/workflow/runs", json={"project_id": "p1"})
        assert resp.status_code == 403

    def test_retry_step_invalid_status_400(self, client, monkeypatch):
        async def fake_owned_step(session, step_id, user):
            return object()

        async def fake_retry(session, step_id):
            raise ValueError("step status is running, only failed can retry")

        monkeypatch.setattr("app.routers.workflow._owned_step", fake_owned_step)
        monkeypatch.setattr("app.routers.workflow.workflow_service.retry_step", fake_retry)
        resp = client.post("/api/workflow/steps/s1/retry")
        assert resp.status_code == 400


# ─────────────────────────────────────────
# P1-2：视频 Job 首帧参数纯函数
# ─────────────────────────────────────────


class TestBuildVideoJobParams:
    def _build(self, first_frame):
        from app.services.job_planner import build_video_job_params

        return build_video_job_params(
            shot_id="s1",
            step_id="st1",
            run_id="r1",
            prompt="一只猫",
            duration_ms=6000,
            aspect_ratio="9:16",
            policy={"resolution": "768P", "provider_policy": "balanced"},
            first_frame=first_frame,
        )

    def test_with_first_frame(self):
        params = self._build("images/a.png")
        assert params["mode"] == "first_frame"
        assert params["first_frame"] == "images/a.png"
        assert "fallback_reason" not in params

    def test_missing_first_frame_falls_back_to_text(self):
        # 缺少首帧时不得创建 first_frame 视频 Job：降级 text 并记录原因
        params = self._build(None)
        assert params["mode"] == "text"
        assert params["fallback_reason"] == "first_frame_missing"
        assert "first_frame" not in params


# ─────────────────────────────────────────
# P1-12：内置 DAG 含 TTS 节点（video → audio → render）
# ─────────────────────────────────────────


def test_builtin_graph_has_audio_node_between_video_and_render():
    node_keys = [n["node_key"] for n in SHORT_DRAMA_TEMPLATE_GRAPH["nodes"]]
    assert "shot.audio.generate" in node_keys
    order = toposort(node_keys, SHORT_DRAMA_TEMPLATE_GRAPH["edges"])
    assert (
        order.index("shot.video.generate")
        < order.index("shot.audio.generate")
        < order.index("episode.render")
    )
    spec = NODE_TYPES["shot.audio.generate"]
    assert spec.creates_jobs and spec.scope == "episode"
