"""系列剧领域测试：纯逻辑（payload 校验 / 旧文本解析 / gate 收集）+ 路由错误映射。

说明：conftest 把 get_session override 成 yield None，且模型用 postgresql.JSONB
（SQLite 不兼容），所以不做真 DB 服务测试；路由测试通过 monkeypatch service
函数验证 403/404/400 映射。
"""

import pytest
from pydantic import ValidationError

from app.schemas.series import StoryboardPayload
from app.services.approval_service import collect_character_asset_ids
from app.services.storyboard_service import parse_legacy_storyboard_text


def _shot(number: int, **kw) -> dict:
    base = {"number": number, "visual_prompt": f"shot {number}"}
    base.update(kw)
    return base


def _payload(**shot_kw) -> dict:
    return {
        "episodes": [
            {
                "number": 1,
                "scenes": [{"number": 1, "shots": [_shot(1, **shot_kw)]}],
            }
        ]
    }


# ─────────────────────────────────────────
# StoryboardPayload 校验
# ─────────────────────────────────────────


class TestStoryboardPayload:
    def test_valid_minimal(self):
        p = StoryboardPayload.model_validate(_payload())
        assert p.schema_version == 1
        shot = p.episodes[0].scenes[0].shots[0]
        assert shot.duration_ms == 5000
        assert shot.aspect_ratio == "9:16"
        assert shot.camera.type == "fixed"
        assert shot.transition.type == "cut"

    def test_duration_zero_rejected(self):
        with pytest.raises(ValidationError):
            StoryboardPayload.model_validate(_payload(duration_ms=0))

    def test_duration_over_limit_rejected(self):
        with pytest.raises(ValidationError):
            StoryboardPayload.model_validate(_payload(duration_ms=60001))

    def test_duration_at_limit_ok(self):
        p = StoryboardPayload.model_validate(_payload(duration_ms=60000))
        assert p.episodes[0].scenes[0].shots[0].duration_ms == 60000

    def test_subtitle_time_inverted_rejected(self):
        payload = _payload(
            subtitle_tracks=[{"text": "字幕", "start_ms": 3000, "end_ms": 1000}]
        )
        with pytest.raises(ValidationError):
            StoryboardPayload.model_validate(payload)

    def test_dialogue_time_inverted_rejected(self):
        payload = _payload(
            dialogue_tracks=[{"text": "台词", "start_ms": 2000, "end_ms": 2000}]
        )
        with pytest.raises(ValidationError):
            StoryboardPayload.model_validate(payload)

    def test_duplicate_shot_number_rejected(self):
        payload = {
            "episodes": [
                {
                    "number": 1,
                    "scenes": [{"number": 1, "shots": [_shot(1), _shot(1)]}],
                }
            ]
        }
        with pytest.raises(ValidationError):
            StoryboardPayload.model_validate(payload)

    def test_duplicate_scene_number_rejected(self):
        payload = {
            "episodes": [
                {"number": 1, "scenes": [{"number": 1}, {"number": 1}]},
            ]
        }
        with pytest.raises(ValidationError):
            StoryboardPayload.model_validate(payload)

    def test_duplicate_episode_number_rejected(self):
        payload = {"episodes": [{"number": 1}, {"number": 1}]}
        with pytest.raises(ValidationError):
            StoryboardPayload.model_validate(payload)


# ─────────────────────────────────────────
# 旧文本分镜解析（纯函数）
# ─────────────────────────────────────────


class TestParseLegacyText:
    def test_basic_shots_with_style(self):
        text = (
            "整体风格：日系动漫\n"
            "色调：暖色\n"
            "镜头1（3秒）【特写】：少女抬头看向天空\n"
            "镜头2（5秒）：街道人来人往\n"
        )
        payload = parse_legacy_storyboard_text(text, series_id="p1")
        assert payload["schema_version"] == 1
        assert payload["series_id"] == "p1"
        scene = payload["episodes"][0]["scenes"][0]
        assert "日系动漫" in scene["description"]
        assert "暖色" in scene["description"]
        shots = scene["shots"]
        assert len(shots) == 2
        assert shots[0]["number"] == 1
        assert shots[0]["duration_ms"] == 3000
        assert shots[0]["camera"]["description"] == "特写"
        assert "少女" in shots[0]["visual_prompt"]
        assert shots[1]["duration_ms"] == 5000

    def test_halfwidth_colon_and_no_duration(self):
        payload = parse_legacy_storyboard_text("镜头3: 一个长镜头")
        shot = payload["episodes"][0]["scenes"][0]["shots"][0]
        assert shot["number"] == 3
        assert shot["duration_ms"] == 5000  # 默认 5s

    def test_no_shots_raises(self):
        with pytest.raises(ValueError):
            parse_legacy_storyboard_text("整体风格：日系动漫")

    def test_duplicate_shot_number_raises(self):
        text = "镜头1：A\n镜头1：B\n"
        with pytest.raises(ValidationError):
            parse_legacy_storyboard_text(text)


