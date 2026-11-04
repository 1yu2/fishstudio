"""角色库：个人角色资产（AssetCard scope=personal、type=character）与画布角色节点之间的互转。

角色 = 画布里的角色图片节点（data.role == "character"，或兼容旧画布：出边全是 reference 的图片节点）。
库角色的 profile.looks = [{name, artifact_id}]；上传的库内图片 Artifact 没有项目（project_id 为空），
由 metadata.owner_user_id 标记归属；从项目存入库时 looks 直接引用项目里的图片 Artifact（读取时校验归属）。
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Artifact, AssetCard, GenerationJob, NodeCanvas, SeriesProject, User
from app.services import asset_service
from app.services import node_canvas_service as canvas_service

LIBRARY_UPLOAD_LIMIT = 10 * 1024 * 1024
DEFAULT_LOOK = "默认"
ROW = 380
COLUMN = 420
NAME_PREFIX = re.compile(r"^\s*角色\s*[·.、:：-]\s*")


class LibraryError(ValueError):
    """面向用户的错误，消息可直接展示（路由转 422）。"""


class UnsupportedImage(LibraryError):
    """上传的文件实际内容不是 PNG / JPEG / WebP（路由转 400）。"""


def sniff_image(data: bytes) -> Optional[tuple[str, str]]:
    """按文件头判断真实图片类型，返回 (mime, 扩展名)；不是支持的类型返回 None。不信任客户端声明的 MIME。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


# ── 画布里的角色识别（工作台步骤状态与角色库共用）──────────────────────────


def is_character_node(node: dict[str, Any], edges: list[dict[str, Any]]) -> bool:
    """role == character，或兼容旧画布：出边非空且全是 reference 的图片节点。"""
    if node.get("type") != "image":
        return False
    if (node.get("data") or {}).get("role") == "character":
        return True
    out = [edge for edge in edges if edge.get("source") == node.get("id")]
    return bool(out) and all(edge.get("targetHandle") == "reference" for edge in out)


def character_name(node: dict[str, Any]) -> str:
    """角色名：data.character，否则从 label “角色 · 狐妖”里取。"""
    data = node.get("data") or {}
    name = str(data.get("character") or "").strip()
    if name:
        return name
    label = str(data.get("label") or "").strip()
    return NAME_PREFIX.sub("", label).strip() or label


def is_base_look(node: dict[str, Any]) -> bool:
    look = str((node.get("data") or {}).get("look") or "").strip()
    return look in ("", DEFAULT_LOOK)


def character_groups(graph: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """角色名 → 该角色的造型节点（保持画布顺序）。"""
    edges = graph.get("edges") or []
    groups: dict[str, list[dict[str, Any]]] = {}
    for node in graph.get("nodes") or []:
        if is_character_node(node, edges):
            groups.setdefault(character_name(node), []).append(node)
    return groups


def base_node(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """基础造型节点：look 为空 / “默认”的第一个；没有就取第一个。"""
    return next((node for node in nodes if is_base_look(node)), nodes[0])


def character_stats(graph: dict[str, Any], node_results: dict[str, set[str]]) -> list[dict[str, Any]]:
    """每个角色是否已定稿：基础造型节点有 selected，且该 artifact 是这个节点的结果。"""
    stats = []
    for name, nodes in character_groups(graph).items():
        base = base_node(nodes)
        selected = (base.get("data") or {}).get("selected")
        stats.append(
            {"name": name, "final": bool(selected) and selected in node_results.get(base["id"], set())}
        )
    return stats


# ── 库角色的读取 ──────────────────────────────────────────────────────────


def root_id(card: AssetCard) -> str:
    """库角色的稳定身份：第一个版本的资产 id；老数据没有记录时就是自己的 id。"""
    return str((card.profile or {}).get("library_root_id") or card.id)


async def _stamp_root(session: AsyncSession, card: AssetCard) -> None:
    """新建的库角色把自己的 id 记为身份（之后的新版本沿用）。"""
    card.profile = {**(card.profile or {}), "library_root_id": card.id}
    await session.commit()
    await session.refresh(card)


def _look_entries(card: AssetCard) -> list[dict[str, Any]]:
    return [
        {"name": str(look.get("name") or DEFAULT_LOOK), "artifact_id": str(look["artifact_id"])}
        for look in (card.profile or {}).get("looks") or []
        if isinstance(look, dict) and look.get("artifact_id")
    ]


async def _usable_artifacts(session: AsyncSession, ids: set[str], user: User) -> dict[str, Artifact]:
    """库角色引用的图片里，当前用户确实有权使用的那些（自己的项目产物，或自己上传到库的图）。"""
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(Artifact, SeriesProject.user_id)
            .outerjoin(SeriesProject, SeriesProject.id == Artifact.project_id)
            .where(Artifact.id.in_(ids), Artifact.type == "image")
        )
    ).all()
    usable: dict[str, Artifact] = {}
    for artifact, project_owner in rows:
        meta = artifact.artifact_metadata or {}
        if artifact.project_id is not None:
            if project_owner == user.id:
                usable[artifact.id] = artifact
        elif meta.get("source") == "library_upload" and meta.get("owner_user_id") == user.id:
            usable[artifact.id] = artifact
    return usable


