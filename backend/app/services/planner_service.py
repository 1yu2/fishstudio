"""Agent Planner：规则版 + 受控 LLM 版（P2-1）。

plan_from_template 解析 user_goal 中的集数/每集镜头数（"N集"、"每集M镜头"），
缺省 1 集 6 镜头，输出模板 + params 后经 workflow_service.validate_plan 校验。

plan_with_llm 是**受控最小实现**：LLM 只输出固定 JSON 字段
{episodes, shots_per_episode, aspect_ratio, provider_policy, extra_nodes?}，
服务端做参数范围校验 + validate_plan；任何非法输出（JSON 解析失败、越界、
未知字段/节点）一律回退规则版并在结果标 planner_fallback=True。
"""

import asyncio
import json
import logging
import re
from typing import Any, Optional

from app.services import workflow_service

logger = logging.getLogger(__name__)

DEFAULT_EPISODES = 1
DEFAULT_SHOTS_PER_EPISODE = 6

# LLM Planner 硬约束（比模板上限更严，受控最小实现）
LLM_MAX_EPISODES = 10
LLM_MAX_SHOTS_PER_EPISODE = 16
LLM_ALLOWED_FIELDS = {"episodes", "shots_per_episode", "aspect_ratio", "provider_policy", "extra_nodes"}
LLM_ALLOWED_PROVIDER_POLICIES = {"balanced", "quality", "fast", "cheap"}

# LLM 调用超时（秒）：超时按失败处理，回退规则版
LLM_TIMEOUT_SECONDS = 10

_EPISODES_RE = re.compile(r"(\d+)\s*集")
_SHOTS_RE = re.compile(r"每集\s*(\d+)\s*(?:个)?\s*镜头")
_ASPECT_RE = re.compile(r"(\d+)\s*[:：]\s*(\d+)")


def parse_goal(user_goal: str) -> dict[str, int | str]:
    """规则解析 user_goal（纯函数，便于单测）。"""
    text = user_goal or ""
    episodes = DEFAULT_EPISODES
    m = _EPISODES_RE.search(text)
    if m:
        episodes = int(m.group(1))
    shots = DEFAULT_SHOTS_PER_EPISODE
    m = _SHOTS_RE.search(text)
    if m:
        shots = int(m.group(1))
    aspect_ratio = "9:16"
    m = _ASPECT_RE.search(text)
    if m:
        aspect_ratio = f"{m.group(1)}:{m.group(2)}"
    return {"episodes": episodes, "shots_per_episode": shots, "aspect_ratio": aspect_ratio}


def plan_from_template(user_goal: str, project_settings: dict[str, Any]) -> dict[str, Any]:
    """规则版 Planner：user_goal + 项目设置 → {template_key, params}（已校验）。"""
    parsed = parse_goal(user_goal)
    settings = project_settings or {}
    params: dict[str, Any] = {
        "episodes": parsed["episodes"],
        "shots_per_episode": parsed["shots_per_episode"],
        "aspect_ratio": settings.get("aspect_ratio") or parsed["aspect_ratio"],
        "provider_policy": settings.get("provider_policy", "balanced"),
    }
    if settings.get("cost_limit") is not None:
        params["cost_limit"] = settings["cost_limit"]

    plan: dict[str, Any] = {"template_key": "short-drama", "params": params}
    ok, errors = workflow_service.validate_plan(plan)
    if not ok:
        raise ValueError(f"plan 校验失败: {'; '.join(errors)}")
    return plan


_LLM_PROMPT = """你是短剧工作流 Planner。根据用户目标输出**仅一个 JSON 对象**，不要输出其他文字。

只允许这些字段（多一个字段都算非法）：
- "episodes": 整数，1..{max_episodes}（集数）
- "shots_per_episode": 整数，1..{max_shots}（每集镜头数）
- "aspect_ratio": 字符串画幅，如 "9:16"、"16:9"、"1:1"
- "provider_policy": 字符串，只能是 balanced / quality / fast / cheap
- "extra_nodes": 可选，字符串数组，额外的已注册节点类型

项目设置：{settings}
用户目标：{goal}
"""


