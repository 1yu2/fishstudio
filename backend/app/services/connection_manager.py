"""
WebSocket 连接管理器
管理每个 canvas_id 的 WebSocket 订阅列表，支持广播事件给所有订阅者。

多进程部署（uvicorn --workers N / 多副本）时，连接只存在于某一个进程内。
设置 REDIS_URL 后，broadcast 发布到 Redis 频道，每个进程的 relay 订阅该频道再投递给本进程的连接；
未设置或 relay 尚未订阅成功时，退回进程内直接投递（单进程开发模式）。
"""

import asyncio
import json
import logging
import os
from typing import Dict, List, Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)

CHANNEL = "fishstudio:canvas-broadcast"
RELAY_RETRY_SECONDS = 2.0


class ConnectionManager:
    def __init__(self):
        # canvas_id -> list of WebSocket connections
        self.active_connections: Dict[str, List[WebSocket]] = {}
        self._redis = None
        self._relay_task: Optional[asyncio.Task] = None
        self._relay_ready = asyncio.Event()

    async def connect(self, canvas_id: str, websocket: WebSocket):
        """接受并注册 WebSocket 连接"""
        await websocket.accept()
        if canvas_id not in self.active_connections:
            self.active_connections[canvas_id] = []
        self.active_connections[canvas_id].append(websocket)
        logger.info(
            f"WebSocket connected for canvas_id={canvas_id}, total={len(self.active_connections[canvas_id])}"
        )

    def disconnect(self, canvas_id: str, websocket: WebSocket):
        """移除 WebSocket 连接"""
        if canvas_id in self.active_connections:
            try:
                self.active_connections[canvas_id].remove(websocket)
            except ValueError:
                pass
            if not self.active_connections[canvas_id]:
                del self.active_connections[canvas_id]
        logger.info(f"WebSocket disconnected for canvas_id={canvas_id}")

    async def broadcast(self, canvas_id: str, message: str):
        """广播消息给所有订阅该 canvas 的 WebSocket 客户端（跨进程时经 Redis 转发）"""
        if self._redis is not None and self._relay_ready.is_set():
            try:
                await self._redis.publish(
                    CHANNEL, json.dumps({"canvas_id": canvas_id, "message": message}, ensure_ascii=False)
                )
                return
            except Exception as e:
                logger.warning(f"Redis publish failed, delivering locally only: {e}")
        await self._deliver_local(canvas_id, message)

    async def _deliver_local(self, canvas_id: str, message: str):
        if canvas_id not in self.active_connections:
            return
        dead_connections = []
        for websocket in list(self.active_connections[canvas_id]):
            try:
                await websocket.send_text(message)
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket for canvas_id={canvas_id}: {e}")
                dead_connections.append(websocket)
        # 清理断开的连接
        for ws in dead_connections:
            self.disconnect(canvas_id, ws)

    async def broadcast_all(self, message: str):
        """广播消息给所有已连接的 WebSocket 客户端（不区分 canvas_id）"""
        for canvas_id in list(self.active_connections.keys()):
            await self.broadcast(canvas_id, message)

    async def start_relay(self, redis_url: Optional[str] = None) -> None:
        """REDIS_URL 存在时启动跨进程 relay；否则保持进程内广播。"""
        redis_url = (redis_url if redis_url is not None else os.getenv("REDIS_URL", "")).strip()
        if not redis_url or self._relay_task is not None:
            return
        from redis.asyncio import Redis

        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._relay_task = asyncio.create_task(self._relay_loop())

    async def stop_relay(self) -> None:
        if self._relay_task is not None:
            self._relay_task.cancel()
            try:
                await self._relay_task
            except asyncio.CancelledError:
                pass
            self._relay_task = None
        self._relay_ready.clear()
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def _relay_loop(self) -> None:
        while True:
            pubsub = self._redis.pubsub()
            try:
                await pubsub.subscribe(CHANNEL)
                self._relay_ready.set()
                logger.info("WebSocket broadcast relay subscribed to Redis")
                async for item in pubsub.listen():
                    if item.get("type") != "message":
                        continue
                    try:
                        data = json.loads(item["data"])
                        await self._deliver_local(data["canvas_id"], data["message"])
                    except Exception as e:
                        logger.warning(f"Dropped malformed broadcast relay message: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Redis 断开期间退回进程内投递，稍后重连
                logger.warning(f"WebSocket broadcast relay lost Redis, retrying: {e}")
            finally:
                self._relay_ready.clear()
                try:
                    await pubsub.aclose()
                except Exception:
                    pass
            await asyncio.sleep(RELAY_RETRY_SECONDS)


# 模块级单例
manager = ConnectionManager()