async def _serialize(
    session: AsyncSession, cards: list[AssetCard], user: User, in_project: set[str]
) -> list[dict[str, Any]]:
    wanted = {look["artifact_id"] for card in cards for look in _look_entries(card)}
    usable = await _usable_artifacts(session, wanted, user)
    items = []
    for card in cards:
        looks = [
            {**look, "url": asset_service.artifact_url(usable[look["artifact_id"]])}
            for look in _look_entries(card)
            if look["artifact_id"] in usable
        ]
        items.append(
            {
                "id": card.id,
                "root_id": root_id(card),
                "name": card.name,
                "description": card.description,
                "looks": looks,
                "cover_url": looks[0]["url"] if looks else None,
                "updated_at": card.created_at.isoformat() if card.created_at else None,
                "in_project": card.id in in_project or root_id(card) in in_project,
            }
        )
    return items


async def _active_cards(session: AsyncSession, user: User) -> list[AssetCard]:
    cards = await asset_service.list_assets(session, user_id=user.id, scope="personal", type="character")
    return [card for card in cards if card.status != "retired"]


async def list_library(
    session: AsyncSession, user: User, canvas: Optional[NodeCanvas] = None
) -> list[dict[str, Any]]:
    cards = await _active_cards(session, user)
    in_project: set[str] = set()
    if canvas is not None:
        in_project = {
            str((node.get("data") or {}).get("library_asset_id"))
            for node in (canvas.graph or {}).get("nodes", [])
            if (node.get("data") or {}).get("library_asset_id")
        }
    return await _serialize(session, cards, user, in_project)


async def _get_card(session: AsyncSession, asset_id: str, user: User) -> AssetCard:
    card = await asset_service.get_personal_asset_for_user(session, asset_id, user.id)
    if card is None or card.type != "character" or card.status == "retired":
        raise LookupError("角色不存在")
    return card


async def _one(session: AsyncSession, card: AssetCard, user: User) -> dict[str, Any]:
    return (await _serialize(session, [card], user, set()))[0]


# ── 上传 / 改名 / 删除 ────────────────────────────────────────────────────


async def upload_character(
    session: AsyncSession,
    user: User,
    *,
    name: str,
    description: str,
    filename: str,
    content_type: str,
    data: bytes,
) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise LibraryError("请填写角色名")
    if len(name) > 255:
        raise LibraryError("角色名太长")
    if not data or len(data) > LIBRARY_UPLOAD_LIMIT:
        raise LibraryError(f"文件为空或超过 {LIBRARY_UPLOAD_LIMIT // (1024 * 1024)}MB")
    sniffed = sniff_image(data)
    if sniffed is None:
        raise UnsupportedImage("只支持 PNG / JPG / WebP 图片")
    mime, suffix = sniffed
    relative = Path("uploads") / "library" / str(user.id) / f"{uuid.uuid4().hex}{suffix}"
    target = asset_service.STORAGE_DIR / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    artifact = await asset_service.create_artifact(
        session,
        project_id=None,
        type="image",
        storage_key=relative.as_posix(),
        mime_type=mime,
        meta={"source": "library_upload", "owner_user_id": user.id, "filename": filename[:200]},
    )
    card = await asset_service.create_asset_card(
        session,
        None,
        user.id,
        type="character",
        name=name,
        description=description.strip(),
        scope="personal",
        source="library_upload",
        profile={"looks": [{"name": DEFAULT_LOOK, "artifact_id": artifact.id}]},
    )
    await _stamp_root(session, card)
    return await _one(session, card, user)


async def update_character(
    session: AsyncSession, user: User, asset_id: str, *, name: Optional[str], description: Optional[str]
) -> dict[str, Any]:
    card = await _get_card(session, asset_id, user)
    if name is not None:
        if not name.strip():
            raise LibraryError("角色名不能为空")
        card.name = name.strip()[:255]
    if description is not None:
        card.description = description.strip()
    await session.commit()
    await session.refresh(card)
    return await _one(session, card, user)


async def delete_character(session: AsyncSession, user: User, asset_id: str) -> None:
    """只删资产卡；图片 Artifact 可能已被项目引用，保留。"""
    card = await _get_card(session, asset_id, user)
    await session.delete(card)
    await session.commit()


# ── 项目 ↔ 角色库 ────────────────────────────────────────────────────────


async def _project_image(
    session: AsyncSession, canvas: NodeCanvas, node_id: str, artifact_id: Any
) -> Optional[Artifact]:
    """定稿图必须是这个节点自己的结果：任务产物（job.params.canvas_node_id）或上传 / 库复制（metadata.canvas_node_id）。"""
    if not isinstance(artifact_id, str) or not artifact_id:
        return None
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None or artifact.project_id != canvas.project_id or artifact.type != "image":
        return None
    if artifact.job_id:
        job = await session.get(GenerationJob, artifact.job_id)
        owner = (job.params or {}).get("canvas_node_id") if job is not None else None
    else:
        owner = (artifact.artifact_metadata or {}).get("canvas_node_id")
    return artifact if owner == node_id else None


