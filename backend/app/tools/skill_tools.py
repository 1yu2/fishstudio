"""
Skill 文件工具

提供读取、写入、初始化三类工具，供 agent 在 Progressive Loading 模式下
按需读取 SKILL.md 及其子资源，也可在对话中创建新 Skill。

安全限制：
- 读取：路径必须在 skills/ 目录内
- 写入：路径必须在 skills/custom/ 目录内，防止篡改 public/
"""
import logging
import re
import sys
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# skills/ 目录的绝对路径（backend/skills/）
_SKILLS_ROOT = (Path(__file__).parent.parent.parent / "skills").resolve()
# 用户自定义 skill 只允许写入 custom/
_CUSTOM_ROOT = _SKILLS_ROOT / "custom"


def _safe_resolve(path_str: str) -> Path | None:
    """
    将路径字符串解析为绝对路径，并验证是否在 skills/ 目录内。
    不在范围内返回 None。
    """
    try:
        p = Path(path_str)
        # 相对路径转绝对（相对于 skills/ 目录）
        if not p.is_absolute():
            p = _SKILLS_ROOT / p
        resolved = p.resolve()
        # 安全检查：必须在 skills/ 目录树内
        resolved.relative_to(_SKILLS_ROOT)
        return resolved
    except (ValueError, OSError):
        return None


# ─── read_file ────────────────────────────────────────────────────────

class ReadFileInput(BaseModel):
    path: str = Field(description="要读取的文件路径（绝对路径或相对于 skills/ 的路径）")
    start_line: Optional[int] = Field(default=None, description="起始行号（1-based，可选）")
    end_line: Optional[int] = Field(default=None, description="结束行号（1-based，含，可选）")


