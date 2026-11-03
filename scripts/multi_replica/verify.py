"""0.1 multi-replica check against the fs-mr stack (mock providers only, no paid calls)."""

import asyncio
import json
import uuid

import httpx
import websockets

APIS = {"api1": "http://127.0.0.1:18101", "api2": "http://127.0.0.1:18102"}
WS_PER_API = 6  # several connections per replica so both uvicorn processes in each container get some
JOBS = 16


async def login(client: httpx.AsyncClient, base: str) -> str:
    r = await client.post(f"{base}/api/auth/login", json={"username": "admin", "password": "mr-admin-pass"})
    r.raise_for_status()
    return r.json()["access_token"]


async def collect(ws, sink: list, stop: asyncio.Event):
    while not stop.is_set():
        try:
            sink.append(json.loads(await asyncio.wait_for(ws.recv(), 0.2)))
        except asyncio.TimeoutError:
            continue
        except websockets.ConnectionClosed:
            return


async def check_broadcast(client, token) -> bool:
    headers = {"Authorization": f"Bearer {token}"}
    canvas_id = f"mr-{uuid.uuid4().hex[:8]}"
    r = await client.post(
        f"{APIS['api1']}/api/canvases", json={"id": canvas_id, "name": "mr"}, headers=headers
    )
    r.raise_for_status()

    sockets, sinks = [], []
    for name, base in APIS.items():
        for _ in range(WS_PER_API):
            ws = await websockets.connect(f"{base.replace('http', 'ws')}/ws/{canvas_id}?token={token}")
            sockets.append((name, ws))
            sinks.append([])
    stop = asyncio.Event()
    tasks = [asyncio.create_task(collect(ws, sink, stop)) for (_, ws), sink in zip(sockets, sinks)]

    ok = True
    for sender in APIS:
        marker = f"hello-from-{sender}-{uuid.uuid4().hex[:6]}"
        async with client.stream(
            "POST",
            f"{APIS[sender]}/api/chat",
            json={"message": marker, "canvas_id": canvas_id},
            headers=headers,
            timeout=60,
        ) as resp:
            async for _ in resp.aiter_lines():
                pass
        await asyncio.sleep(1.0)
        got = [
            any(m.get("type") == "user_message" and m.get("content") == marker for m in sink)
            for sink in sinks
        ]
        per_api = {name: sum(g for (n, _), g in zip(sockets, got) if n == name) for name in APIS}
        print(f"  chat on {sender}: subscribers that saw it {per_api} (expect {WS_PER_API} each)")
        ok &= all(got)
        # every subscriber should get each message exactly once
        dupes = [sum(m.get("content") == marker for m in sink) for sink in sinks]
        if any(d > 1 for d in dupes):
            print(f"  duplicate deliveries: {dupes}")
            ok = False
    stop.set()
    await asyncio.gather(*tasks)
    for _, ws in sockets:
        await ws.close()
    return ok


async def check_jobs(client, token) -> bool:
    headers = {"Authorization": f"Bearer {token}"}
    r = await client.post(f"{APIS['api1']}/api/node-canvases", json={"title": "mr"}, headers=headers)
    r.raise_for_status()
    canvas = r.json()
    nodes = [
        {"id": f"img-{i}", "type": "image", "position": {"x": 0, "y": i * 10}, "data": {"prompt": f"p{i}"}}
        for i in range(JOBS)
    ]
    r = await client.put(
        f"{APIS['api2']}/api/node-canvases/{canvas['id']}/graph",
        json={"graph": {"nodes": nodes, "edges": []}, "revision": canvas["revision"]},
        headers=headers,
    )
    r.raise_for_status()

    async def run(i):
        base = APIS["api1"] if i % 2 == 0 else APIS["api2"]
        r = await client.post(
            f"{base}/api/node-canvases/{canvas['id']}/nodes/img-{i}/run",
            json={"kind": "image", "prompt": f"p{i}"},
            headers=headers,
        )
        r.raise_for_status()
        return r.json()["job_id"]

    job_ids = await asyncio.gather(*(run(i) for i in range(JOBS)))
    statuses = {}
    for _ in range(90):
        for jid in job_ids:
            base = APIS["api2"] if len(statuses) % 2 else APIS["api1"]
            statuses[jid] = (await client.get(f"{base}/api/jobs/{jid}", headers=headers)).json().get("status")
        if all(s in {"succeeded", "failed", "canceled"} for s in statuses.values()):
            break
        await asyncio.sleep(1)
    counts = {}
    for s in statuses.values():
        counts[s] = counts.get(s, 0) + 1
    print(f"  {JOBS} jobs submitted alternately to api1/api2 -> {counts}")
    return counts.get("succeeded", 0) == JOBS


async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        token = await login(client, APIS["api1"])
        # token issued by api1 must work on api2 (shared JWT secret, stateless auth)
        me = await client.get(f"{APIS['api2']}/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        print(f"[auth] api1 token on api2 -> {me.status_code}")
        print("[broadcast] chat on one replica reaches WebSocket subscribers on both")
        b_ok = await check_broadcast(client, token)
        print("[jobs] jobs created on either replica are executed by the worker pool")
        j_ok = await check_jobs(client, token)
    print(
        f"RESULT broadcast={'PASS' if b_ok else 'FAIL'} jobs={'PASS' if j_ok else 'FAIL'} auth={me.status_code}"
    )


asyncio.run(main())
