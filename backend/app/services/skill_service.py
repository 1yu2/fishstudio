"""
FishStudio Skill Service
扫描 skills/ 目录，解析 SKILL.md frontmatter，管理启用状态，向 agent 提供 skills context。
"""
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# ─── 路径配置 ───────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent.parent  # backend/
SKILLS_DIR = BASE_DIR / "skills"
STORAGE_DIR = BASE_DIR / "storage"
SETTINGS_FILE = STORAGE_DIR / "settings.json"


# ─── 数据结构 ──────────────────────────────────────────────────────────
@dataclass
class SkillMeta:
    id: str           # 目录名，如 "xiaohongshu-copywriter"
    name: str         # frontmatter name 字段
    description: str  # frontmatter description 字段
    source: str       # "public" | "custom"
    skill_dir: Path
    md_path: Path


@dataclass
class SkillWithState(SkillMeta):
    enabled: bool = False


# ─── Frontmatter 解析 ─────────────────────────────────────────────────
_FM_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


def _parse_skill_md(md_path: Path) -> Optional[tuple[dict, str]]:
    """
    解析 SKILL.md，返回 (frontmatter_dict, body_str)。
    解析失败时返回 None。
    """
    try:
        content = md_path.read_text(encoding="utf-8")
        m = _FM_PATTERN.match(content)
        if not m:
            logger.warning(f"SKILL.md missing frontmatter, skipping: {md_path}")
            return None
        fm_raw, body = m.group(1), m.group(2)
        fm = yaml.safe_load(fm_raw)
        if not isinstance(fm, dict):
            logger.warning(f"SKILL.md frontmatter is not a dict, skipping: {md_path}")
            return None
        return fm, body
    except Exception as e:
        logger.warning(f"Failed to parse SKILL.md {md_path}: {e}")
        return None


# ─── 扫描 ──────────────────────────────────────────────────────────────
def scan_available_skills() -> list[SkillMeta]:
    """扫描 skills/public/ 和 skills/custom/ 下的所有 SKILL.md，返回解析成功的 SkillMeta 列表。"""
    skills: list[SkillMeta] = []

    for source in ("public", "custom"):
        source_dir = SKILLS_DIR / source
        if not source_dir.exists():
            continue
        for skill_dir in sorted(source_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            md_path = skill_dir / "SKILL.md"
            if not md_path.exists():
                continue
            parsed = _parse_skill_md(md_path)
            if parsed is None:
                continue
            fm, _ = parsed
            name = fm.get("name", skill_dir.name)
            description = fm.get("description", "")
            skills.append(SkillMeta(
                id=skill_dir.name,
                name=name,
                description=description,
                source=source,
                skill_dir=skill_dir,
                md_path=md_path,
            ))

    return skills


# ─── Settings 读写 ─────────────────────────────────────────────────────
def _load_settings() -> dict:
    """读取 settings.json，不存在时返回空 dict。"""
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load settings.json: {e}")
    return {}


def _save_settings_atomic(data: dict) -> None:
    """原子写入 settings.json（os.replace 保证原子性）。"""
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=STORAGE_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, SETTINGS_FILE)
    except Exception:
        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ─── 公开 API ──────────────────────────────────────────────────────────
def get_skills_with_state() -> list[SkillWithState]:
    """返回所有可用 skill 及其启用状态。

    public/ 下的 skill 默认始终启用（无需手动安装）。
    custom/ 下的 skill 从 settings.json 的 installedSkills key 读取启用状态。
    """
    available = scan_available_skills()
    installed: dict[str, bool] = _load_settings().get("installedSkills", {})

    result: list[SkillWithState] = []
    for meta in available:
        # public skills 始终启用；custom skills 按 settings 决定
        if meta.source == "public":
            enabled = True
        else:
            enabled = installed.get(meta.id, False)
        result.append(SkillWithState(
            id=meta.id,
            name=meta.name,
            description=meta.description,
            source=meta.source,
            skill_dir=meta.skill_dir,
            md_path=meta.md_path,
            enabled=enabled,
        ))
    return result


def set_skill_enabled(skill_id: str, enabled: bool) -> None:
    """
    将指定 skill 的启用状态写入 settings.json 的 installedSkills key。
    使用 os.replace 原子写，防止 JSON 损坏。
    """
    data = _load_settings()
    installed: dict = data.get("installedSkills", {})
    installed[skill_id] = enabled
    data["installedSkills"] = installed
    _save_settings_atomic(data)


def get_skills_context() -> str:
    """
    构建注入 system prompt 的 skill 元数据块（Progressive Loading 模式）。

    只注入 name + description + SKILL.md 文件路径，不读取文件内容。
    Agent 在判断用户意图匹配某个 skill 后，自行调用 read_skill_file 工具按需加载。
    无启用 skill 时返回 ""。
    """
    skills = get_skills_with_state()
    enabled_skills = [s for s in skills if s.enabled]
    if not enabled_skills:
        return ""

    items = []
    for s in enabled_skills:
        items.append(
            f"  <skill>\n"
            f"    <name>{s.name}</name>\n"
            f"    <description>{s.description}</description>\n"
            f"    <location>{s.md_path}</location>\n"
            f"  </skill>"
        )
    skills_block = "<available_skills>\n" + "\n".join(items) + "\n</available_skills>"

    return f"""<skill_system>
你可以访问以下专项 Skill，每个 Skill 提供特定领域的优化工作流和专业知识。

**Progressive Loading 使用规则：**
1. 仔细对比用户意图与每个 skill 的 description，只有高度匹配时才加载对应 skill；
2. 确认匹配后，先用简短中文向用户说明你打算加载哪个 skill 及原因；
3. 然后调用 read_skill_file 工具读取该 skill 的 <location> 路径（即 SKILL.md 文件）；
4. 仔细阅读 skill 内容，按其中定义的工作流拆解任务、逐步执行；
5. 如需更多资源，可调用 list_skill_dir 查看 skill 目录，再用 read_skill_file 加载 references/、scripts/ 等子资源；
6. 若无任何 skill 与用户需求明确匹配，直接正常回答，严禁强行关联或模糊匹配。

{skills_block}
</skill_system>"""
