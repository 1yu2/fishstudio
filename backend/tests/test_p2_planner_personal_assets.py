"""P2-1（LLM Planner 接入创建 Run）与 P2-3（个人资产独立 API）测试。

同 test_series_domain 的限制：无真 DB，路由层 monkeypatch service 验证
200/403/404/400 映射与参数流向；service 层用最小 _FakeSession stub。
"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.db.models import AssetCard
from app.services import asset_service, planner_service, workflow_service
from app.services.workflow_nodes import SHORT_DRAMA_TEMPLATE_GRAPH

# ─────────────────────────────────────────
# 最小 stub
# ─────────────────────────────────────────


class _FakeSession:
    def __init__(self, get_map=None):
        self.get_map = get_map or {}

    async def get(self, model, key):
        name = getattr(model, "__name__", str(model))
        return self.get_map.get((name, key))


def _fake_card(**over) -> SimpleNamespace:
    """满足 AssetCardOut(from_attributes) 的假资产。"""
    base = dict(
        id="a1",
        project_id=None,
        user_id=1,
        type="character",
        name="主角",
        version=1,
        description="",
        provider_meta={},
        scope="personal",
        tags=[],
        source="",
        license_meta={},
        status="draft",
        created_at=datetime.now(timezone.utc),
    )
    base.update(over)
    return SimpleNamespace(**base)


# ─────────────────────────────────────────
# P2-1：plan_with_llm 结果契约（planner_model / fallback_reason / extra_nodes）
# ─────────────────────────────────────────


class _MockLLM:
    def __init__(self, content, model_name="mock-planner-v1"):
        self._content = content
        self.model_name = model_name

    async def ainvoke(self, prompt):
        return SimpleNamespace(content=self._content)


class TestLLMPlannerContract:
    def test_success_records_planner_model_and_extra_nodes(self):
        llm = _MockLLM('{"episodes": 2, "shots_per_episode": 4, "extra_nodes": ["storyboard.approve"]}')
        plan = asyncio.run(planner_service.plan_with_llm("拍个短剧", {}, llm=llm))
        assert plan["planner_model"] == "mock-planner-v1"
        assert plan["extra_nodes"] == ["storyboard.approve"]
        assert "planner_fallback" not in plan

    def test_fallback_marks_rule_based_and_reason(self):
        llm = _MockLLM("不是 JSON")
        plan = asyncio.run(planner_service.plan_with_llm("2集每集4镜头", {}, llm=llm))
        assert plan["planner_fallback"] is True
        assert plan["planner_model"] == "rule-based"
        assert plan["fallback_reason"]

    def test_timeout_falls_back(self, monkeypatch):
        class _SlowLLM:
            async def ainvoke(self, prompt):
                await asyncio.sleep(1)
                return SimpleNamespace(content="{}")

        monkeypatch.setattr(planner_service, "LLM_TIMEOUT_SECONDS", 0.01)
        plan = asyncio.run(planner_service.plan_with_llm("", {}, llm=_SlowLLM()))
        assert plan["planner_fallback"] is True
        assert plan["planner_model"] == "rule-based"


# ─────────────────────────────────────────
# P2-1：apply_extra_nodes（extra_nodes 落地语义）
# ─────────────────────────────────────────


class TestApplyExtraNodes:
    def test_linear_insert_before_render(self):
        graph = workflow_service.apply_extra_nodes(SHORT_DRAMA_TEMPLATE_GRAPH, ["storyboard.approve"])
        keys = [n["node_key"] for n in graph["nodes"]]
        assert "extra.storyboard.approve" in keys
        edges = [tuple(e) for e in graph["edges"]]
        # 拆除 audio→render 直连，串入尾部链
        assert ("shot.audio.generate", "episode.render") not in edges
        assert ("shot.audio.generate", "extra.storyboard.approve") in edges
        assert ("extra.storyboard.approve", "episode.render") in edges
        # DAG 仍然合法
        ok, errors = workflow_service.validate_plan(
            {"graph": graph, "params": {"episodes": 1, "shots_per_episode": 6}}
        )
        assert ok, errors

    def test_node_key_uniquified(self):
        graph = workflow_service.apply_extra_nodes(
            SHORT_DRAMA_TEMPLATE_GRAPH, ["storyboard.approve", "storyboard.approve"]
        )
        keys = [n["node_key"] for n in graph["nodes"]]
        assert "extra.storyboard.approve" in keys
        assert "extra.storyboard.approve#2" in keys

    def test_custom_upstream(self):
        graph = workflow_service.apply_extra_nodes(
            SHORT_DRAMA_TEMPLATE_GRAPH,
            [{"node_type": "storyboard.approve", "upstream": "outline.generate"}],
        )
        edges = [tuple(e) for e in graph["edges"]]
        assert ("outline.generate", "extra.storyboard.approve") in edges
        assert ("extra.storyboard.approve", "episode.render") in edges

    def test_unknown_node_type_rejected(self):
        with pytest.raises(ValueError):
            workflow_service.apply_extra_nodes(SHORT_DRAMA_TEMPLATE_GRAPH, ["evil.node"])

    def test_unknown_upstream_rejected(self):
        with pytest.raises(ValueError):
            workflow_service.apply_extra_nodes(
                SHORT_DRAMA_TEMPLATE_GRAPH,
                [{"node_type": "storyboard.approve", "upstream": "ghost"}],
            )

    def test_empty_is_noop(self):
        graph = workflow_service.apply_extra_nodes(SHORT_DRAMA_TEMPLATE_GRAPH, [])
        assert graph["nodes"] == SHORT_DRAMA_TEMPLATE_GRAPH["nodes"]
        assert graph["edges"] == SHORT_DRAMA_TEMPLATE_GRAPH["edges"]


# ─────────────────────────────────────────
# P2-1：POST /api/workflow/runs 的 use_llm_planner 流向
# ─────────────────────────────────────────


class TestCreateRunUseLlmPlanner:
    def _stub_project(self, monkeypatch):
        async def fake_get_project(session, project_id, user_id=None, is_admin=False):
            return SimpleNamespace(id=project_id, title="测试剧", settings={})

        async def _noop(*a, **k):
            return None

        monkeypatch.setattr("app.routers.workflow.series_service.get_project", fake_get_project)
        monkeypatch.setattr("app.routers.workflow.workflow_service.seed_builtin_templates", _noop)
        monkeypatch.setattr("app.routers.workflow.workflow_service.advance_run", _noop)

    def test_llm_planner_flow(self, client, monkeypatch):
        self._stub_project(monkeypatch)
        captured = {}

        async def fake_plan_with_llm(goal, settings):
            return {
                "template_key": "short-drama",
                "params": {"episodes": 2, "shots_per_episode": 4},
                "planner_model": "mock-planner-v1",
                "extra_nodes": ["storyboard.approve"],
            }

        async def fake_create_run(session, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id="r1", status="ready")

        monkeypatch.setattr("app.routers.workflow.planner_service.plan_with_llm", fake_plan_with_llm)
        monkeypatch.setattr("app.routers.workflow.workflow_service.create_run", fake_create_run)
        resp = client.post(
            "/api/workflow/runs",
            json={"project_id": "p1", "goal": "2集短剧", "use_llm_planner": True},
        )
        assert resp.status_code == 200
        assert captured["planner_model"] == "mock-planner-v1"
        assert captured["extra_nodes"] == ["storyboard.approve"]
        assert captured["input_snapshot"]["episodes"] == 2
        assert resp.json()["plan"]["planner_model"] == "mock-planner-v1"

    def test_default_uses_rule_planner(self, client, monkeypatch):
        self._stub_project(monkeypatch)
        called = {"llm": False}
        captured = {}

        async def fake_plan_with_llm(goal, settings):
            called["llm"] = True
            return {}

        async def fake_create_run(session, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id="r1", status="ready")

        monkeypatch.setattr("app.routers.workflow.planner_service.plan_with_llm", fake_plan_with_llm)
        monkeypatch.setattr("app.routers.workflow.workflow_service.create_run", fake_create_run)
        resp = client.post("/api/workflow/runs", json={"project_id": "p1"})
        assert resp.status_code == 200
        assert called["llm"] is False
        assert captured["planner_model"] == "rule-based"
        assert captured["extra_nodes"] is None

    def test_llm_fallback_flows_to_create_run(self, client, monkeypatch):
        self._stub_project(monkeypatch)
        captured = {}

        async def fake_plan_with_llm(goal, settings):
            plan = planner_service.plan_from_template(goal, settings)
            plan["planner_fallback"] = True
            plan["fallback_reason"] = "boom"
            plan["planner_model"] = "rule-based"
            return plan

        async def fake_create_run(session, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id="r1", status="ready")

        monkeypatch.setattr("app.routers.workflow.planner_service.plan_with_llm", fake_plan_with_llm)
        monkeypatch.setattr("app.routers.workflow.workflow_service.create_run", fake_create_run)
        resp = client.post(
            "/api/workflow/runs",
            json={"project_id": "p1", "use_llm_planner": True},
        )
        assert resp.status_code == 200
        assert captured["planner_model"] == "rule-based"
        body = resp.json()["plan"]
        assert body["planner_fallback"] is True
        assert body["fallback_reason"] == "boom"


# ─────────────────────────────────────────
# P2-3：get_personal_asset_for_user（owner-only，admin 不放行）
# ─────────────────────────────────────────


class TestGetPersonalAssetForUser:
    def test_owner_gets_card(self):
        card = AssetCard(id="a1", project_id=None, user_id=1, scope="personal", type="character")
        session = _FakeSession(get_map={("AssetCard", "a1"): card})
        got = asyncio.run(asset_service.get_personal_asset_for_user(session, "a1", 1))
        assert got is card

    def test_other_user_rejected(self):
        card = AssetCard(id="a1", project_id=None, user_id=1, scope="personal", type="character")
        session = _FakeSession(get_map={("AssetCard", "a1"): card})
        assert asyncio.run(asset_service.get_personal_asset_for_user(session, "a1", 2)) is None

    def test_project_scope_rejected(self):
        card = AssetCard(id="a1", project_id="p1", user_id=1, scope="project", type="character")
        session = _FakeSession(get_map={("AssetCard", "a1"): card})
        assert asyncio.run(asset_service.get_personal_asset_for_user(session, "a1", 1)) is None

    def test_missing_returns_none(self):
        assert asyncio.run(asset_service.get_personal_asset_for_user(_FakeSession(), "ghost", 1)) is None


# ─────────────────────────────────────────
# P2-3：个人资产 API（路由层 monkeypatch service）
# ─────────────────────────────────────────


class TestPersonalAssetRouter:
    def test_create_personal_asset(self, client, monkeypatch):
        captured = {}

        async def fake_create(session, project_id, user_id, **kwargs):
            captured.update(project_id=project_id, user_id=user_id, **kwargs)
            return _fake_card()

        monkeypatch.setattr("app.routers.assets.asset_service.create_asset_card", fake_create)
        resp = client.post(
            "/api/assets/personal",
            json={"type": "character", "name": "主角", "tags": ["古风"]},
        )
        assert resp.status_code == 200
        assert captured["project_id"] is None
        assert captured["scope"] == "personal"
        assert captured["user_id"] == 1  # conftest fake user
        assert resp.json()["scope"] == "personal"
        assert resp.json()["project_id"] is None

    def test_list_personal_assets_forces_user_scope(self, client, monkeypatch):
        captured = {}

        async def fake_list(session, **kwargs):
            captured.update(kwargs)
            return [_fake_card()]

        monkeypatch.setattr("app.routers.assets.asset_service.list_assets", fake_list)
        resp = client.get("/api/assets/personal", params={"type": "character", "q": "主"})
        assert resp.status_code == 200
        assert captured["scope"] == "personal"
        assert captured["user_id"] == 1
        assert captured["type"] == "character"
        assert len(resp.json()) == 1

    def test_new_version_not_found_or_not_owner_404(self, client, monkeypatch):
        async def fake_get(session, asset_id, user_id):
            return None

        monkeypatch.setattr("app.routers.assets.asset_service.get_personal_asset_for_user", fake_get)
        resp = client.post("/api/assets/personal/a1/versions", json={"updates": {}})
        assert resp.status_code == 404

    def test_new_version_ok(self, client, monkeypatch):
        async def fake_get(session, asset_id, user_id):
            return _fake_card()

        async def fake_new_version(session, asset_id, user_id, updates):
            return _fake_card(version=2)

        monkeypatch.setattr("app.routers.assets.asset_service.get_personal_asset_for_user", fake_get)
        monkeypatch.setattr("app.routers.assets.asset_service.new_asset_version", fake_new_version)
        resp = client.post("/api/assets/personal/a1/versions", json={"updates": {}})
        assert resp.status_code == 200
        assert resp.json()["version"] == 2

    def test_copy_to_project_forbidden_project_403(self, client, monkeypatch):
        async def fake_get_project(session, project_id, user_id=None, is_admin=False):
            raise PermissionError("project belongs to another user")

        monkeypatch.setattr("app.routers.assets.series_service.get_project", fake_get_project)
        resp = client.post("/api/assets/personal/a1/copy-to-project", json={"project_id": "p2"})
        assert resp.status_code == 403

    def test_copy_to_project_not_owner_asset_403(self, client, monkeypatch):
        async def fake_get_project(session, project_id, user_id=None, is_admin=False):
            return SimpleNamespace(id=project_id)

        async def fake_copy(session, asset_id, project_id, user_id):
            raise PermissionError("只能复制自己的个人资产")

        monkeypatch.setattr("app.routers.assets.series_service.get_project", fake_get_project)
        monkeypatch.setattr("app.routers.assets.asset_service.copy_personal_asset_to_project", fake_copy)
        resp = client.post("/api/assets/personal/a1/copy-to-project", json={"project_id": "p1"})
        assert resp.status_code == 403

    def test_copy_to_project_ok(self, client, monkeypatch):
        async def fake_get_project(session, project_id, user_id=None, is_admin=False):
            return SimpleNamespace(id=project_id)

        async def fake_copy(session, asset_id, project_id, user_id):
            return _fake_card(project_id=project_id, scope="project", source=asset_id)

        monkeypatch.setattr("app.routers.assets.series_service.get_project", fake_get_project)
        monkeypatch.setattr("app.routers.assets.asset_service.copy_personal_asset_to_project", fake_copy)
        resp = client.post("/api/assets/personal/a1/copy-to-project", json={"project_id": "p1"})
        assert resp.status_code == 200
        assert resp.json()["scope"] == "project"
        assert resp.json()["source"] == "a1"


# ─────────────────────────────────────────
# P2-3：项目资产列表 include_personal 合并
# ─────────────────────────────────────────


class TestProjectAssetsIncludePersonal:
    def _stub_project(self, monkeypatch):
        async def fake_get_project(session, project_id, user_id=None, is_admin=False):
            return SimpleNamespace(id=project_id)

        monkeypatch.setattr("app.routers.series.series_service.get_project", fake_get_project)

    def test_default_only_project_assets(self, client, monkeypatch):
        self._stub_project(monkeypatch)
        calls = []

        async def fake_list(session, **kwargs):
            calls.append(kwargs)
            return [_fake_card(project_id="p1", scope="project")]

        monkeypatch.setattr("app.routers.series.asset_service.list_assets", fake_list)
        resp = client.get("/api/series/projects/p1/assets")
        assert resp.status_code == 200
        assert len(calls) == 1
        assert calls[0].get("scope") is None

    def test_include_personal_merges(self, client, monkeypatch):
        self._stub_project(monkeypatch)
        calls = []

        async def fake_list(session, **kwargs):
            calls.append(kwargs)
            if kwargs.get("scope") == "personal":
                return [_fake_card(id="pa1")]
            return [_fake_card(id="pr1", project_id="p1", scope="project")]

        monkeypatch.setattr("app.routers.series.asset_service.list_assets", fake_list)
        resp = client.get("/api/series/projects/p1/assets", params={"include_personal": True})
        assert resp.status_code == 200
        assert len(calls) == 2
        personal_call = [c for c in calls if c.get("scope") == "personal"][0]
        assert personal_call["user_id"] == 1
        scopes = {c["scope"] for c in resp.json()}
        assert scopes == {"project", "personal"}
