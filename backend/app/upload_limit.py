"""上传请求体的 ASGI 级限制：必须在 multipart 解析（会把文件写进临时磁盘）之前生效。

- 未带 Authorization 头的上传请求直接 401，不读取请求体；
- Content-Length 超限直接 413；
- 没有 / 伪造 Content-Length（chunked）时，在 receive() 里累计字节，超限立即中断并返回 413。
业务层（upload_guard / 各路由）里的大小检查仍保留，作为第二道防线。
"""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable

MB = 1024 * 1024
_OVERHEAD = MB  # multipart 边界、表单字段的额外开销

# (路径正则, 文件上限字节) —— 只作用于 POST
UPLOAD_LIMITS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"^/api/upload-image$"), 15 * MB),
    (re.compile(r"^/api/upload-audio$"), 20 * MB),
    (re.compile(r"^/api/upload-video$"), 200 * MB),
    (re.compile(r"^/api/series/projects/[^/]+/artifacts/upload-reference-video$"), 200 * MB),
    (re.compile(r"^/api/series/projects/[^/]+/artifacts/upload-audio$"), 20 * MB),
    (re.compile(r"^/api/node-canvases/[^/]+/uploads$"), 200 * MB),
    (re.compile(r"^/api/node-canvases/[^/]+/bgm$"), 20 * MB),
    (re.compile(r"^/api/library/characters$"), 10 * MB),
]


def limit_for(path: str) -> int | None:
    for pattern, limit in UPLOAD_LIMITS:
        if pattern.match(path):
            return limit + _OVERHEAD
    return None


async def _reply(send, status: int, detail: str, extra: list[tuple[bytes, bytes]] | None = None) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": headers + (extra or [])})
    await send({"type": "http.response.body", "body": body})


class UploadLimitMiddleware:
    def __init__(self, app: Callable[..., Awaitable[None]]):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        limit = limit_for(scope["path"])
        if limit is None:
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        if not headers.get(b"authorization", b"").strip():
            await _reply(send, 401, "Missing token", [(b"www-authenticate", b"Bearer")])
            return
        declared = headers.get(b"content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            await _reply(send, 413, "上传内容过大")
            return

        received = 0
        exceeded = False
        started = False

        async def counting_receive():
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    # 假装客户端断开，让 multipart 解析立刻停止，不再继续落盘
                    return {"type": "http.disconnect"}
            return message

        async def tracking_send(message):
            nonlocal started
            if exceeded:
                # 超限后应用多半已把「断开」翻译成 400 / 500：丢弃它的响应，统一回 413
                if not started:
                    started = True
                    await _reply(send, 413, "上传内容过大")
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except Exception:
            if exceeded and not started:
                await _reply(send, 413, "上传内容过大")
                return
            raise
        if exceeded and not started:
            await _reply(send, 413, "上传内容过大")
