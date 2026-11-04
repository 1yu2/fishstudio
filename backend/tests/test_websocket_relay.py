"""跨进程 WebSocket 广播：两个 ConnectionManager 模拟两个 uvicorn 进程，经 Redis 转发。"""

import asyncio

import pytest

from app.services.connection_manager import ConnectionManager


class FakeBroker:
    def __init__(self):
        self.queues: list[asyncio.Queue] = []
        self.fail_publish = False


class FakePubSub:
    def __init__(self, broker: FakeBroker):
        self.broker = broker
        self.queue: asyncio.Queue = asyncio.Queue()

    async def subscribe(self, channel):
        self.broker.queues.append(self.queue)

    async def listen(self):
        while True:
            yield await self.queue.get()

    async def aclose(self):
        if self.queue in self.broker.queues:
            self.broker.queues.remove(self.queue)


class FakeRedis:
    def __init__(self, broker: FakeBroker):
        self.broker = broker

    def pubsub(self):
        return FakePubSub(self.broker)

    async def publish(self, channel, data):
        if self.broker.fail_publish:
            raise ConnectionError("redis down")
        for queue in self.broker.queues:
            queue.put_nowait({"type": "message", "data": data})

    async def aclose(self):
        pass


class FakeWebSocket:
    def __init__(self):
        self.sent: list[str] = []

    async def accept(self):
        pass

    async def send_text(self, message):
        self.sent.append(message)


@pytest.fixture
def broker(monkeypatch):
    broker = FakeBroker()
    monkeypatch.setattr("redis.asyncio.Redis.from_url", lambda *a, **k: FakeRedis(broker))
    return broker


async def _wait_until(predicate, timeout=1.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


async def test_broadcast_reaches_socket_on_other_process(broker):
    process_a, process_b = ConnectionManager(), ConnectionManager()
    await process_a.start_relay("redis://fake")
    await process_b.start_relay("redis://fake")
    try:
        await _wait_until(lambda: process_a._relay_ready.is_set() and process_b._relay_ready.is_set())
        ws_a, ws_b, ws_other = FakeWebSocket(), FakeWebSocket(), FakeWebSocket()
        await process_a.connect("canvas-1", ws_a)
        await process_b.connect("canvas-1", ws_b)
        await process_b.connect("canvas-2", ws_other)

        # 聊天请求落在进程 A，订阅者分别连在 A 和 B
        await process_a.broadcast("canvas-1", '{"type":"delta"}')

        await _wait_until(lambda: ws_a.sent and ws_b.sent)
        assert ws_a.sent == ['{"type":"delta"}']
        assert ws_b.sent == ['{"type":"delta"}']
        assert ws_other.sent == []
    finally:
        await process_a.stop_relay()
        await process_b.stop_relay()


async def test_publish_failure_falls_back_to_local_delivery(broker):
    manager = ConnectionManager()
    await manager.start_relay("redis://fake")
    try:
        await _wait_until(manager._relay_ready.is_set)
        ws = FakeWebSocket()
        await manager.connect("canvas-1", ws)
        broker.fail_publish = True

        await manager.broadcast("canvas-1", "hello")

        assert ws.sent == ["hello"]
    finally:
        await manager.stop_relay()


async def test_without_redis_url_delivers_in_process(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    manager = ConnectionManager()
    await manager.start_relay()
    ws = FakeWebSocket()
    await manager.connect("canvas-1", ws)

    await manager.broadcast("canvas-1", "hello")

    assert ws.sent == ["hello"]
    assert manager._relay_task is None
