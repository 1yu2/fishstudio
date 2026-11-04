"""画布剧本：结构校验、版本保存与合并、冲突、恢复、规则导入、AI 起草 / 改写（mock LLM）、对工作台 has_script 的影响。"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from test_node_canvas import _user, pg, requires_pg  # noqa: F401  (pg 是 fixture)

from app.routers import canvas_script as router
from app.services import canvas_script_service as service
from app.services import node_canvas_service as canvas_service
from app.services import workspace_overview_service as overview


def _payload(text="雪落竹林", scene_id="s1"):
    return {
        "acts": [
            {
                "title": "第一幕 · 相遇",
                "scenes": [
                    {
                        "id": scene_id,
                        "title": "雪夜竹林",
                        "heading": "外景 · 夜 · 大雪",
                        "blocks": [
                            {"type": "action", "text": text},
                            {"type": "dialogue", "character": "狐妖", "text": "你来了。"},
                        ],
                    }
                ],
            }
        ]
    }


async def _save(session, canvas, user, payload, based_on, source="user", seq="auto", **extra):
    """seq="auto" 时取当前最新行的 edit_seq（模拟“读到最新再保存”）；测试过期写入时显式传 seq。"""
    if seq == "auto":
        latest = await service._latest(session, canvas.id)
        seq = latest.edit_seq if latest else None
    body = router.ScriptSave(payload=payload, based_on=based_on, based_on_seq=seq, source=source, **extra)
    return await router.save_script(canvas.id, body, session=session, user=user)


async def _canvas(session, user):
    return await canvas_service.create_canvas(session, user, title="剧本测试")


# ── 结构校验（无需数据库） ─────────────────────────────────────────────────


def test_payload_limits_and_unique_scene_ids():
    service.ScriptPayload.model_validate(_payload())
    dup = {"id": "b1", "type": "action", "text": "x"}
    with pytest.raises(ValidationError):  # 块 id 重复
        service.ScriptPayload.model_validate({"acts": [{"scenes": [{"id": "s1", "blocks": [dup, dup]}]}]})
    service.ScriptPayload.model_validate(
        {"acts": [{"scenes": [{"id": "s1", "blocks": [{"type": "action"}] * 2}]}]}
    )  # 空 id 不查重
    scene = {"id": "a", "blocks": []}
    with pytest.raises(ValidationError):
        service.ScriptPayload.model_validate({"acts": [{"scenes": [scene, scene]}]})
    with pytest.raises(ValidationError):  # 超过 20 幕
        service.ScriptPayload.model_validate({"acts": [{} for _ in range(21)]})
    with pytest.raises(ValidationError):  # 超过 80 场
        scenes = [{"id": f"s{i}"} for i in range(81)]
        service.ScriptPayload.model_validate({"acts": [{"scenes": scenes[:40]}, {"scenes": scenes[40:]}]})
    with pytest.raises(ValidationError):  # 每场最多 200 块
        service.ScriptPayload.model_validate(
            {"acts": [{"scenes": [{"id": "s1", "blocks": [{"type": "action", "text": "x"}] * 201}]}]}
        )
    with pytest.raises(ValidationError):  # 单块最多 2000 字
        service.ScriptPayload.model_validate(
            {"acts": [{"scenes": [{"id": "s1", "blocks": [{"type": "action", "text": "x" * 2001}]}]}]}
        )


def test_word_count_ignores_whitespace_and_character_names():
    assert service.word_count(_payload("雪 落\n竹林")) == 4 + 4
    assert service.has_content({"acts": []}) is False
    assert service.has_content({"acts": [{"scenes": [{"blocks": [{"text": "  "}]}]}]}) is False


# ── 规则导入 ──────────────────────────────────────────────────────────────


def test_import_acts_and_scenes_with_dialogue():
    text = """第一幕 相遇
