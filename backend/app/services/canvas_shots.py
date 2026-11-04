"""画布镜头口径：从画布 graph 推导镜头列表（与前端 storyboard/shots.ts 的 buildShots 一致）。

- 每个视频节点是一个镜头，它的 first_frame 上游图片是画面（帧）节点。
- 没有连到任何视频的图片节点也是一个“只有画面”的镜头，但不包括角色图
  （data.role == "character"，或出边非空且全是 reference 的旧画布图片）。
- 顺序：主节点 data.order 升序；没有 order 的按画布位置 y、再 x 排在后面。
- 配音：audio 节点通过 targetHandle == "voice" 的边连到镜头主节点，镜头内按 data.order，否则按 y 坐标。
"""

from __future__ import annotations

import re
from typing import Any, Optional

DEFAULT_DURATION_S = 5
NUMBER_PREFIX = re.compile(r"^\s*\d+\s*[·.、:：-]\s*")
FRAME_SUFFIX = re.compile(r"\s*关键帧$")


def _order(node: dict[str, Any]) -> Optional[float]:
    value = (node.get("data") or {}).get("order")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _pos(node: dict[str, Any]) -> tuple[float, float]:
    position = node.get("position") or {}
    return float(position.get("y") or 0), float(position.get("x") or 0)


def _sort_key(node: dict[str, Any]) -> tuple:
    order = _order(node)
    y, x = _pos(node)
    return (0 if order is not None else 1, order if order is not None else 0, y, x)


def _title(node: dict[str, Any], fallback: str) -> str:
    text = NUMBER_PREFIX.sub("", str((node.get("data") or {}).get("label") or "").strip())
    if node.get("type") == "image":
        text = FRAME_SUFFIX.sub("", text)
    return text.strip() or fallback


def _duration_s(node: dict[str, Any]) -> int:
    value = (node.get("data") or {}).get("duration")
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return DEFAULT_DURATION_S
    return seconds or DEFAULT_DURATION_S


def canvas_shots(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """画布 graph → 镜头列表：[{index, main_id, video_id, frame_id, title, duration_s, audio_ids}]。"""
    nodes = (graph or {}).get("nodes", [])
    edges = (graph or {}).get("edges", [])
    by_id = {node["id"]: node for node in nodes}
    to_video: set[str] = set()
    outgoing: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        outgoing.setdefault(edge.get("source"), []).append(edge)
        if by_id.get(edge.get("target"), {}).get("type") == "video":
            to_video.add(edge.get("source"))

    mains: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("type") == "video":
            first = next(
                (
                    edge
                    for edge in edges
                    if edge.get("target") == node["id"] and edge.get("targetHandle") == "first_frame"
                ),
                None,
            )
            frame = by_id.get(first.get("source")) if first else None
            mains.append(
                {
                    "main": node,
                    "video": node,
                    "frame": frame if frame and frame.get("type") == "image" else None,
                }
            )
        elif node.get("type") == "image" and node["id"] not in to_video:
            if (node.get("data") or {}).get("role") == "character":
                continue
            out = outgoing.get(node["id"], [])
            if out and all(edge.get("targetHandle") == "reference" for edge in out):
                continue
            mains.append({"main": node, "video": None, "frame": node})
    mains.sort(key=lambda item: _sort_key(item["main"]))

    shots: list[dict[str, Any]] = []
    for index, item in enumerate(mains):
        main = item["main"]
        voices = [
            by_id[edge["source"]]
            for edge in edges
            if edge.get("target") == main["id"]
            and edge.get("targetHandle") == "voice"
            and by_id.get(edge.get("source"), {}).get("type") == "audio"
        ]
        audio_ids: list[str] = []
        for node in sorted(voices, key=_sort_key):
            if node["id"] not in audio_ids:
                audio_ids.append(node["id"])
        shots.append(
            {
                "index": index,
                "main_id": main["id"],
                "video_id": item["video"]["id"] if item["video"] else None,
                "frame_id": item["frame"]["id"] if item["frame"] else None,
                "title": _title(main, f"镜头 {index + 1}"),
                "duration_s": _duration_s(main),
                "audio_ids": audio_ids,
            }
        )
    return shots