# ─────────────────────────────────────────
# generation gate 纯逻辑：角色资产收集
# ─────────────────────────────────────────


class TestCollectCharacterAssetIds:
    def test_dedup_and_filter_episode(self):
        payload = {
            "episodes": [
                {
                    "number": 1,
                    "scenes": [
                        {"shots": [{"character_asset_ids": ["a", "b", "a"]}]},
                        {"shots": [{"character_asset_ids": ["b", "c"]}]},
                    ],
                },
                {
                    "number": 2,
                    "scenes": [{"shots": [{"character_asset_ids": ["z"]}]}],
                },
            ]
        }
        assert collect_character_asset_ids(payload) == ["a", "b", "c", "z"]
        assert collect_character_asset_ids(payload, episode_no=1) == ["a", "b", "c"]

    def test_empty(self):
        assert collect_character_asset_ids({}) == []


# ─────────────────────────────────────────
# 路由错误映射（monkeypatch service，验证 403/404/400）
# ─────────────────────────────────────────


class _FakeProject:
    def __init__(self, user_id=1):
        self.id = "proj-1"
        self.user_id = user_id


class TestRouterErrorMapping:
    def test_get_project_forbidden(self, client, monkeypatch):
        async def fake_get_project(session, project_id, user_id=None, is_admin=False):
            raise PermissionError("project belongs to another user")

        monkeypatch.setattr("app.routers.series.series_service.get_project", fake_get_project)
        resp = client.get("/api/series/projects/proj-1")
        assert resp.status_code == 403

    def test_get_project_not_found(self, client, monkeypatch):
        async def fake_get_project(session, project_id, user_id=None, is_admin=False):
            return None

        monkeypatch.setattr("app.routers.series.series_service.get_project", fake_get_project)
        resp = client.get("/api/series/projects/proj-x")
        assert resp.status_code == 404

    def test_save_storyboard_validation_error(self, client, monkeypatch):
        from fastapi import HTTPException

        async def fake_ctx(session, episode_id, user):
            return object(), _FakeProject()

        async def fake_save(session, project, episode_id, payload, created_by=""):
            raise ValueError("镜头时长必须在 1~60000ms 之间")

        monkeypatch.setattr("app.routers.series._owned_episode_project", fake_ctx)
        monkeypatch.setattr("app.routers.series.storyboard_service.save_storyboard", fake_save)
        resp = client.put("/api/series/episodes/ep-1/storyboard", json={"payload": {}})
        assert resp.status_code == 400
        assert "镜头时长" in resp.json()["detail"]

    def test_generation_gate_forbidden(self, client, monkeypatch):
        from fastapi import HTTPException

        async def fake_ctx(session, episode_id, user):
            raise HTTPException(status_code=403, detail="无权访问")

        monkeypatch.setattr("app.routers.series._owned_episode_project", fake_ctx)
        resp = client.get("/api/series/episodes/ep-1/generation-gate")
        assert resp.status_code == 403

    def test_generation_gate_ok(self, client, monkeypatch):
        async def fake_ctx(session, episode_id, user):
            return object(), _FakeProject()

        async def fake_gate(session, project_id, episode_id):
            return False, ["分镜 revision 1 未审批（当前状态 draft）"]

        monkeypatch.setattr("app.routers.series._owned_episode_project", fake_ctx)
        monkeypatch.setattr(
            "app.routers.series.approval_service.check_generation_gate", fake_gate
        )
        resp = client.get("/api/series/episodes/ep-1/generation-gate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert len(body["reasons"]) == 1

    def test_update_shot_not_found(self, client, monkeypatch):
        async def fake_update(session, shot_id, fields, user_id):
            raise ValueError("shot x not found")

        monkeypatch.setattr("app.routers.series.storyboard_service.update_shot", fake_update)
        resp = client.patch("/api/series/shots/x", json={"visual_prompt": "new"})
        assert resp.status_code == 404

    def test_update_shot_forbidden(self, client, monkeypatch):
        async def fake_update(session, shot_id, fields, user_id):
            raise PermissionError("shot belongs to another user")

        monkeypatch.setattr("app.routers.series.storyboard_service.update_shot", fake_update)
        resp = client.patch("/api/series/shots/x", json={"visual_prompt": "new"})
        assert resp.status_code == 403
