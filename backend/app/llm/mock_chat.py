"""Mock 对话模型：仅 ALLOW_MOCK_PROVIDERS=true 且没有真实对话模型时给“拆分镜头”用。

不产生外部调用。根据 Copilot 拆分提示里的“画幅 / 想法”确定性地拼出分镜 JSON：
- 想法里以“角色：”开头的行是角色（格式 `名字：外貌描述`），原样放进 characters；
- 以“镜头N：”开头的行是现成的镜头（格式 `镜头N：情节｜画面：…｜环境：…｜镜头：…｜出镜：甲、乙`，基准脚本使用），
  按顺序取前 N 个，画面 / 环境 / 镜头合成提示词，情节作旁白台词，出镜取自“出镜：”；
- 其余句子按镜头数均分，镜头出镜角色按句子里出现的角色名判断，每个镜头给一句旁白台词。
"""

import json
import re
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

_SHOTS = re.compile(r"大约\s*(\d+)\s*个镜头")
_SENTENCE_END = re.compile(r"(?<=[。！？!?；;\n])")
_SHOT_LINE = re.compile(r"^镜头\s*\d+\s*[：:]")


def _structured_shot(line: str, known: list[str]) -> dict[str, Any]:
    """`镜头N：情节｜画面：…｜环境：…｜镜头：…｜出镜：甲、乙` → SplitShot 形状。"""
    parts = [part.strip() for part in _SHOT_LINE.sub("", line).split("｜")]
    plot, fields = parts[0], {}
    for part in parts[1:]:
        key, _, value = part.partition("：")
        fields[key.strip()] = value.strip()
    names = [name.strip() for name in re.split(r"[、,，]", fields.get("出镜", "")) if name.strip()]
    prompt = "，".join(item for item in (fields.get("画面"), fields.get("环境"), fields.get("镜头")) if item)
    return {
        "title": plot[:20] or "镜头",
        "prompt": (prompt or plot)[:780],
        "characters": [name for name in names if name in known][:3],
        "duration_s": 4,
        "lines": [{"speaker": "旁白", "text": plot[:60]}] if plot else [],
    }


def build_mock_split(prompt: str) -> dict[str, Any]:
    """从拆分请求的用户消息构造 SplitResult 形状的 JSON。"""
    head, _, idea = prompt.partition("想法：")
    match = _SHOTS.search(head)
    count = max(1, min(12, int(match.group(1)))) if match else 4
    characters: list[dict[str, str]] = []
    story: list[str] = []
    for line in idea.splitlines():
        text = line.strip()
        if not text:
            continue
        if text.startswith("角色：") and "：" in text[3:]:
            name, _, description = text[3:].partition("：")
            characters.append({"name": name.strip()[:40], "description": description.strip()[:600]})
        else:
            story.append(text)
    structured = [line for line in story if _SHOT_LINE.match(line)]
    if structured:
        known = [item["name"] for item in characters]
        shots = [_structured_shot(line, known) for line in structured[:count]]
        return {"characters": characters[:6], "shots": shots}
    sentences = [part.strip() for part in _SENTENCE_END.split("".join(story)) if part.strip()] or [
        "一个简短的故事"
    ]
    shots = []
    for index in range(count):
        # 句子不够镜头数时循环使用，保证每个镜头都有画面描述
        sentence = (
            sentences[index * len(sentences) // count]
            if count <= len(sentences)
            else sentences[index % len(sentences)]
        )
        names = [item["name"] for item in characters if item["name"] in sentence][:3]
        shots.append(
            {
                "title": f"镜头{index + 1}",
                "prompt": sentence[:780],
                "characters": names,
                "duration_s": 4,
                "lines": [{"speaker": "旁白", "text": sentence[:60]}],
            }
        )
    return {"characters": characters[:6], "shots": shots}


class MockPlannerChat(BaseChatModel):
    """只会回答 Copilot 拆分请求的确定性假模型。"""

    @property
    def _llm_type(self) -> str:
        return "mock-planner"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        user = next((str(m.content) for m in reversed(messages) if m.type == "human"), "")
        content = json.dumps({**build_mock_split(user), "mocked": True}, ensure_ascii=False)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])