def _parse_llm_plan_json(text: str) -> dict[str, Any]:
    """从 LLM 输出提取 JSON 对象并做字段/范围硬校验（纯函数）。

    非法一律抛 ValueError（调用方回退规则版）。
    """
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        raise ValueError("LLM 输出不含 JSON 对象")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("LLM 输出不是 JSON 对象")
    unknown = set(data) - LLM_ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"LLM 输出含非法字段: {sorted(unknown)}")

    episodes = data.get("episodes")
    shots = data.get("shots_per_episode")
    if not isinstance(episodes, int) or not (1 <= episodes <= LLM_MAX_EPISODES):
        raise ValueError(f"episodes 超出范围 1..{LLM_MAX_EPISODES}: {episodes}")
    if not isinstance(shots, int) or not (1 <= shots <= LLM_MAX_SHOTS_PER_EPISODE):
        raise ValueError(f"shots_per_episode 超出范围 1..{LLM_MAX_SHOTS_PER_EPISODE}: {shots}")
    aspect = data.get("aspect_ratio", "9:16")
    if not isinstance(aspect, str) or not re.fullmatch(r"\d+:\d+", aspect):
        raise ValueError(f"aspect_ratio 非法: {aspect}")
    policy = data.get("provider_policy", "balanced")
    if policy not in LLM_ALLOWED_PROVIDER_POLICIES:
        raise ValueError(f"provider_policy 非法: {policy}")
    extra_nodes = data.get("extra_nodes") or []
    if not isinstance(extra_nodes, list):
        raise ValueError("extra_nodes 必须是数组")
    return {
        "episodes": episodes,
        "shots_per_episode": shots,
        "aspect_ratio": aspect,
        "provider_policy": policy,
        "extra_nodes": extra_nodes,
    }


def _llm_model_name(llm: Any) -> str:
    """从 langchain ChatModel 上取模型名（各 provider 字段不一致，逐级兜底）。"""
    name = getattr(llm, "model_name", None) or getattr(llm, "model", None)
    return str(name) if name else type(llm).__name__


async def plan_with_llm(
    goal: str,
    project_settings: dict[str, Any],
    llm: Optional[Any] = None,
) -> dict[str, Any]:
    """受控 LLM Planner（P2-1）：LLM 输出受字段白名单 + 参数范围 + validate_plan
    三重约束；任何一步非法都回退规则版并标 planner_fallback=True。

    llm 可注入（langchain ChatModel，测试用 mock）；缺省用 app.llm.factory
    create_llm()。extra_nodes 仅接受已注册 node_type，未知节点视为非法回退。

    返回 plan 附带：
    - planner_model：实际使用的模型名（LLM 成功）或 "rule-based"（回退）；
    - planner_fallback/fallback_reason：回退标记与原因；
    - extra_nodes：成功时透传已校验的额外节点（str 列表），供 create_run 落地。
    """
    from app.services.workflow_nodes import NODE_TYPES

    if llm is None:
        from app.llm.factory import create_llm

        llm = create_llm()

    def _fallback(reason: str) -> dict[str, Any]:
        logger.warning(f"LLM Planner 回退规则版: {reason}")
        plan = plan_from_template(goal, project_settings)
        plan["planner_fallback"] = True
        plan["fallback_reason"] = reason
        plan["planner_model"] = "rule-based"
        return plan

    prompt = _LLM_PROMPT.format(
        max_episodes=LLM_MAX_EPISODES,
        max_shots=LLM_MAX_SHOTS_PER_EPISODE,
        settings=json.dumps(project_settings or {}, ensure_ascii=False),
        goal=goal,
    )
    try:
        response = await asyncio.wait_for(llm.ainvoke(prompt), timeout=LLM_TIMEOUT_SECONDS)
        text = getattr(response, "content", response)
        if isinstance(text, list):  # 部分模型返回 content blocks
            text = "".join(str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in text)
        parsed = _parse_llm_plan_json(str(text))
    except Exception as e:
        return _fallback(str(e))

    extra = parsed.pop("extra_nodes")
    unknown_nodes = [n for n in extra if n not in NODE_TYPES]
    if unknown_nodes:
        return _fallback(f"extra_nodes 含未注册节点: {unknown_nodes}")

    settings = project_settings or {}
    params: dict[str, Any] = {
        "episodes": parsed["episodes"],
        "shots_per_episode": parsed["shots_per_episode"],
        "aspect_ratio": settings.get("aspect_ratio") or parsed["aspect_ratio"],
        "provider_policy": parsed["provider_policy"],
    }
    if settings.get("cost_limit") is not None:
        params["cost_limit"] = settings["cost_limit"]

    plan: dict[str, Any] = {"template_key": "short-drama", "params": params}
    ok, errors = workflow_service.validate_plan(plan)
    if not ok:
        return _fallback(f"validate_plan 失败: {'; '.join(errors)}")
    plan["planner_model"] = _llm_model_name(llm)
    plan["extra_nodes"] = extra
    return plan