@tool("read_skill_file", args_schema=ReadFileInput)
def read_skill_file_tool(path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """
    读取 skill 文件内容。用于加载 SKILL.md 及其 references/、scripts/ 等子文件。
    路径必须在 skills/ 目录内。支持按行范围读取。
    """
    resolved = _safe_resolve(path)
    if resolved is None:
        return f"Error: 路径不在允许范围内或无效: {path}"
    if not resolved.exists():
        return f"Error: 文件不存在: {path}"
    if not resolved.is_file():
        return f"Error: 不是文件: {path}"

    try:
        lines = resolved.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        return f"Error: 读取失败: {e}"

    start = (start_line - 1) if start_line else 0
    end = end_line if end_line else len(lines)
    selected = lines[start:end]

    # 带行号输出，方便 agent 定位
    return "\n".join(f"{start + i + 1}: {line}" for i, line in enumerate(selected))


# ─── ls ──────────────────────────────────────────────────────────────

class LsInput(BaseModel):
    path: str = Field(description="要列出的目录路径（绝对路径或相对于 skills/ 的路径）")


@tool("list_skill_dir", args_schema=LsInput)
def list_skill_dir_tool(path: str) -> str:
    """
    列出 skill 目录下的文件和子目录。用于探索 SKILL.md 同级的 references/、scripts/ 等资源。
    路径必须在 skills/ 目录内。
    """
    resolved = _safe_resolve(path)
    if resolved is None:
        return f"Error: 路径不在允许范围内或无效: {path}"
    if not resolved.exists():
        return f"Error: 目录不存在: {path}"
    if not resolved.is_dir():
        return f"Error: 不是目录: {path}"

    try:
        entries = sorted(resolved.iterdir(), key=lambda p: (p.is_file(), p.name))
    except Exception as e:
        return f"Error: 无法列出目录: {e}"

    lines = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        prefix = "📄" if entry.is_file() else "📁"
        lines.append(f"{prefix} {entry.name}")

    return "\n".join(lines) if lines else "(空目录)"


# ─── 写入工具（仅限 custom/） ─────────────────────────────────────────

def _safe_resolve_custom(path_str: str) -> Path | None:
    """
    将路径解析为绝对路径，并验证必须在 skills/custom/ 内。
    不满足时返回 None。
    """
    try:
        p = Path(path_str)
        if not p.is_absolute():
            p = _CUSTOM_ROOT / p
        resolved = p.resolve()
        resolved.relative_to(_CUSTOM_ROOT)
        return resolved
    except (ValueError, OSError):
        return None


_SKILL_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$')


class InitSkillInput(BaseModel):
    skill_name: str = Field(
        description="Skill 的目录名，使用 hyphen-case（小写字母、数字、连字符），如 novel-writer"
    )


@tool("init_skill", args_schema=InitSkillInput)
def init_skill_tool(skill_name: str) -> str:
    """
    在 skills/custom/ 下初始化一个新 Skill 目录。
    创建 SKILL.md 模板、scripts/、references/、assets/ 三个子目录及示例文件。
    skill_name 必须是 hyphen-case（小写字母、数字、连字符），最长 64 字符。
    """
    if not _SKILL_NAME_RE.match(skill_name):
        return f"Error: skill_name '{skill_name}' 格式无效，必须是 hyphen-case（小写字母、数字、连字符）"

    scripts_dir = (
        Path(__file__).parent.parent.parent
        / "skills" / "public" / "skill-creator" / "scripts"
    ).resolve()

    if not scripts_dir.exists():
        return "Error: 找不到 skill-creator/scripts/ 目录，请确认 skill-creator 已安装"

    import subprocess
    result = subprocess.run(
        [sys.executable, str(scripts_dir / "init_skill.py"), skill_name, "--path", str(_CUSTOM_ROOT)],
        capture_output=True,
        text=True,
        cwd=str(scripts_dir),
    )

    output = result.stdout.strip()
    if result.returncode != 0:
        err = result.stderr.strip()
        return f"Error: 初始化失败\n{output}\n{err}"

    skill_dir = _CUSTOM_ROOT / skill_name
    return f"✅ Skill '{skill_name}' 初始化成功\n路径: {skill_dir}\n\n{output}"


class WriteSkillFileInput(BaseModel):
    path: str = Field(
        description=(
            "要写入的文件路径（绝对路径，或相对于 skills/custom/ 的路径）。"
            "只允许写入 skills/custom/ 目录内的文件。"
        )
    )
    content: str = Field(description="文件的完整内容")


@tool("write_skill_file", args_schema=WriteSkillFileInput)
def write_skill_file_tool(path: str, content: str) -> str:
    """
    在 skills/custom/ 下写入（或覆盖）文件。
    用于创建或编辑 SKILL.md、references/、scripts/ 等文件。
    路径必须在 skills/custom/ 目录内，不可写入 public/。
    """
    resolved = _safe_resolve_custom(path)
    if resolved is None:
        return f"Error: 路径不在允许范围内（必须在 skills/custom/ 内）: {path}"

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        logger.info(f"write_skill_file: 写入 {resolved}")
        return f"✅ 已写入: {resolved}"
    except Exception as e:
        return f"Error: 写入失败: {e}"


class DeleteSkillFileInput(BaseModel):
    path: str = Field(
        description="要删除的文件路径（绝对路径，或相对于 skills/custom/ 的路径）"
    )


@tool("delete_skill_file", args_schema=DeleteSkillFileInput)
def delete_skill_file_tool(path: str) -> str:
    """
    删除 skills/custom/ 下的指定文件。
    用于清理 init_skill 生成的不需要的示例文件（如 scripts/example.py）。
    只允许删除文件，不可删除目录。路径必须在 skills/custom/ 内。
    """
    resolved = _safe_resolve_custom(path)
    if resolved is None:
        return f"Error: 路径不在允许范围内（必须在 skills/custom/ 内）: {path}"
    if not resolved.exists():
        return f"Error: 文件不存在: {path}"
    if not resolved.is_file():
        return f"Error: 只能删除文件，不能删除目录: {path}"

    try:
        resolved.unlink()
        logger.info(f"delete_skill_file: 删除 {resolved}")
        return f"✅ 已删除: {resolved}"
    except Exception as e:
        return f"Error: 删除失败: {e}"
