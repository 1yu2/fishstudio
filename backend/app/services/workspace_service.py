"""
Workspace Service - 管理工作空间身份记忆文件
AGENTS.md / TOOLS.md / IDENTITY.md / USER.md / SOUL.md / MEMORY.md
"""
import logging
from pathlib import Path
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# 文件名 -> (显示名称, 描述)  ← 顺序即注入顺序（优先级由高到低）
WORKSPACE_FILES: Dict[str, Tuple[str, str]] = {
    "AGENTS.md":   ("行为规则",   "Agent 的工作流规则、禁止操作清单和快捷指令"),
    "TOOLS.md":    ("工具配置",   "本地环境特有的工具参数（模型路径、服务地址等）"),
    "IDENTITY.md": ("Agent身份", "Agent 的名字、存在形式和视觉标识"),
    "USER.md":     ("用户画像",   "描述用户的风格偏好和常用角色"),
    "SOUL.md":     ("Agent人格", "定制 Agent 的沟通风格和创作偏好"),
    "MEMORY.md":   ("创作记忆",   "跨对话的长期记忆，Agent 自动维护"),
}

# 单文件注入字符上限
_MAX_CHARS = 5000
_HEAD_RATIO = 0.70
_TAIL_RATIO = 0.20

# 默认模板内容
_DEFAULT_TEMPLATES: Dict[str, str] = {
    "AGENTS.md": """\
# 行为规则

## 工作流约定
- 执行创作任务时，先确认理解用户意图，再调用工具
- 多步骤任务完成后，简短汇报结果，不啰嗦

## 禁止操作
- 不删除用户已确认满意的素材文件
- 不在未经用户确认的情况下覆盖现有文件

## 快捷指令
（在此定义自然语言宏，例如："出图" = 调用图片生成工具并返回结果）
""",
    "TOOLS.md": """\
# 工具配置

## 服务地址
- 后端 Base URL：http://localhost:8000
- ComfyUI 地址：（填写你的 ComfyUI 服务地址）

## 模型偏好
- 图片生成首选模型：（填写模型名称）
- TTS 偏好音色：（填写音色名称）

## 本地路径
- 参考图存放目录：（填写本地路径）
- 工作流文件路径：（填写 ComfyUI workflow JSON 路径）
""",
    "IDENTITY.md": """\
# Agent 身份

- **名字**：FishStudio
- **存在形式**：多模态创作 AI 助手
- **气质**：专业、高效、富有创造力
- **签名 Emoji**：🎨
- **头像**：（可填写图片路径或 URL）
""",
    "USER.md": """\
# 用户画像
- 昵称：
- 风格偏好：（如：写实、动漫、赛博朋克）
- 常用画面比例：（如：16:9、1:1）
- 常用角色：（名称 + 简要描述）
- TTS 偏好：（音色风格描述）
- 注意事项：（不喜欢什么、需要特别注意的）
""",
    "SOUL.md": """\
# Agent 人格设定
- 称呼用户：（如：主人、老板、同学）
- 沟通风格：（如：简洁专业、活泼有趣、温柔耐心）
- 创作偏好：（在提示词优化时的默认倾向）
- 特别说明：（其他个性化要求）
""",
    "MEMORY.md": """\
# 创作记忆

## 角色资产
（Agent 在此记录用户确认满意的角色信息和参考图路径）

## 成功 Prompt 模板
（Agent 在此记录效果好的提示词模板）

## 用户偏好记录
（Agent 在此记录用户明确表达的风格偏好）
""",
}


def get_workspace_dir() -> Path:
    """返回 workspace 目录路径（backend/workspace/）"""
    return Path(__file__).parent.parent.parent / "workspace"


def ensure_workspace_defaults() -> None:
    """首次运行时创建默认工作空间文件（已存在则跳过）"""
    workspace_dir = get_workspace_dir()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    for filename, template in _DEFAULT_TEMPLATES.items():
        file_path = workspace_dir / filename
        if not file_path.exists():
            file_path.write_text(template, encoding="utf-8")
            logger.info(f"📝 创建工作空间默认文件: {filename}")


def load_workspace_file(filename: str) -> str:
    """读取指定工作空间文件内容，文件不存在时返回空字符串"""
    if filename not in WORKSPACE_FILES:
        raise ValueError(f"未知的工作空间文件: {filename}")
    file_path = get_workspace_dir() / filename
    if not file_path.exists():
        return ""
    return file_path.read_text(encoding="utf-8")


def save_workspace_file(filename: str, content: str) -> None:
    """保存内容到指定工作空间文件"""
    if filename not in WORKSPACE_FILES:
        raise ValueError(f"未知的工作空间文件: {filename}")
    workspace_dir = get_workspace_dir()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / filename).write_text(content, encoding="utf-8")
    logger.info(f"💾 保存工作空间文件: {filename} ({len(content)} 字符)")


def _truncate(content: str) -> str:
    """超出 _MAX_CHARS 时截断，保留头 70% + 尾 20%"""
    if len(content) <= _MAX_CHARS:
        return content
    head_len = int(_MAX_CHARS * _HEAD_RATIO)
    tail_len = int(_MAX_CHARS * _TAIL_RATIO)
    head = content[:head_len]
    tail = content[-tail_len:]
    omitted = len(content) - head_len - tail_len
    return f"{head}\n\n... [省略 {omitted} 字符] ...\n\n{tail}"


def _is_empty_or_template(content: str, filename: str) -> bool:
    """判断文件内容是否为空或仅含模板注释（无实质内容）"""
    stripped = content.strip()
    if not stripped:
        return True
    # 检查是否与默认模板完全相同（用户未填写任何内容）
    template = _DEFAULT_TEMPLATES.get(filename, "").strip()
    if stripped == template:
        return True
    return False


def get_workspace_context() -> str:
    """
    返回拼接好的工作空间 prompt 注入块。
    仅注入有实质内容的文件，空文件/纯模板文件跳过。

    <工作空间>
        ## AGENTS.md（行为规则）
        {content}

        ## USER.md（用户画像）
        {content}
        ...
    </工作空间>

    """
    ensure_workspace_defaults()
    sections = []
    for filename, (display_name, _) in WORKSPACE_FILES.items():
        try:
            content = load_workspace_file(filename)
        except Exception as e:
            logger.warning(f"读取工作空间文件失败 {filename}: {e}")
            continue
        if _is_empty_or_template(content, filename):
            continue
        truncated = _truncate(content)
        sections.append(f"## {filename}（{display_name}）\n{truncated}")

    if not sections:
        return ""

    body = "\n\n".join(sections)
    return f"<工作空间>\n{body}\n</工作空间>"