async def save_to_library(
    session: AsyncSession, user: User, canvas: NodeCanvas, character: str
) -> dict[str, Any]:
    nodes = character_groups(canvas.graph or {}).get(character.strip())
    if not nodes:
        raise LibraryError("画布里没有这个角色")
    looks: list[dict[str, str]] = []
    ordered = [base_node(nodes), *[node for node in nodes if node is not base_node(nodes)]]
    base_final = False
    for node in ordered:
        data = node.get("data") or {}
        artifact = await _project_image(session, canvas, node["id"], data.get("selected"))
        if artifact is not None:
            base_final = base_final or node is ordered[0]
            looks.append(
                {"name": str(data.get("look") or "").strip() or DEFAULT_LOOK, "artifact_id": artifact.id}
            )
    if not base_final:
        raise LibraryError("先给角色定稿")
    description = str((base_node(nodes).get("data") or {}).get("prompt") or "").strip()
    profile: dict[str, Any] = {"looks": looks, "source_canvas_id": canvas.id}
    existing = next(
        (
            card
            for card in await _active_cards(session, user)
            if card.name == character.strip() and (card.profile or {}).get("source_canvas_id") == canvas.id
        ),
        None,
    )
    if existing is not None:
        profile["library_root_id"] = root_id(existing)
        card = await asset_service.new_asset_version(
            session, existing.id, user.id, {"profile": profile, "description": description}
        )
    else:
        card = await asset_service.create_asset_card(
            session,
            None,
            user.id,
            type="character",
            name=character.strip(),
            description=description,
            scope="personal",
            source="project_save",
            profile=profile,
        )
        await _stamp_root(session, card)
    return await _one(session, card, user)


def _new_node_id() -> str:
    return f"image-{uuid.uuid4().hex[:8]}"


async def add_from_library(
    session: AsyncSession, user: User, canvas: NodeCanvas, asset_id: str, base_revision: int
) -> dict[str, Any]:
    """把库角色加入画布：每个造型在本项目里新建一条 Artifact 行 + 一个角色节点，带 revision 保存。"""
    card = await _get_card(session, asset_id, user)
    # 先锁画布行并重读，再比较版本、读图：并发的两次加入（或普通保存）只有一个能通过
    await session.refresh(canvas, with_for_update=True)
    if base_revision != canvas.revision:
        raise canvas_service.CanvasConflict("画布已在其他窗口更新，请刷新后再编辑")
    graph = dict(canvas.graph or {})
    nodes = list(graph.get("nodes") or [])
    identity = {card.id, root_id(card)}
    if any((node.get("data") or {}).get("library_asset_id") in identity for node in nodes):
        raise LibraryError("这个角色已在本项目")
    entries = _look_entries(card)
    usable = await _usable_artifacts(session, {look["artifact_id"] for look in entries}, user)
    entries = [look for look in entries if look["artifact_id"] in usable]
    if not entries:
        raise LibraryError("这个角色的图片已不可用")

    characters = [node for node in nodes if is_character_node(node, graph.get("edges") or [])]
    if characters:
        x = min(node["position"]["x"] for node in characters)
        y = max(node["position"]["y"] for node in characters) + ROW
    else:
        x = min((node["position"]["x"] for node in nodes), default=COLUMN) - COLUMN
        y = 0
    new_ids: list[str] = []
    for index, look in enumerate(entries):
        source = usable[look["artifact_id"]]
        node_id = _new_node_id()
        copy = Artifact(
            project_id=canvas.project_id,
            type="image",
            storage_key=source.storage_key,
            storage_backend=source.storage_backend,
            content_hash=source.content_hash,
            mime_type=source.mime_type,
            width=source.width,
            height=source.height,
            artifact_metadata={
                "source": "canvas_upload",
                "canvas_node_id": node_id,
                "library_asset_id": root_id(card),
            },
        )
        session.add(copy)
        await session.flush()
        is_default = look["name"] == DEFAULT_LOOK
        nodes.append(
            {
                "id": node_id,
                "type": "image",
                "position": {"x": x, "y": y + index * ROW},
                "data": {
                    "label": f"角色 · {card.name}" + ("" if is_default else f" · {look['name']}"),
                    "role": "character",
                    "character": card.name,
                    "look": "" if is_default else look["name"],
                    "prompt": card.description,
                    "model": "",
                    "count": 4,
                    "selected": copy.id,
                    "library_asset_id": root_id(card),
                },
            }
        )
        new_ids.append(node_id)
    await canvas_service.save_graph(session, canvas, {**graph, "nodes": nodes}, base_revision, user.id)
    return {"revision": canvas.revision, "node_ids": new_ids}
