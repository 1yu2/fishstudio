"""画布剧本：结构化剧本（幕 → 场 → 块）的版本保存、规则导入、AI 起草与改写建议。

剧本以版本表 canvas_script_revisions 保存；连续打字产生的自动保存会合并进同一版（见 save_script），
AI 起草 / 导入 / 改写建议都只返回结果，由前端确认后再保存。导入不调用 LLM，纯规则解析。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CanvasScriptRevision, NodeCanvas, User
from app.services import node_canvas_service as canvas_service

logger = logging.getLogger(__name__)

MAX_ACTS = 20
MAX_SCENES = 80
MAX_BLOCKS = 200
MAX_BLOCK_CHARS = 2000
MAX_IMPORT_CHARS = 50_000
MERGE_WINDOW = timedelta(minutes=10)
HISTORY_LIMIT = 50
SOURCES = ("user", "ai_draft", "import", "restore")
LLM_UNAVAILABLE = "AI 暂时不可用，请稍后再试"


class ScriptError(ValueError):
    """面向用户的错误（422），消息可直接展示。"""


class ScriptConflict(ScriptError):
    """保存时基于的版本不是最新（别处改过）。"""


class ScriptLLMError(ScriptError):
    """模型不可用或返回无法识别的内容（502）。"""


# ── 结构 ──────────────────────────────────────────────────────────────────


class ScriptBlock(BaseModel):
    # 前端生成的块 id（用于稳定的 key / 改写建议定位）；可为空（导入 / AI 起草的结果由前端补齐）
    id: str = Field(default="", max_length=64)
    type: Literal["action", "dialogue"]
    character: str = Field(default="", max_length=40)
    text: str = Field(default="", max_length=MAX_BLOCK_CHARS)


class ScriptScene(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=100)
    heading: str = Field(default="", max_length=100)
    blocks: list[ScriptBlock] = Field(default_factory=list, max_length=MAX_BLOCKS)


class ScriptAct(BaseModel):
    title: str = Field(default="", max_length=100)
    scenes: list[ScriptScene] = Field(default_factory=list, max_length=MAX_SCENES)


class ScriptPayload(BaseModel):
    acts: list[ScriptAct] = Field(default_factory=list, max_length=MAX_ACTS)

    @model_validator(mode="after")
    def _scenes_limits(self) -> "ScriptPayload":
        ids = [scene.id for act in self.acts for scene in act.scenes]
        if len(ids) > MAX_SCENES:
            raise ValueError(f"最多 {MAX_SCENES} 场")
        if len(set(ids)) != len(ids):
            raise ValueError("场 id 重复")
        block_ids = [b.id for act in self.acts for scene in act.scenes for b in scene.blocks if b.id]
        if len(set(block_ids)) != len(block_ids):
            raise ValueError("块 id 重复")
        return self


def word_count(payload: dict[str, Any]) -> int:
    """字数 = 全部块文字（含角色名之外的台词与动作）的非空白字符数。"""
    total = 0
    for act in payload.get("acts") or []:
        for scene in act.get("scenes") or []:
            for block in scene.get("blocks") or []:
                total += len(re.sub(r"\s+", "", str(block.get("text") or "")))
    return total


def has_content(payload: Optional[dict[str, Any]]) -> bool:
    """至少有一块非空文字。"""
    for act in (payload or {}).get("acts") or []:
        for scene in act.get("scenes") or []:
            if any(str(block.get("text") or "").strip() for block in scene.get("blocks") or []):
                return True
    return False


# ── 版本 ──────────────────────────────────────────────────────────────────


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _full(row: CanvasScriptRevision) -> dict[str, Any]:
    return {
        "revision": row.revision,
        "payload": row.payload or {"acts": []},
        "source": row.source,
        "word_count": row.word_count,
        "edit_seq": row.edit_seq,
        "updated_at": _iso(row.updated_at),
    }


def _brief(row: CanvasScriptRevision) -> dict[str, Any]:
    return {
        "revision": row.revision,
        "source": row.source,
        "word_count": row.word_count,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


async def get_owned_canvas(session: AsyncSession, canvas_id: str, user: User) -> NodeCanvas:
    """只允许画布 owner（管理员也只看自己的）；别人的与不存在一视同仁。"""
    canvas = (
        await session.execute(
            select(NodeCanvas).where(NodeCanvas.id == canvas_id, NodeCanvas.owner_id == user.id)
        )
    ).scalar_one_or_none()
    if canvas is None:
        raise LookupError("项目不存在或无权访问")
    return canvas


async def _latest(session: AsyncSession, canvas_id: str) -> Optional[CanvasScriptRevision]:
    return (
        await session.execute(
            select(CanvasScriptRevision)
            .where(CanvasScriptRevision.canvas_id == canvas_id)
            .order_by(CanvasScriptRevision.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def get_script(session: AsyncSession, canvas: NodeCanvas) -> dict[str, Any]:
    rows = (
        (
            await session.execute(
                select(CanvasScriptRevision)
                .where(CanvasScriptRevision.canvas_id == canvas.id)
                .order_by(CanvasScriptRevision.revision.desc())
                .limit(HISTORY_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    return {"current": _full(rows[0]) if rows else None, "revisions": [_brief(row) for row in rows]}


async def get_revision(session: AsyncSession, canvas: NodeCanvas, revision: int) -> dict[str, Any]:
    row = (
        await session.execute(
            select(CanvasScriptRevision).where(
                CanvasScriptRevision.canvas_id == canvas.id, CanvasScriptRevision.revision == revision
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise LookupError("版本不存在")
    return _full(row)


async def save_script(
    session: AsyncSession,
    canvas: NodeCanvas,
    user: User,
    payload: ScriptPayload,
    *,
    based_on: Optional[int],
    based_on_seq: Optional[int] = None,
    source: str = "user",
    save_id: Optional[str] = None,
    supersedes: Optional[str] = None,
) -> dict[str, Any]:
    """保存剧本。based_on 必须是当前最新版本号（没有版本时为 None），based_on_seq 必须是这一版当前的 edit_seq，否则冲突。
    （合并覆盖不改版本号，所以只比版本号发现不了别处的覆盖，要再比 edit_seq。）

    页面关闭的 keepalive 写入带 supersedes = 在途保存的 save_id：在途保存若已落库（最新行的 last_save_id 等于它），
    即使 based_on 已过期也接受；没落库则按 based_on 正常校验。两种到达顺序下最新内容都不会丢。

    合并规则：最新一版 source=user、同一用户、更新时间不足 10 分钟，且这次也是用户编辑 → 覆盖这一版（版本号不变），
    避免连续打字刷出大量版本；否则新建下一版。
    """
    if source not in SOURCES:
        raise ScriptError("来源不合法")
    # 同一画布的保存串行化，避免并发时版本号撞车
    await canvas_service.advisory_lock(session, f"canvas-script:{canvas.id}")
    latest = await _latest(session, canvas.id)
    matches = (latest.revision if latest else None) == based_on and (
        latest is None or latest.edit_seq == based_on_seq
    )
    landed = bool(supersedes) and latest is not None and latest.last_save_id == supersedes
    if not (matches or landed):
        raise ScriptConflict("剧本在别处被修改")
    data = payload.model_dump()
    now = datetime.now(timezone.utc)
    if (
        latest is not None
        and source == "user"
        and latest.source == "user"
        and latest.created_by == user.id
        and now - latest.updated_at < MERGE_WINDOW
    ):
        latest.payload = data
        latest.word_count = word_count(data)
        latest.updated_at = now
        latest.edit_seq = latest.edit_seq + 1
        latest.last_save_id = save_id
        await session.flush()
        return _full(latest)
    return await _create_revision(session, canvas, user, data, source, latest, now, save_id)


async def _create_revision(
    session: AsyncSession,
    canvas: NodeCanvas,
    user: User,
    data: dict[str, Any],
    source: str,
    latest: Optional[CanvasScriptRevision],
    now: datetime,
    save_id: Optional[str] = None,
) -> dict[str, Any]:
    row = CanvasScriptRevision(
        canvas_id=canvas.id,
        revision=(latest.revision if latest else 0) + 1,
        payload=data,
        source=source,
        created_by=user.id,
        word_count=word_count(data),
        edit_seq=1,
        last_save_id=save_id,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    return _full(row)


async def restore_script(
    session: AsyncSession, canvas: NodeCanvas, user: User, revision: int
) -> dict[str, Any]:
    """以某一版内容新建一版（source=restore），历史版本本身不动。"""
    await canvas_service.advisory_lock(session, f"canvas-script:{canvas.id}")
    target = await get_revision(session, canvas, revision)
    latest = await _latest(session, canvas.id)
    return await _create_revision(
        session, canvas, user, target["payload"], "restore", latest, datetime.now(timezone.utc)
    )


async def scripted_canvas_ids(session: AsyncSession, canvas_ids: list[str]) -> set[str]:
    """批量判断哪些画布的最新剧本至少有一块非空文字（工作台 has_script 用，一次查询）。"""
    if not canvas_ids:
        return set()
    rows = (
        await session.execute(
            select(CanvasScriptRevision.canvas_id, CanvasScriptRevision.payload)
            .where(CanvasScriptRevision.canvas_id.in_(canvas_ids))
            .distinct(CanvasScriptRevision.canvas_id)
            .order_by(CanvasScriptRevision.canvas_id, CanvasScriptRevision.revision.desc())
        )
    ).all()
    return {row.canvas_id for row in rows if has_content(row.payload)}


# ── 规则导入（不调用 LLM） ─────────────────────────────────────────────────

_NUM = r"[0-9一二三四五六七八九十百零两]+"
_ACT_RE = re.compile(rf"^第\s*{_NUM}\s*幕")
_SCENE_RES = (
    re.compile(rf"^场\s*{_NUM}\s*(?P<rest>.*)$"),
    re.compile(rf"^第\s*{_NUM}\s*场\s*(?P<rest>.*)$"),
    re.compile(r"^[0-9]+\s*[.、．]\s*(?P<rest>\S.*)$"),
)
_HEADING_RE = re.compile(r"(INT\.?|EXT\.?|内景|外景)", re.IGNORECASE)
_DIALOGUE_RE = re.compile(
    r"^(?P<name>[^:：，。！？,.!?;；\s][^:：，。！？,.!?;；]{0,7}?)\s*[:：]\s*(?P<text>\S.*)$"
)
_SEPARATORS = " \t:：·、-—.．"


def _scene_marker(line: str) -> Optional[str]:
    """返回场标记行去掉标记后的剩余文字；不是场标记返回 None。"""
    for pattern in _SCENE_RES:
        match = pattern.match(line)
        if match:
            return match.group("rest").strip(_SEPARATORS)
    return None


def parse_text(text: str) -> dict[str, Any]:
    """把纯文本按规则解析成剧本 payload（不保存）。"""
    if len(text) > MAX_IMPORT_CHARS:
        raise ScriptError(f"文本最多 {MAX_IMPORT_CHARS} 字")
    acts: list[dict[str, Any]] = []
    scene_count = 0

    def new_act(title: str) -> dict[str, Any]:
        act = {"title": title, "scenes": []}
        acts.append(act)
        return act

    def new_scene(title: str = "", heading: str = "") -> dict[str, Any]:
        nonlocal scene_count
        scene_count += 1
        scene = {"id": f"s{scene_count}", "title": title, "heading": heading, "blocks": []}
        (acts[-1] if acts else new_act("第一幕"))["scenes"].append(scene)
        return scene

    scene: Optional[dict[str, Any]] = None
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if _ACT_RE.match(line):
            new_act(line[:100])
            scene = None
            continue
        marker = _scene_marker(line)
        if (
            marker is None
            and _HEADING_RE.match(line)
            and (scene is None or scene["blocks"] or scene["heading"])
        ):
            # 以 INT./EXT./内景/外景开头的整行本身就是新场的 heading
            scene = new_scene(heading=line[:100])
            continue
        if marker is not None:
            title, heading = marker, ""
            found = _HEADING_RE.search(marker)
            if found:
                title, heading = marker[: found.start()].strip(_SEPARATORS), marker[found.start() :].strip()
            scene = new_scene(title[:100], heading[:100])
            continue
        if scene is not None and not scene["blocks"] and not scene["heading"] and _HEADING_RE.match(line):
            scene["heading"] = line[:100]  # 场标记下一行的“内景 · 夜”样式
            continue
        if scene is None:
            scene = new_scene()
        match = _DIALOGUE_RE.match(line)
        if match:
            block = {
                "type": "dialogue",
                "character": match.group("name").strip(),
                "text": match.group("text").strip(),
            }
            blocks = [block]
        else:
            blocks = [{"type": "action", "character": "", "text": line}]
        for item in blocks:
            for start in range(0, len(item["text"]), MAX_BLOCK_CHARS):
                scene["blocks"].append({**item, "text": item["text"][start : start + MAX_BLOCK_CHARS]})
    try:
        return ScriptPayload.model_validate({"acts": acts}).model_dump()
    except ValidationError as error:
        raise ScriptError("剧本太长：最多 20 幕、80 场、每场 200 块") from error


# ── LLM：起草与改写建议 ────────────────────────────────────────────────────

DRAFT_PROMPT = """你是短视频编剧。根据创作者的想法写一个分场剧本，只输出一个 JSON 对象，不要解释：
{"acts": [{"title": "第一幕 · 相遇", "scenes": [{"id": "s1", "title": "场景短标题", "heading": "外景 · 夜 · 大雪",
 "blocks": [{"type": "action", "text": "动作与画面描写"}, {"type": "dialogue", "character": "角色名", "text": "台词"}]}]}]}
