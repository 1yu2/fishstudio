"""
Workspace Tools - Agent 主动写入工作空间记忆的工具
"""
import logging
from langchain_core.tools import tool
from app.services import workspace_service

logger = logging.getLogger(__name__)

# MEMORY.md 中合法的章节名
_VALID_SECTIONS = ["角色资产", "成功 Prompt 模板", "用户偏好记录"]


@tool
def write_memory(section: str, content: str) -> str:
    """
    将重要创作信息追加写入 MEMORY.md 的指定章节。
    用于记录用户满意的角色资产、效果好的 Prompt 模板、用户明确的风格偏好等长期记忆。

    Args:
        section: 写入哪个章节，必须是以下之一：
                 "角色资产" / "成功 Prompt 模板" / "用户偏好记录"
        content: 要追加到该章节的 Markdown 内容（一条或多条条目）

    Returns:
        操作结果说明
    """
    if section not in _VALID_SECTIONS:
        return f"❌ 无效的章节名称：'{section}'。有效章节：{', '.join(_VALID_SECTIONS)}"

    try:
        memory_content = workspace_service.load_workspace_file("MEMORY.md")
    except Exception as e:
        logger.error(f"读取 MEMORY.md 失败: {e}")
        return f"❌ 读取 MEMORY.md 失败：{e}"

    # 查找目标章节位置
    section_header = f"## {section}"
    if section_header not in memory_content:
        # 章节不存在则追加到末尾
        memory_content = memory_content.rstrip() + f"\n\n{section_header}\n{content.strip()}\n"
    else:
        # 在章节结尾追加内容（在下一个 ## 之前，或文件末尾）
        idx = memory_content.index(section_header)
        # 找到该章节结束位置（下一个 ## 开始处 或 文件末尾）
        next_section_idx = memory_content.find("\n## ", idx + len(section_header))
        if next_section_idx == -1:
            # 没有下一个章节，追加到末尾
            memory_content = memory_content.rstrip() + f"\n{content.strip()}\n"
        else:
            # 在下一章节前插入
            before = memory_content[:next_section_idx].rstrip()
            after = memory_content[next_section_idx:]
            memory_content = before + f"\n{content.strip()}\n" + after

    try:
        workspace_service.save_workspace_file("MEMORY.md", memory_content)
        logger.info(f"✅ 成功写入 MEMORY.md [{section}]")
        return f"✅ 已成功将内容写入 MEMORY.md 的「{section}」章节。"
    except Exception as e:
        logger.error(f"写入 MEMORY.md 失败: {e}")
        return f"❌ 写入 MEMORY.md 失败：{e}"