场 1 雪夜竹林
内景 · 夜
大雪压弯了竹子。
狐妖：你来了。
书生:我迷路了。
第二幕 离别
第2场 山门 外景 · 黄昏
钟声响起。
"""
    payload = service.parse_text(text)
    assert [act["title"] for act in payload["acts"]] == ["第一幕 相遇", "第二幕 离别"]
    first = payload["acts"][0]["scenes"][0]
    assert first["title"] == "雪夜竹林" and first["heading"] == "内景 · 夜"
    assert [(b["type"], b["character"], b["text"]) for b in first["blocks"]] == [
        ("action", "", "大雪压弯了竹子。"),
        ("dialogue", "狐妖", "你来了。"),
        ("dialogue", "书生", "我迷路了。"),
    ]
    second = payload["acts"][1]["scenes"][0]
    assert second["title"] == "山门" and second["heading"] == "外景 · 黄昏"
    assert [scene["id"] for act in payload["acts"] for scene in act["scenes"]] == ["s1", "s2"]


def test_import_dialogue_only_goes_into_first_act_first_scene():
    payload = service.parse_text(
        "甲：你好\n乙：你好呀，今天天气不错\n这一句名字太长太长太长太长太长：不是对白"
    )
    assert len(payload["acts"]) == 1 and payload["acts"][0]["title"] == "第一幕"
    blocks = payload["acts"][0]["scenes"][0]["blocks"]
    assert [b["type"] for b in blocks] == ["dialogue", "dialogue", "action"]
    assert blocks[1]["character"] == "乙"


def test_import_plain_prose_is_one_scene_of_actions():
    payload = service.parse_text("清晨，村口很安静。\n\n老人坐在树下。")
    assert len(payload["acts"]) == 1 and len(payload["acts"][0]["scenes"]) == 1
    scene = payload["acts"][0]["scenes"][0]
    assert [b["type"] for b in scene["blocks"]] == ["action", "action"]
    assert service.parse_text("") == {"acts": []}


def test_import_int_ext_lines_and_numbered_scenes():
    text = "EXT. 竹林 - 夜\n风很大。\n李白：走吧\nINT. 茅屋 - 夜\n灯亮着。\n3. 清晨\n鸟叫。"
    scenes = [scene for act in service.parse_text(text)["acts"] for scene in act["scenes"]]
    assert [s["heading"] for s in scenes] == ["EXT. 竹林 - 夜", "INT. 茅屋 - 夜", ""]
    assert scenes[2]["title"] == "清晨"
    assert scenes[0]["blocks"][1] == {"id": "", "type": "dialogue", "character": "李白", "text": "走吧"}


def test_import_too_long_text_rejected():
    with pytest.raises(service.ScriptError):
        service.parse_text("字" * 50_001)
    with pytest.raises(service.ScriptError):  # 超过 80 场
        service.parse_text("\n".join(f"场 {i} 标题" for i in range(1, 82)))


def test_import_endpoint_returns_payload_without_saving():
    # 导入不需要数据库之外的东西，但要求画布归属；这里只确认 body 长度校验
    with pytest.raises(ValidationError):
        router.ScriptImport(text="x" * 50_001)


# ── 数据库：权限 / 保存 / 合并 / 冲突 / 恢复 ───────────────────────────────


@requires_pg
def test_foreign_and_missing_canvas_are_404_everywhere(pg):
    async def _run():
        async with pg() as s:
            owner, other = await _user(s, "owner"), await _user(s, "other")
            admin = await _user(s, "root", admin=True)
            canvas = await _canvas(s, owner)
            errors = []
            for user, canvas_id in ((other, canvas.id), (owner, "missing"), (admin, canvas.id)):
                calls = [
                    router.get_script(canvas_id, session=s, user=user),
                    router.get_revision(canvas_id, 1, session=s, user=user),
                    _save_id(s, canvas_id, user),
                    router.restore_script(canvas_id, router.ScriptRestore(revision=1), session=s, user=user),
                    router.import_script(canvas_id, router.ScriptImport(text="x"), session=s, user=user),
                    router.draft_script(canvas_id, router.ScriptDraft(idea="x"), session=s, user=user),
                    router.rewrite_script(
                        canvas_id, router.ScriptRewrite(text="x", mode="rewrite"), session=s, user=user
                    ),
                ]
                for call in calls:
                    try:
                        await call
                    except HTTPException as exc:
                        errors.append((exc.status_code, exc.detail))
            return errors

    errors = asyncio.run(_run())
    assert errors == [(404, "项目不存在或无权访问")] * 21


def _save_id(session, canvas_id, user):
    body = router.ScriptSave(payload=_payload(), based_on=None)
    return router.save_script(canvas_id, body, session=session, user=user)


@requires_pg
def test_get_empty_then_save_creates_first_revision(pg):
    async def _run():
        async with pg() as s:
            user = await _user(s)
            canvas = await _canvas(s, user)
            empty = await router.get_script(canvas.id, session=s, user=user)
            saved = await _save(s, canvas, user, _payload(), None)
            return empty, saved, await router.get_script(canvas.id, session=s, user=user)

    empty, saved, loaded = asyncio.run(_run())
    assert empty == {"current": None, "revisions": []}
    assert saved["revision"] == 1 and saved["source"] == "user" and saved["word_count"] == 4 + 4
    assert loaded["current"]["payload"]["acts"][0]["scenes"][0]["heading"] == "外景 · 夜 · 大雪"
    assert [item["revision"] for item in loaded["revisions"]] == [1]


async def _age(session, canvas, revision, minutes):
    from sqlalchemy import update

    from app.db.models import CanvasScriptRevision

    stamp = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    await session.execute(
        update(CanvasScriptRevision)
        .where(CanvasScriptRevision.canvas_id == canvas.id, CanvasScriptRevision.revision == revision)
        .values(updated_at=stamp)
    )


@requires_pg
def test_merge_window_overwrites_then_new_revision_after_ten_minutes(pg):
    async def _run():
        async with pg() as s:
            user = await _user(s)
            canvas = await _canvas(s, user)
            first = await _save(s, canvas, user, _payload("一"), None)
            second = await _save(s, canvas, user, _payload("二二"), 1)  # 10 分钟内：覆盖
            await _age(s, canvas, 1, 11)
            third = await _save(s, canvas, user, _payload("三三三"), 1)  # 超过 10 分钟：新建
            listing = await router.get_script(canvas.id, session=s, user=user)
            return first, second, third, listing

    first, second, third, listing = asyncio.run(_run())
    assert (first["revision"], second["revision"], third["revision"]) == (1, 1, 2)
    assert second["payload"]["acts"][0]["scenes"][0]["blocks"][0]["text"] == "二二"
    assert (first["edit_seq"], second["edit_seq"], third["edit_seq"]) == (1, 2, 1)
    assert [item["revision"] for item in listing["revisions"]] == [2, 1]
    assert listing["revisions"][1]["word_count"] == 2 + 4


@requires_pg
def test_different_source_or_user_creates_new_revision(pg):
    async def _run():
        async with pg() as s:
            user, other = await _user(s, "a"), await _user(s, "b")
            canvas = await _canvas(s, user)
            await _save(s, canvas, user, _payload("一"), None, source="ai_draft")
            edit = await _save(s, canvas, user, _payload("二"), 1)  # 上一版是 ai_draft → 新建
            imported = await _save(
                s, canvas, user, _payload("三"), 2, source="import"
            )  # 这次是 import → 新建
            # 另一个用户（直接走 service，路由层只放行 owner）不覆盖别人的版本
            by_other = await service.save_script(
                s,
                canvas,
                other,
                service.ScriptPayload.model_validate(_payload("四")),
                based_on=3,
                based_on_seq=1,
            )
            return edit, imported, by_other

    edit, imported, by_other = asyncio.run(_run())
    assert (edit["revision"], edit["source"]) == (2, "user")
    assert (imported["revision"], imported["source"]) == (3, "import")
    assert by_other["revision"] == 4


@requires_pg
def test_stale_based_on_is_409(pg):
    async def _run():
        async with pg() as s:
            user = await _user(s)
            canvas = await _canvas(s, user)
            await _save(s, canvas, user, _payload(), None)
            await _age(s, canvas, 1, 30)
            await _save(s, canvas, user, _payload("改"), 1)  # → 版本 2
            errors = []
            for based_on in (1, None, 5):
                try:
                    await _save(s, canvas, user, _payload(), based_on)
                except HTTPException as exc:
                    errors.append((exc.status_code, exc.detail))
            return errors

    assert asyncio.run(_run()) == [(409, "剧本在别处被修改")] * 3


@requires_pg
def test_restore_creates_new_revision_from_old_content(pg):
    async def _run():
        async with pg() as s:
            user = await _user(s)
            canvas = await _canvas(s, user)
            await _save(s, canvas, user, _payload("最初"), None)
            await _age(s, canvas, 1, 30)
            await _save(s, canvas, user, _payload("后来"), 1)
            restored = await router.restore_script(
                canvas.id, router.ScriptRestore(revision=1), session=s, user=user
            )
            old = await router.get_revision(canvas.id, 1, session=s, user=user)
            try:
                await router.get_revision(canvas.id, 99, session=s, user=user)
                missing = None
            except HTTPException as exc:
                missing = exc.status_code
            try:
                await router.restore_script(
                    canvas.id, router.ScriptRestore(revision=99), session=s, user=user
                )
                missing_restore = None
            except HTTPException as exc:
                missing_restore = exc.status_code
            return restored, old, missing, missing_restore

    restored, old, missing, missing_restore = asyncio.run(_run())
    assert restored["revision"] == 3 and restored["source"] == "restore"
    assert restored["payload"]["acts"][0]["scenes"][0]["blocks"][0]["text"] == "最初"
    assert old["payload"] == restored["payload"] and old["revision"] == 1
    assert missing == 404 and missing_restore == 404


@requires_pg
def test_history_is_capped_at_fifty_newest_first(pg):
    async def _run():
        async with pg() as s:
            user = await _user(s)
            canvas = await _canvas(s, user)
            current = None
            for index in range(52):
                current = await service.save_script(
                    s,
                    canvas,
                    user,
                    service.ScriptPayload.model_validate(_payload(str(index))),
                    based_on=current["revision"] if current else None,
                    based_on_seq=current["edit_seq"] if current else None,
                    source="import",
                )
            return await router.get_script(canvas.id, session=s, user=user)

    listing = asyncio.run(_run())
    assert len(listing["revisions"]) == 50
    assert listing["revisions"][0]["revision"] == 52 and listing["current"]["revision"] == 52


def test_invalid_block_type_is_rejected_by_save_body():
    with pytest.raises(ValidationError):
        router.ScriptSave(
            payload={"acts": [{"scenes": [{"id": "s", "blocks": [{"type": "x", "text": "a"}]}]}]}
        )


@requires_pg
def test_import_endpoint_does_not_save(pg):
    async def _run():
        async with pg() as s:
            user = await _user(s)
            canvas = await _canvas(s, user)
            parsed = await router.import_script(
                canvas.id, router.ScriptImport(text="场 1 开场\n甲：你好"), session=s, user=user
            )
            return parsed, await router.get_script(canvas.id, session=s, user=user)

    parsed, listing = asyncio.run(_run())
    assert parsed["payload"]["acts"][0]["scenes"][0]["blocks"][0]["character"] == "甲"
    assert listing["current"] is None


# ── LLM（全部 mock） ──────────────────────────────────────────────────────


def _mock_llm(monkeypatch, replies):
    calls = []
    queue = iter(replies)

    async def fake(messages):
        calls.append(messages)
        reply = next(queue)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(canvas_service, "_call_llm", fake)
    return calls


DRAFT_JSON = json.dumps(
    {
        "acts": [
            {
                "title": "第一幕",
                "scenes": [
                    {
                        "id": "dup",
                        "title": "A",
                        "heading": "外景 · 夜",
                        "blocks": [
                            {"type": "action", "character": "不该有", "text": "雪地。"},
                            {"type": "dialogue", "character": "狐妖", "text": "你来了。"},
                        ],
                    },
                    {
                        "id": "dup",
                        "title": "B",
                        "heading": "",
                        "blocks": [{"type": "action", "text": "天亮。"}],
                    },
                ],
            }
        ]
    },
    ensure_ascii=False,
)


def test_draft_retries_once_after_bad_output(monkeypatch):
    calls = _mock_llm(monkeypatch, ["不是 JSON", DRAFT_JSON])
    payload = asyncio.run(
        service.draft_script(SimpleNamespace(aspect_ratio="9:16"), idea="雪夜狐妖", scenes=2)
    )
    assert len(calls) == 2 and "不符合要求" in calls[1][-1]["content"]
    scenes = payload["acts"][0]["scenes"]
    assert [s["id"] for s in scenes] == ["s1", "s2"]  # id 由服务端重排，去重
    assert scenes[0]["blocks"][0]["character"] == ""  # 动作块不带角色名


def test_draft_two_bad_outputs_is_502_and_vendor_error_is_502(monkeypatch):
    _mock_llm(monkeypatch, ["坏", '{"acts": []}'])  # 空剧本也算无效
    with pytest.raises(service.ScriptLLMError, match="AI 暂时不可用"):
        asyncio.run(service.draft_script(SimpleNamespace(aspect_ratio="9:16"), idea="x", scenes=2))
    _mock_llm(monkeypatch, [RuntimeError("vendor down")])
    with pytest.raises(service.ScriptLLMError):
        asyncio.run(service.draft_script(SimpleNamespace(aspect_ratio="9:16"), idea="x", scenes=2))


@requires_pg
def test_draft_and_rewrite_endpoints(pg, monkeypatch):
    async def _run():
        async with pg() as s:
            user = await _user(s)
            canvas = await _canvas(s, user)
            _mock_llm(monkeypatch, ["坏", DRAFT_JSON])
            drafted = await router.draft_script(
                canvas.id, router.ScriptDraft(idea="雪夜", scenes=2), session=s, user=user
            )
            saved_after_draft = await router.get_script(canvas.id, session=s, user=user)
            calls = _mock_llm(monkeypatch, ['{"text": "更短的话"}'])
            rewritten = await router.rewrite_script(
                canvas.id,
                router.ScriptRewrite(text="很长很长的话", mode="tone", tone="更古风", context="整块文字"),
                session=s,
                user=user,
            )
            _mock_llm(monkeypatch, ["坏", "还是坏"])
            errors = []
            for call in (
                router.rewrite_script(
                    canvas.id, router.ScriptRewrite(text="x", mode="shorten"), session=s, user=user
                ),
                router.draft_script(canvas.id, router.ScriptDraft(idea="x"), session=s, user=user),
            ):
                _mock_llm(monkeypatch, ["坏", "还是坏"])
                try:
                    await call
                except HTTPException as exc:
                    errors.append((exc.status_code, exc.detail))
            return drafted, saved_after_draft, rewritten, calls, errors

    drafted, saved, rewritten, calls, errors = asyncio.run(_run())
    assert len(drafted["payload"]["acts"][0]["scenes"]) == 2
    assert saved["current"] is None  # 起草不保存
    assert rewritten == {"text": "更短的话"}
    prompt = calls[0][-1]["content"]
    assert "更古风" in prompt and "整块文字" in prompt
    assert errors == [(502, "AI 暂时不可用，请稍后再试")] * 2


def test_rewrite_request_validation():
    with pytest.raises(ValidationError):
        router.ScriptRewrite(text="x" * 2001, mode="rewrite")
    with pytest.raises(ValidationError):
        router.ScriptRewrite(text="x", mode="other")
    with pytest.raises(ValidationError):
        router.ScriptDraft(idea="x", scenes=1)
    with pytest.raises(ValidationError):
        router.ScriptDraft(idea="x", scenes=9)


# ── 工作台 has_script ─────────────────────────────────────────────────────


@requires_pg
def test_has_script_follows_structured_script(pg):
    async def _run():
        async with pg() as s:
            user = await _user(s)
            canvas = await _canvas(s, user)  # 没有文本节点
            other = await _canvas(s, user)
            before = (await overview.project_summary(s, user, canvas.id))["steps"][0]["status"]
            await _save(s, canvas, user, _payload(), None)
            empty = {
                "acts": [
                    {"title": "", "scenes": [{"id": "s1", "blocks": [{"type": "action", "text": "  "}]}]}
                ]
            }
            await _save(s, other, user, empty, None)
            after = (await overview.project_summary(s, user, canvas.id))["steps"][0]["status"]
            blank = (await overview.project_summary(s, user, other.id))["steps"][0]["status"]
            return before, after, blank

    before, after, blank = asyncio.run(_run())
    assert before == "active" and after == "done"
    assert blank == "active"  # 全是空块不算有剧本


# ── 过期写入：合并覆盖不改版本号，靠 edit_seq 发现 ─────────────────────────


@requires_pg
def test_two_writers_merge_in_place_second_stale_writer_gets_409(pg):
    async def _run():
        async with pg() as s:
            user = await _user(s)
            canvas = await _canvas(s, user)
            created = await _save(s, canvas, user, _payload("初稿"), None)
            a = await _save(s, canvas, user, _payload("A 改"), 1, seq=created["edit_seq"])  # 窗口 A 合并进 v1
            try:
                await _save(
                    s, canvas, user, _payload("B 改"), 1, seq=created["edit_seq"]
                )  # 窗口 B 仍基于旧 seq
                stale = None
            except HTTPException as exc:
                stale = (exc.status_code, exc.detail)
            latest = await router.get_script(canvas.id, session=s, user=user)
            return a, stale, latest

    a, stale, latest = asyncio.run(_run())
    assert a["revision"] == 1 and a["edit_seq"] == 2
    assert stale == (409, "剧本在别处被修改")
    assert (
        latest["current"]["payload"]["acts"][0]["scenes"][0]["blocks"][0]["text"] == "A 改"
    )  # A 没被 B 覆盖


@requires_pg
def test_missing_seq_is_stale_once_a_version_exists(pg):
    async def _run():
        async with pg() as s:
            user = await _user(s)
            canvas = await _canvas(s, user)
            await _save(s, canvas, user, _payload(), None)
            try:
                await _save(s, canvas, user, _payload("x"), 1, seq=None)
            except HTTPException as exc:
                return exc.status_code

    assert asyncio.run(_run()) == 409


@requires_pg
def test_concurrent_writers_only_one_wins(pg):
    """两个会话同时基于同一状态保存：串行化后一个成功、一个 409。"""

    async def _run():
        async with pg() as setup:
            user = await _user(setup)
            canvas = await _canvas(setup, user)
            await _save(setup, canvas, user, _payload("初稿"), None)
            await setup.commit()
            canvas_id, user_id = canvas.id, user.id

        async def writer(text):
            async with pg() as s:
                from app.db.models import NodeCanvas, User

                u, c = await s.get(User, user_id), await s.get(NodeCanvas, canvas_id)
                try:
                    out = await _save(s, c, u, _payload(text), 1, seq=1)
                    await s.commit()
                    return out["edit_seq"]
                except HTTPException as exc:
                    await s.rollback()
                    return exc.status_code

        return await asyncio.gather(writer("甲"), writer("乙"))

    results = asyncio.run(_run())
    assert sorted(results, key=str) == [2, 409]


@requires_pg
def test_keepalive_supersedes_in_flight_save_in_either_order(pg):
    async def _run():
        async with pg() as s:
            user = await _user(s)
            canvas = await _canvas(s, user)
            # 在途保存（创建 v1，save_id=A）已落库后，keepalive 仍按创建前的状态（None）发来
            await _save(s, canvas, user, _payload("在途"), None, save_id="A")
            landed = await _save(
                s, canvas, user, _payload("最新"), None, seq=None, save_id="B", supersedes="A"
            )
            # 没指向已落库保存的 keepalive 仍然是过期写入
            try:
                await _save(s, canvas, user, _payload("无关"), None, seq=None, save_id="C", supersedes="zzz")
                bad = None
            except HTTPException as exc:
                bad = exc.status_code
            # keepalive 先到：按创建前的状态直接成功，之后在途保存（旧状态）被 409 拒绝
            canvas2 = await _canvas(s, user)
            first = await _save(s, canvas2, user, _payload("最新"), None, save_id="B", supersedes="A")
            try:
                await _save(s, canvas2, user, _payload("在途"), None, save_id="A")
                late = None
            except HTTPException as exc:
                late = exc.status_code
            return landed, bad, first, late

    landed, bad, first, late = asyncio.run(_run())
    assert (
        landed["revision"] == 1 and landed["payload"]["acts"][0]["scenes"][0]["blocks"][0]["text"] == "最新"
    )
    assert bad == 409 and first["revision"] == 1 and late == 409


@pytest.mark.parametrize(
    "bad",
    [
        '{"acts": [null]}',
        '{"acts": [{"scenes": [5]}]}',
        '{"acts": [{"scenes": [{"blocks": [null]}]}]}',
        '{"acts": "x"}',
        "[1]",
    ],
)
def test_draft_malformed_shapes_are_bad_output_not_500(monkeypatch, bad):
    calls = _mock_llm(monkeypatch, [bad, DRAFT_JSON])
    payload = asyncio.run(service.draft_script(SimpleNamespace(aspect_ratio="9:16"), idea="x", scenes=2))
    assert len(calls) == 2 and len(payload["acts"][0]["scenes"]) == 2
    _mock_llm(monkeypatch, [bad, bad])
    with pytest.raises(service.ScriptLLMError):
        asyncio.run(service.draft_script(SimpleNamespace(aspect_ratio="9:16"), idea="x", scenes=2))


def test_rewrite_malformed_shapes_are_bad_output(monkeypatch):
    _mock_llm(monkeypatch, ['{"text": null}', '{"text": "好"}'])
    assert asyncio.run(service.rewrite_text(text="x", mode="rewrite")) == {"text": "好"}
    _mock_llm(monkeypatch, ["{}", '{"text": 5}'])
    with pytest.raises(service.ScriptLLMError):
        asyncio.run(service.rewrite_text(text="x", mode="rewrite"))
