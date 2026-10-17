"""通过 Model Runtime 执行结构化剧本/连续性 Observer。"""

from __future__ import annotations

import json
import re
from typing import Any

from app.model_runtime import (
    ChatModelRequest,
    ModelPolicy,
    ModelRequestContext,
    get_model_runtime,
)
from app.schemas.series import ObserverFinding


def _json_content(response: Any) -> dict[str, Any]:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content
        )
    text = str(content).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Observer 模型必须返回 JSON 对象")
    return parsed


async def run_review(
    *,
    project_id: str,
    episode_id: str,
    workflow_step_id: str | None,
    review_type: str,
    target_type: str,
    target_revision: int,
    target_payload: dict[str, Any],
    issue_codes: list[str],
    categories: list[str],
    prompt_version: str,
) -> tuple[float, str, list[dict[str, Any]], str | None]:
    scope = {"issue_codes": issue_codes, "categories": categories}
    prompt = [
        {
            "role": "system",
            "content": (
                "你是影视制作 Observer。只能建议、评分和标记风险，不能代表人工审批。"
                "返回 JSON：score(0-100)、summary、findings。每个 finding 必须包含 "
                "code/category/severity(info|warning|major|critical)/evidence/location/suggested_fix。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "review_type": review_type,
                    "target_type": target_type,
                    "target_revision": target_revision,
                    "target": target_payload,
                    "targeted_scope": scope,
                    "criteria": (
                        ["motivation", "pacing", "hook", "shootability", "shot_load"]
                        if target_type == "script"
                        else ["direction", "location", "costume", "action", "eyeline", "lighting"]
                    ),
                },
                ensure_ascii=False,
            ),
        },
    ]
    result = await get_model_runtime().invoke_chat(
        prompt,
        ChatModelRequest(
            policy=ModelPolicy(
                purpose="script_reviewer" if target_type == "script" else "continuity_reviewer",
                quality_tier="quality",
                required_capabilities=frozenset({"json_schema"}),
            ),
            metadata={"review_type": review_type, "target_revision": target_revision},
        ),
        context=ModelRequestContext(
            project_id=project_id,
            workflow_step_id=workflow_step_id,
            creative_role="observer",
            prompt_version=prompt_version,
        ),
    )
    parsed = _json_content(result.response)
    score = min(100.0, max(0.0, float(parsed.get("score", 0))))
    findings = [
        ObserverFinding.model_validate(item).model_dump(mode="json")
        for item in (parsed.get("findings") or [])
    ]
    return score, str(parsed.get("summary") or ""), findings, result.invocation_id
