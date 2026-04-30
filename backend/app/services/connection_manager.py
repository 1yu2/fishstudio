"""
WebSocket 连接管理器
管理每个 canvas_id 的 WebSocket 订阅列表，支持广播事件给所有订阅者
"""
from fastapi import WebSocket
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        # canvas_id -> list of WebSocket connections
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, canvas_id: str, websocket: WebSocket):
        """接受并注册 WebSocket 连接"""
        await websocket.accept()
        if canvas_id not in self.active_connections:
            self.active_connections[canvas_id] = []
        self.active_connections[canvas_id].append(websocket)
        logger.info(f"WebSocket connected for canvas_id={canvas_id}, total={len(self.active_connections[canvas_id])}")

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
        """广播消息给所有订阅该 canvas 的 WebSocket 客户端"""
        if canvas_id not in self.active_connections:
            return
        dead_connections = []
        for websocket in self.active_connections[canvas_id]:
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


# 模块级单例
manager = ConnectionManager()