要求：场数等于要求的场数；每场 3 到 8 个块，动作描写具体可视化，台词口语化且每句不超过 30 字；角色名前后一致；不要编造与想法无关的设定。"""

REWRITE_PROMPT = """你是剧本润色助手。按要求改写给定的一段文字，只输出一个 JSON 对象，不要解释：{"text": "改写后的文字"}
要求：保持原意与人物关系；只返回这一段改写后的文字，不要加引号或前后缀。"""

TONE_FALLBACK = "更口语"


async def _ask_json(messages: list[dict[str, str]], validate) -> Any:
    """调用模型并校验 JSON；第一次校验失败带着错误重试一次，仍失败抛 ScriptLLMError。"""
    try:
        raw = await canvas_service._call_llm(messages)
    except Exception as error:  # noqa: BLE001 - 厂商 / 配置错误统一成面向用户的提示
        logger.warning("script llm call failed: %s", error)
        raise ScriptLLMError(LLM_UNAVAILABLE) from error
    try:
        return validate(canvas_service._parse_json(raw))
    except Exception as error:  # noqa: BLE001 - 任何解析 / 形状错误都按坏输出处理（重试一次，仍失败 502）
        messages = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": f"输出不符合要求：{str(error)[:300]}。只输出修正后的完整 JSON。"},
        ]
    try:
        repaired = await canvas_service._call_llm(messages)
        return validate(canvas_service._parse_json(repaired))
    except Exception as error:  # noqa: BLE001
        logger.warning("script llm output invalid after retry: %s", error)
        raise ScriptLLMError(LLM_UNAVAILABLE) from error


def _validate_draft(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("acts"), list):
        raise ValueError("缺少 acts")
    scene_no = 0
    for act in data["acts"]:
        if not isinstance(act, dict) or not isinstance(act.get("scenes", []), list):
            raise ValueError("幕格式不对")
        for scene in act.get("scenes") or []:
            if not isinstance(scene, dict) or not isinstance(scene.get("blocks", []), list):
                raise ValueError("场格式不对")
            scene_no += 1
            scene["id"] = f"s{scene_no}"  # id 由服务端统一生成，避免模型重复
            for block in scene.get("blocks") or []:
                if not isinstance(block, dict):
                    raise ValueError("块格式不对")
                block.pop("id", None)
                if block.get("type") != "dialogue":
                    block["character"] = ""
    payload = ScriptPayload.model_validate(data)
    if not has_content(payload.model_dump()):
        raise ValueError("剧本没有内容")
    return payload.model_dump()


async def draft_script(canvas: NodeCanvas, *, idea: str, scenes: int) -> dict[str, Any]:
    if not idea.strip():
        raise ScriptError("先写一个想法")
    messages = [
        {"role": "system", "content": DRAFT_PROMPT},
        {"role": "user", "content": f"画幅 {canvas.aspect_ratio}，共 {scenes} 场。想法：\n{idea.strip()}"},
    ]
    return await _ask_json(messages, _validate_draft)


class _RewriteResult(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_BLOCK_CHARS)

    @field_validator("text")
    @classmethod
    def _strip(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("改写结果为空")
        return value.strip()


async def rewrite_text(*, text: str, mode: str, tone: str = "", context: str = "") -> dict[str, str]:
    if not text.strip():
        raise ScriptError("先选中要改写的文字")
    ask = {
        "rewrite": "换一种写法，意思不变",
        "shorten": "缩短到原来一半左右，保留关键信息",
        "tone": f"改成「{tone.strip() or TONE_FALLBACK}」的语气",
    }[mode]
    user = f"要求：{ask}\n需要改写的文字：\n{text}"
    if context.strip():
        user += f"\n\n所在段落（仅供参考，不要改写）：\n{context}"
    messages = [{"role": "system", "content": REWRITE_PROMPT}, {"role": "user", "content": user}]
    result = await _ask_json(messages, lambda data: _RewriteResult.model_validate(data))
    return {"text": result.text}
