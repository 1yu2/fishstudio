"""
FishStudio 设置 API 路由
支持 Skills 配置、MCP 服务器配置、环境变量配置
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any
import re
import logging

from app.auth.dependencies import get_current_user, require_admin
from app.db.database import get_session
from app.db.models import AuditLog, Setting, User
from app.services import skill_service
from app.services import workspace_service

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])

# .env 文件路径（环境变量配置仍然写到 .env 文件，需重启生效）
BASE_DIR = Path(__file__).parent.parent.parent
ENV_FILE = BASE_DIR / ".env"

# 默认 skills 配置
DEFAULT_SKILLS = [
    {
        "id": "image_generation",
        "name": "图片生成",
        "description": "火山引擎文生图/图生图",
        "enabled": True,
        "category": "image",
    },
    {
        "id": "video_generation",
        "name": "视频生成",
        "description": "火山引擎文/图生视频",
        "enabled": True,
        "category": "video",
    },
    {
        "id": "video_concatenation",
        "name": "视频拼接",
        "description": "长视频分镜拼接工作流",
        "enabled": True,
        "category": "video",
    },
    {
        "id": "model_3d",
        "name": "3D 模型生成",
        "description": "腾讯混元文/图生3D",
        "enabled": True,
        "category": "3d",
    },
    {
        "id": "virtual_anchor",
        "name": "虚拟人生成",
        "description": "ComfyUI 口型同步视频",
        "enabled": True,
        "category": "avatar",
    },
    {
        "id": "tts",
        "name": "语音合成",
        "description": "Qwen-TTS 音色设计与克隆",
        "enabled": True,
        "category": "audio",
    },
    {
        "id": "audio_mixing",
        "name": "音频混音",
        "description": "音频拼接、BGM 选择与混音",
        "enabled": True,
        "category": "audio",
    },
    {
        "id": "xiaohongshu_post",
        "name": "小红书帖子",
        "description": "生成完整的小红书图文帖子，含标题、正文、话题标签",
        "enabled": True,
        "category": "content",
    },
]


# ─────────────────────────────────────────
# DB-backed settings KV helpers
# ─────────────────────────────────────────

async def _get_setting(session: AsyncSession, key: str, default: Any = None) -> Any:
    row = await session.get(Setting, key)
    return row.value if row is not None else default


async def _set_setting(session: AsyncSession, key: str, value: Any) -> None:
    row = await session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    await session.commit()


# ─────────────────────────────────────────
# Skills 配置
# ─────────────────────────────────────────

class SkillItem(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    category: str


class SkillsPayload(BaseModel):
    skills: list[SkillItem]


@router.get("/settings/skills")
async def get_skills(session: AsyncSession = Depends(get_session)):
    """返回 skills 配置列表，已保存的启用状态会覆盖默认值"""
    stored = await _get_setting(session, "skills", {}) or {}
    skills = []
    for s in DEFAULT_SKILLS:
        item = dict(s)
        if s["id"] in stored:
            item["enabled"] = stored[s["id"]]
        skills.append(item)
    return {"skills": skills}


@router.put("/settings/skills")
async def put_skills(payload: SkillsPayload, session: AsyncSession = Depends(get_session)):
    """保存 skills 启用状态"""
    await _set_setting(session, "skills", {s.id: s.enabled for s in payload.skills})
    return {"ok": True}


# ─────────────────────────────────────────
# Installed Skills（基于 SKILL.md 文件的 skills）
# ─────────────────────────────────────────

class InstalledSkillResponse(BaseModel):
    id: str
    name: str
    description: str
    source: str   # "public" | "custom"
    enabled: bool


class SkillToggleRequest(BaseModel):
    enabled: bool


@router.get("/settings/skills/installed")
async def get_installed_skills():
    """返回所有已安装的 skill（来自 skills/ 目录的 SKILL.md 文件）及其启用状态"""
    skills = skill_service.get_skills_with_state()
    return [
        InstalledSkillResponse(
            id=s.id,
            name=s.name,
            description=s.description,
            source=s.source,
            enabled=s.enabled,
        )
        for s in skills
    ]


@router.put("/settings/skills/installed/{skill_id}")
async def toggle_installed_skill(skill_id: str, payload: SkillToggleRequest):
    """启用或禁用指定的 installed skill"""
    # 验证 skill 存在
    skills = skill_service.get_skills_with_state()
    skill = next((s for s in skills if s.id == skill_id), None)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

    skill_service.set_skill_enabled(skill_id, payload.enabled)
    return InstalledSkillResponse(
        id=skill.id,
        name=skill.name,
        description=skill.description,
        source=skill.source,
        enabled=payload.enabled,
    )


@router.get("/settings/skills/installed/{skill_id}/content")
async def get_installed_skill_content(skill_id: str):
    """返回指定 installed skill 的 SKILL.md 原始文本"""
    skills = skill_service.get_skills_with_state()
    skill = next((s for s in skills if s.id == skill_id), None)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    try:
        content = skill.md_path.read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read SKILL.md: {e}")
    return {"id": skill_id, "content": content}


# ─────────────────────────────────────────
# MCP 服务器配置
# ─────────────────────────────────────────

@router.get("/settings/mcp")
async def get_mcp(session: AsyncSession = Depends(get_session)):
    """返回 MCP servers 配置"""
    return {"mcpServers": await _get_setting(session, "mcpServers", {}) or {}}


@router.put("/settings/mcp")
async def put_mcp(payload: dict[str, Any], session: AsyncSession = Depends(get_session)):
    """保存 MCP servers 配置"""
    await _set_setting(session, "mcpServers", payload.get("mcpServers", {}))
    return {"ok": True}


# ─────────────────────────────────────────
# 环境变量配置（敏感操作：仅 admin + 写审计）
# ─────────────────────────────────────────

# 需要脱敏显示的字段前缀
SENSITIVE_PATTERNS = ["KEY", "SECRET", "TOKEN", "PASSWORD"]

# 按分组定义关心的 env keys（含描述）
ENV_GROUPS = [
    {
        "group": "LLM 配置",
        "keys": [
            {"key": "OPENAI_API_KEY", "desc": "OpenAI 兼容接口 API Key（SiliconFlow / 火山引擎等）", "sensitive": True},
            {"key": "OPENAI_BASE_URL", "desc": "OpenAI 兼容接口 Base URL", "sensitive": False},
            {"key": "MODEL_NAME", "desc": "主模型名称", "sensitive": False},
            {"key": "RECURSION_LIMIT", "desc": "LangGraph 多步推理递归上限", "sensitive": False},
        ],
    },
    {
        "group": "图片生成",
        "keys": [
            {"key": "VOLCANO_API_KEY", "desc": "火山引擎 API Key", "sensitive": True},
            {"key": "VOLCANO_BASE_URL", "desc": "火山引擎 Base URL", "sensitive": False},
            {"key": "VOLCANO_IMAGE_MODEL", "desc": "图片生成模型", "sensitive": False},
            {"key": "VOLCANO_EDIT_MODEL", "desc": "图片编辑模型", "sensitive": False},
            {"key": "IMAGE_MODEL_NAME", "desc": "备用图片生成模型名称", "sensitive": False},
            {"key": "EDIT_IMAGE_MODEL_NAME", "desc": "备用图片编辑模型名称", "sensitive": False},
            {"key": "BASE_URL", "desc": "本地服务基础 URL（用于图片可访问地址拼接）", "sensitive": False},
        ],
    },
    {
        "group": "视频生成",
        "keys": [
            {"key": "VOLCANO_VIDEO_MODEL", "desc": "火山引擎视频生成模型", "sensitive": False},
            {"key": "VOLCANO_THINKING_ENABLED", "desc": "是否启用思考模式（true/false）", "sensitive": False},
        ],
    },
    {
        "group": "3D 模型生成",
        "keys": [
            {"key": "TENCENT_AI3D_API_KEY", "desc": "腾讯混元 3D API Key", "sensitive": True},
            {"key": "TENCENT_AI3D_BASE_URL", "desc": "腾讯混元 3D Base URL", "sensitive": False},
        ],
    },
    {
        "group": "语音合成 (TTS)",
        "keys": [
            {"key": "DASHSCOPE_API_KEY", "desc": "阿里云百炼 CosyVoice / Qwen API Key", "sensitive": True},
            {"key": "DASHSCOPE_BASE_URL", "desc": "DashScope Base URL", "sensitive": False},
        ],
    },
    {
        "group": "ComfyUI 虚拟人",
        "keys": [
            {"key": "COMFYUI_SERVER_ADDRESS", "desc": "ComfyUI 服务器地址", "sensitive": False},
            {"key": "COMFYUI_WORKFLOW_PATH", "desc": "ComfyUI 工作流 JSON 文件路径", "sensitive": False},
            {"key": "FACE_DETECTION_METHOD", "desc": "人脸检测方法（opencv / llm）", "sensitive": False},
        ],
    },
    {
        "group": "调试配置",
        "keys": [
            {"key": "MOCK_MODE", "desc": "Mock 模式（true 时不调用真实 API）", "sensitive": False},
            {"key": "MOCK_IMAGE_PATH", "desc": "Mock 图片路径", "sensitive": False},
            {"key": "MOCK_MODEL_PATH", "desc": "Mock 3D 模型路径", "sensitive": False},
            {"key": "MOCK_VIDEO_PATH", "desc": "Mock 视频路径", "sensitive": False},
            {"key": "LOG_LEVEL", "desc": "日志级别（DEBUG / INFO / WARNING / ERROR）", "sensitive": False},
        ],
    },
]


def _mask(value: str) -> str:
    """敏感值脱敏：保留前 3 + 后 3 字符。"""
    if not value:
        return ""
    if len(value) <= 8:
        return "***"
    return f"{value[:3]}***{value[-3:]}"


def _is_sensitive_key(key: str) -> bool:
    return any(p in key.upper() for p in SENSITIVE_PATTERNS)


def _parse_env_file() -> dict[str, str]:
    """解析 .env 文件，返回 {key: value} 字典"""
    result: dict[str, str] = {}
    if not ENV_FILE.exists():
        return result
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip()
    return result


def _write_env_key(key: str, value: str) -> None:
    """更新或追加 .env 文件中的指定 key"""
    if not ENV_FILE.exists():
        ENV_FILE.write_text(f"{key}={value}\n", encoding="utf-8")
        return
    content = ENV_FILE.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)
    pattern = re.compile(r"^" + re.escape(key) + r"\s*=")
    found = False
    new_lines = []
    for line in lines:
        if pattern.match(line):
            new_lines.append(f"{key}={value}\n")
            found = True
        else:
            new_lines.append(line)
    if not found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"{key}={value}\n")
    ENV_FILE.write_text("".join(new_lines), encoding="utf-8")


@router.get("/settings/env")
async def get_env(_admin: User = Depends(require_admin)):
    """返回环境变量配置（仅 admin；GET 不脱敏，因 admin 本来就有 .env 写权限）"""
    env_values = _parse_env_file()
    groups = []
    for group_def in ENV_GROUPS:
        items = []
        for kdef in group_def["keys"]:
            key = kdef["key"]
            raw_value = env_values.get(key, "")
            sensitive = kdef["sensitive"]
            items.append({
                "key": key,
                "value": raw_value,
                "desc": kdef["desc"],
                "sensitive": sensitive,
            })
        groups.append({"group": group_def["group"], "items": items})
    return {"groups": groups}


class EnvUpdateItem(BaseModel):
    key: str
    value: str


class EnvUpdatePayload(BaseModel):
    updates: list[EnvUpdateItem]


@router.put("/settings/env")
async def put_env(
    payload: EnvUpdatePayload,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """更新 .env 文件中的指定键值对（仅 admin；写审计日志）"""
    known_keys = {kdef["key"] for g in ENV_GROUPS for kdef in g["keys"]}
    current = _parse_env_file()
    for item in payload.updates:
        if item.key not in known_keys:
            raise HTTPException(status_code=400, detail=f"Unknown key: {item.key}")
        before_raw = current.get(item.key, "")
        sensitive = _is_sensitive_key(item.key)
        before_val = _mask(before_raw) if sensitive else before_raw
        after_val = _mask(item.value) if sensitive else item.value
        _write_env_key(item.key, item.value)
        session.add(
            AuditLog(
                user_id=admin.id,
                username=admin.username,
                action="settings.env.update",
                target=item.key,
                before={"value": before_val},
                after={"value": after_val},
            )
        )
    await session.commit()
    return {"ok": True, "requires_restart": True}


# ─────────────────────────────────────────
# Workspace 配置（USER.md / SOUL.md / MEMORY.md）
# ─────────────────────────────────────────

class WorkspaceFileResponse(BaseModel):
    id: str        # 文件名，如 "USER.md"
    name: str      # 显示名称
    desc: str      # 描述
    content: str   # 文件内容


class WorkspaceFileUpdatePayload(BaseModel):
    content: str


@router.get("/settings/workspace")
async def get_workspace_files():
    """返回所有工作空间文件列表及内容"""
    workspace_service.ensure_workspace_defaults()
    result = []
    for filename, (name, desc) in workspace_service.WORKSPACE_FILES.items():
        try:
            content = workspace_service.load_workspace_file(filename)
        except Exception as e:
            logger.warning(f"Failed to load workspace file {filename}: {e}")
            content = ""
        result.append(WorkspaceFileResponse(id=filename, name=name, desc=desc, content=content))
    return result


@router.get("/settings/workspace/{filename}")
async def get_workspace_file(filename: str):
    """返回指定工作空间文件内容"""
    if filename not in workspace_service.WORKSPACE_FILES:
        raise HTTPException(status_code=404, detail=f"Unknown workspace file: {filename}")
    workspace_service.ensure_workspace_defaults()
    try:
        content = workspace_service.load_workspace_file(filename)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read {filename}: {e}")
    name, desc = workspace_service.WORKSPACE_FILES[filename]
    return WorkspaceFileResponse(id=filename, name=name, desc=desc, content=content)


@router.put("/settings/workspace/{filename}")
async def put_workspace_file(filename: str, payload: WorkspaceFileUpdatePayload):
    """保存指定工作空间文件内容"""
    if filename not in workspace_service.WORKSPACE_FILES:
        raise HTTPException(status_code=404, detail=f"Unknown workspace file: {filename}")
    try:
        workspace_service.save_workspace_file(filename, payload.content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save {filename}: {e}")
    return {"ok": True}
