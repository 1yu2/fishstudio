"""Kill a worker while it holds job leases, expire those leases, and check the other worker
finishes every job exactly once (mock providers only, no paid calls).

Lease TTL is 300s; instead of waiting, the dead worker's leases are moved into the past,
which is what recover_expired_leases would see once the TTL elapsed.
"""

import asyncio
import subprocess

import httpx

API = "http://127.0.0.1:18101"
JOBS = 30
WORKER = "fs-mr-worker-1"


def sql(query: str) -> str:
    cmd = ["docker", "exec", "fs-mr-pg-1", "psql", "-U", "postgres", "-d", "fishstudio", "-At", "-c", query]
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


async def main():
    host = subprocess.run(
        ["docker", "inspect", "-f", "{{.Config.Hostname}}", WORKER], capture_output=True, text=True
    ).stdout.strip()
    async with httpx.AsyncClient(timeout=30) as client:
        login = await client.post(
            f"{API}/api/auth/login", json={"username": "admin", "password": "mr-admin-pass"}
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        canvas = (
            await client.post(f"{API}/api/node-canvases", json={"title": "failover"}, headers=headers)
        ).json()
        nodes = [
            {"id": f"n{i}", "type": "image", "position": {"x": 0, "y": i}, "data": {"prompt": "p"}}
            for i in range(JOBS)
        ]
        graph = {"graph": {"nodes": nodes, "edges": []}, "revision": canvas["revision"]}
        r = await client.put(f"{API}/api/node-canvases/{canvas['id']}/graph", json=graph, headers=headers)
        r.raise_for_status()
        ids = []
        for i in range(JOBS):
            r = await client.post(
                f"{API}/api/node-canvases/{canvas['id']}/nodes/n{i}/run",
                json={"kind": "image", "prompt": f"p{i}"},
                headers=headers,
            )
            assert r.status_code == 202, r.text
            ids.append(r.json()["job_id"])

    in_jobs = ",".join(f"'{job_id}'" for job_id in ids)
    held_by_worker = f"id in ({in_jobs}) and leased_by like '{host}%' and status in ('leased','running')"
    held = "0"
    for _ in range(50):
        held = sql(f"select count(*) from generation_jobs where {held_by_worker}")
        if held and int(held) > 0:
            break
        await asyncio.sleep(0.1)
    subprocess.run(["docker", "kill", WORKER], capture_output=True)
    print(f"killed {WORKER} while it held {held} job(s)")
    await asyncio.sleep(2)
    print(
        "still leased by the dead worker:",
        sql(f"select count(*) from generation_jobs where {held_by_worker}"),
    )
    sql(f"update generation_jobs set lease_until = now() - interval '1 second' where {held_by_worker}")
    print("expired those leases (simulating the 300s TTL)")

    status = ""
    for _ in range(120):
        status = sql(f"select status, count(*) from generation_jobs where id in ({in_jobs}) group by 1")
        if status == f"succeeded|{JOBS}":
            break
        await asyncio.sleep(1)
    by_worker = sql(
        f"select split_part(leased_by, ':', 1), attempt, count(*) from generation_jobs "
        f"where id in ({in_jobs}) group by 1, 2"
    )
    per_job = sql(
        f"select n, count(*) from (select job_id, count(*) n from artifacts "
        f"where job_id in ({in_jobs}) group by job_id) t group by n"
    )
    print("final:", status.replace("\n", ", "))
    print("executed by (host|attempt|jobs):", by_worker.replace("\n", ", "))
    print("artifacts per job (n|jobs):", per_job)
    ok = status == f"succeeded|{JOBS}" and per_job == f"1|{JOBS}"
    print(f"RESULT failover={'PASS' if ok else 'FAIL'}")


asyncio.run(main())
