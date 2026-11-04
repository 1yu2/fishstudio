"""画布批量运行：服务端输入解析、幂等、依赖推进、批量报价与预算（真实 PG 部分需要 TEST_DATABASE_URL）。"""

import asyncio
import json
import logging
from decimal import Decimal
from pathlib import Path

import pytest
from test_node_canvas import _node, _user, pg, requires_pg  # noqa: F401  (pg 是 fixture)

from app.services import canvas_batch_service as batches
from app.services import node_canvas_service as service

FIXTURE = Path(__file__).parent / "fixtures" / "node_canvas_resolve_cases.json"


def _edge(source, target, handle):
    return {"id": f"{source}-{target}-{handle}", "source": source, "target": target, "targetHandle": handle}


# ── 纯逻辑 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", json.loads(FIXTURE.read_text())["cases"], ids=lambda case: case["name"])
def test_resolve_node_request_matches_shared_fixture(case):
    graph = {
        "nodes": [{"position": {"x": 0, "y": 0}, **node} for node in case["nodes"]],
        "edges": case["edges"],
    }
    request, blockers = service.resolve_node_request(
        graph, case["node_id"], case["results"], case["aspect_ratio"]
    )
    assert request == case["expected"]["request"]
    assert blockers == case["expected"]["blockers"]


def test_request_fingerprint_ignores_client_request_id_and_prompt_padding():
    a = service.NodeRunRequest(kind="image", prompt="x ", client_request_id="one")
    b = service.NodeRunRequest(kind="image", prompt=" x", client_request_id="two")
    c = service.NodeRunRequest(kind="image", prompt="x", count=2)
    assert service.request_fingerprint(a) == service.request_fingerprint(b)
    assert service.request_fingerprint(a) != service.request_fingerprint(c)


def _graph(nodes, edges):
    return {"nodes": nodes, "edges": edges}


def test_plan_orders_dependencies_and_skips_only_fresh_results():
    graph = _graph(
        [_node("v", "video"), _node("b", "image"), _node("a", "image"), _node("t"), _node("w", "video")],
        [_edge("a", "b", "reference"), _edge("b", "v", "first_frame"), _edge("t", "w", "prompt")],
    )
    order, skipped = batches.plan_batch(graph, batches.BatchRequest(), {})
    assert order.index("a") < order.index("b") < order.index("v") and "t" not in order and skipped == set()

    # a 已有结果被跳过，但 b 没有结果，所以 v 即使有旧结果也要重跑
    order, skipped = batches.plan_batch(
        graph, batches.BatchRequest(), {"a": ["x"], "v": ["old"], "w": ["w1"]}
    )
    assert skipped == {"a", "w"}

    order, skipped = batches.plan_batch(
        graph, batches.BatchRequest(scope="downstream", node_id="a"), {"a": ["x"], "b": ["y"], "w": ["w1"]}
    )
    assert order == ["a", "b", "v"] and skipped == set()  # 起点重跑，下游结果过期


def test_plan_rejects_cycles_and_unknown_root():
    graph = _graph(
        [_node("a", "image"), _node("b", "image")],
        [_edge("a", "b", "reference"), _edge("b", "a", "reference")],
    )
    with pytest.raises(service.CanvasError, match="循环"):
        batches.plan_batch(graph, batches.BatchRequest(), {})
    with pytest.raises(service.CanvasError, match="节点不存在"):
        batches.plan_batch(
            _graph([_node("a", "image")], []), batches.BatchRequest(scope="downstream", node_id="z"), {}
        )


def test_plan_kinds_limits_members_without_blocking_other_kind():
    graph = _graph(
        [_node("a", "image"), _node("v", "video"), _node("w", "video"), _node("t")],
        [_edge("a", "v", "first_frame"), _edge("t", "w", "prompt")],
    )
    order, _ = batches.plan_batch(graph, batches.BatchRequest(kinds=["image"]), {})
    assert order == ["a"]
    order, _ = batches.plan_batch(graph, batches.BatchRequest(kinds=["video"]), {})
    assert sorted(order) == ["v", "w"]  # 上游图片不在批次里，不产生依赖
    order, _ = batches.plan_batch(graph, batches.BatchRequest(kinds=["image", "video"]), {})
    assert sorted(order) == ["a", "v", "w"]
    # 与 downstream 组合：从 a 往下游只取视频
    order, _ = batches.plan_batch(
        graph, batches.BatchRequest(scope="downstream", node_id="a", kinds=["video"]), {}
    )
    assert order == ["v"]
    order, _ = batches.plan_batch(
        graph, batches.BatchRequest(scope="downstream", node_id="a", kinds=["image"]), {"a": ["x"]}
    )
    assert order == ["a"]
    with pytest.raises(service.CanvasError, match="没有可运行"):
        batches.plan_batch(_graph([_node("a", "image")], []), batches.BatchRequest(kinds=["video"]), {})


def test_idempotency_key_is_unambiguous_per_id_and_kinds():
    def key(client_id, kinds=None):
        return batches._idempotency_key(batches.BatchRunRequest(client_request_id=client_id, kinds=kinds))

    assert key("same") == key("same", ["image", "video"])  # 覆盖全部类型 = 不限
    assert len({key("same"), key("same", ["image"]), key("same", ["video"])}) == 3
    # 旧的 "id|kind" 拼接会让这两个撞上
    assert key("abc", ["image"]) != key("abc|image")
    assert all(len(key(c, k)) <= 64 for c in ("x" * 64, "a") for k in (None, ["video"]))
    legacy = batches._lookup_keys(batches.BatchRunRequest(client_request_id="abc"))
    assert legacy == [key("abc"), "abc"]
    assert batches._lookup_keys(batches.BatchRunRequest(client_request_id="abc", kinds=["image"])) == [
        key("abc", ["image"])
    ]
    # v2: 开头的原始 id 不做旧值查找，旧值查找撞不上任何新 key
    assert len(batches._lookup_keys(batches.BatchRunRequest(client_request_id=key("abc")))) == 1


# ── 真实 PostgreSQL ──────────────────────────────────────────────────────


def _plan(cost="0.72", provider="ark"):
    async def fake_plan(session, **kwargs):
        return {
            "provider": provider,
            "model": "seedance",
            "resolution": "480p",
            "protocol": "ark_v3",
            "billable_duration": kwargs["duration"],
            "estimated_total_cost": Decimal(cost),
            "price_snapshot": {},
            "deployment_name": "ark-seedance",
            "route": {"tier": kwargs["tier"]},
        }

    return fake_plan


async def _succeed(s, canvas, job, count=1):
    from app.db.models import Artifact

    job.status = "succeeded"
    for index in range(count):
        s.add(
            Artifact(
                project_id=canvas.project_id,
                job_id=job.id,
                type="image",
                storage_key=f"img/{job.id}-{index}.png",
            )
        )
    await s.flush()


@requires_pg
def test_same_client_request_id_reuses_job_and_reserves_budget_once(pg, monkeypatch):
    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _plan())
    calls = []
    real_reserve = service.budget_service.reserve

    async def spy(session, user_id, estimate):
        calls.append(estimate)
        return await real_reserve(session, user_id, estimate)

    monkeypatch.setattr(service.budget_service, "reserve", spy)
    req = service.NodeRunRequest(kind="video", prompt="推门", client_request_id="click-1")

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await service.create_canvas(s, owner, title="t")
            await service.save_graph(s, canvas, {"nodes": [_node("vid", "video")], "edges": []}, revision=1)
            first = await service.run_node(s, canvas, "vid", req)
            second = await service.run_node(s, canvas, "vid", req)
            other = await service.run_node(
                s, canvas, "vid", req.model_copy(update={"client_request_id": "click-2"})
            )
            legacy_a = await service.run_node(
                s, canvas, "vid", req.model_copy(update={"client_request_id": None})
            )
            legacy_b = await service.run_node(
                s, canvas, "vid", req.model_copy(update={"client_request_id": None})
            )
            await s.commit()
            return first.id, second.id, other.id, legacy_a.id, legacy_b.id

    first, second, other, legacy_a, legacy_b = asyncio.run(_run())

    assert first == second and len({first, other, legacy_a, legacy_b}) == 4
    assert len(calls) == 4  # 重复的那次没有预留


@requires_pg
def test_concurrent_duplicate_runs_create_one_job(pg, monkeypatch):
    from sqlalchemy import func, select

    from app.db.models import GenerationJob

    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _plan())
    calls = []
    real_reserve = service.budget_service.reserve

    async def spy(session, user_id, estimate):
        calls.append(estimate)
        return await real_reserve(session, user_id, estimate)

    monkeypatch.setattr(service.budget_service, "reserve", spy)
    req = service.NodeRunRequest(kind="video", prompt="推门", client_request_id="click-1")

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await service.create_canvas(s, owner, title="t")
            await service.save_graph(s, canvas, {"nodes": [_node("vid", "video")], "edges": []}, revision=1)
            await s.commit()
            canvas_id, owner_id = canvas.id, owner.id

        async def one():
            async with pg() as session:
                from app.db.models import User

                user = await session.get(User, owner_id)
                current = await service.get_canvas(session, canvas_id, user)
                job = await service.run_node(session, current, "vid", req)
                await session.commit()
                return job.id

        ids = await asyncio.gather(one(), one(), one())
        async with pg() as session:
            count = await session.scalar(select(func.count(GenerationJob.id)))
        return ids, count

    ids, count = asyncio.run(_run())

    assert len(set(ids)) == 1 and count == 1
    assert len(calls) == 1


async def _batch_canvas(s, owner, videos=10):
    """10 个 “文本 → 视频” + 2 个 “图片 → 视频”。"""
    canvas = await service.create_canvas(s, owner, title="batch")
    nodes, edges = [], []
    for index in range(videos):
        nodes += [_node(f"t{index}", "text", text=f"镜头{index}"), _node(f"v{index}", "video")]
        edges.append(_edge(f"t{index}", f"v{index}", "prompt"))
    for name in ("a", "b"):
        nodes += [
            _node(f"img-{name}", "image", prompt=f"关键帧{name}"),
            _node(f"vid-{name}", "video", prompt="动起来"),
        ]
        edges.append(_edge(f"img-{name}", f"vid-{name}", "first_frame"))
    await service.save_graph(s, canvas, {"nodes": nodes, "edges": edges}, revision=1)
    return canvas


@requires_pg
def test_batch_runs_images_before_their_videos_without_duplicates(pg, monkeypatch, caplog):
    from sqlalchemy import select

    from app.db.models import GenerationJob

    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _plan(provider="local_h3"))

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await _batch_canvas(s, owner)
            request = batches.BatchRunRequest(client_request_id="b1")
            batch = await batches.create_batch(s, canvas, request)
            again = await batches.create_batch(s, canvas, request)  # 重复提交：同一批次
            first_wave = {n: dict(v) for n, v in batch.nodes.items()}
            jobs = (await s.execute(select(GenerationJob))).scalars().all()
            wave1 = len(jobs)
            by_node = {job.params["canvas_node_id"]: job for job in jobs}
            for name in ("a", "b"):
                await _succeed(s, canvas, by_node[f"img-{name}"])
            await batches.advance_batch(s, batch.id)
            await batches.advance_batch(s, batch.id)  # 再推一次不应重复提交
            jobs = (await s.execute(select(GenerationJob))).scalars().all()
            by_node = {job.params["canvas_node_id"]: job for job in jobs}
            for job in jobs:
                if job.status != "succeeded":
                    await _succeed(s, canvas, job, count=0)
            await batches.advance_batch(s, batch.id)
            return batch, again, first_wave, wave1, jobs, by_node

    logging.getLogger(batches.__name__).disabled = False  # alembic 的 fileConfig 会停用已存在的 logger
    with caplog.at_level(logging.INFO, logger="app.services.canvas_batch_service"):
        batch, again, first_wave, wave1, jobs, by_node = asyncio.run(_run())

    assert again.id == batch.id
    assert wave1 == 12  # 10 个文本视频 + 2 个图片先跑；2 个依赖首帧的视频还在等
    assert first_wave["vid-a"]["status"] == "waiting" and first_wave["img-a"]["status"] == "running"
    assert len(jobs) == 14 and len({job.idempotency_key for job in jobs}) == 14
    assert all(job.params["batch_run_id"] == batch.id for job in jobs)
    assert by_node["vid-a"].params["first_frame"].startswith("img/")
    assert batch.status == "succeeded" and batch.node_ids.index("img-a") < batch.node_ids.index("vid-a")
    finished = [r for r in caplog.records if r.getMessage().startswith("canvas_batch_finished")]
    assert len(finished) == 1 and finished[0].canvas_batch["node_count"] == 14
    assert finished[0].canvas_batch["succeeded"] == 14


@requires_pg
def test_staged_batches_images_first_then_videos(pg, monkeypatch):
    from sqlalchemy import select

    from app.db.models import GenerationJob

    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _plan(provider="local_h3"))

    async def _jobs(s):
        return {
            job.params["canvas_node_id"]: job
            for job in (await s.execute(select(GenerationJob))).scalars().all()
        }

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await _batch_canvas(s, owner, videos=1)
            images = batches.BatchRunRequest(client_request_id="same", kinds=["image"])
            quote = await batches.batch_quote(s, canvas, images)
            image_batch = await batches.create_batch(s, canvas, images)
            only_images = await _jobs(s)
            # 同一个 client_request_id、不同 kinds：是另一个批次；相同 kinds 仍幂等
            again = await batches.create_batch(s, canvas, images)
            video_request = batches.BatchRunRequest(client_request_id="same", kinds=["video"])
            jobs = await _jobs(s)
            await _succeed(s, canvas, jobs["img-a"])
            jobs["img-b"].status = "failed"  # img-b 没有任何结果
            await batches.advance_batch(s, image_batch.id)
            video_quote = await batches.batch_quote(s, canvas, video_request)
            video_batch = await batches.create_batch(s, canvas, video_request)
            after = await _jobs(s)
            await s.refresh(image_batch)
            await s.refresh(video_batch)
            image_detail = batches.batch_detail(image_batch)
            video_detail = batches.batch_detail(video_batch)
            return (
                quote,
                image_detail,
                set(only_images),
                again.id,
                video_quote,
                video_detail,
                {k: j.params.get("first_frame") for k, j in after.items()},
            )

    quote, image_detail, only_images, again_id, video_quote, detail, after = asyncio.run(_run())

    assert only_images == {"img-a", "img-b"}  # 下游视频不进批次，也没有被标 blocked
    assert [n["kind"] for n in quote["nodes"]] == ["image", "image"]
    assert set(image_detail["nodes"]) == {"img-a", "img-b"} and image_detail["kinds"] == ["image"]
    assert again_id == image_detail["id"] and detail["id"] != image_detail["id"]
    assert detail["kinds"] == ["video"] and set(detail["node_ids"]) == {"v0", "vid-a", "vid-b"}
    # 上游图片没有结果的视频：报价不可用且不是 0 元；批次里 blocked，其余视频照常提交
    unavailable = {item["node_id"]: item for item in video_quote["unavailable_nodes"]}
    assert set(unavailable) == {"vid-b"} and "还没有图片" in unavailable["vid-b"]["reason"]
    assert detail["nodes"]["vid-b"]["status"] == "blocked"
    assert {"v0", "vid-a"} <= set(after) and "vid-b" not in after
    assert after["vid-a"].startswith("img/")
    assert detail["nodes"]["vid-a"]["status"] == "running" and detail["status"] == "running"


@requires_pg
def test_full_batch_detail_has_no_kinds(pg, monkeypatch):
    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _plan(provider="local_h3"))

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await _batch_canvas(s, owner, videos=1)
            batch = await batches.create_batch(s, canvas, batches.BatchRunRequest(client_request_id="all"))
            await s.refresh(batch)
            return batches.batch_detail(batch)

    detail = asyncio.run(_run())
    assert detail["kinds"] is None and detail["client_request_id"].startswith("v2:")


@requires_pg
def test_kind_batch_key_cannot_collide_and_legacy_rows_are_found(pg, monkeypatch):
    from sqlalchemy import select, update

    from app.db.models import CanvasBatchRun

    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _plan(provider="local_h3"))

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await _batch_canvas(s, owner, videos=1)
            image = await batches.create_batch(
                s, canvas, batches.BatchRunRequest(client_request_id="abc", kinds=["image"])
            )
            image_id = image.id
            # 原始 id 恰好是旧拼接格式：必须是另一个批次
            full = await batches.create_batch(
                s, canvas, batches.BatchRunRequest(client_request_id="abc|image")
            )
            full_id = full.id
            # 模拟改动前创建的批次：存的是原始 id
            await s.execute(
                update(CanvasBatchRun)
                .where(CanvasBatchRun.id == full_id)
                .values(client_request_id="legacy-1")
            )
            await s.flush()
            again = await batches.create_batch(
                s, canvas, batches.BatchRunRequest(client_request_id="legacy-1")
            )
            count = len((await s.execute(select(CanvasBatchRun))).scalars().all())
            return image_id, full_id, again.id, count

    image_id, full_id, again_id, count = asyncio.run(_run())
    assert image_id != full_id
    assert again_id == full_id and count == 2


@requires_pg
def test_kinds_batch_with_other_blockers_is_still_rejected(pg, monkeypatch):
    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _plan(provider="local_h3"))

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await service.create_canvas(s, owner, title="t")
            graph = {
                "nodes": [_node("img", "image", prompt="x"), _node("vid", "video", prompt="动")],
                "edges": [_edge("img", "vid", "last_frame")],  # 尾帧缺图 + 没有首帧
            }
            await service.save_graph(s, canvas, graph, revision=1)
            request = batches.BatchRunRequest(client_request_id="r1", kinds=["video"])
            quote = await batches.batch_quote(s, canvas, request)
            with pytest.raises(service.CanvasError, match="首帧"):
                await batches.create_batch(s, canvas, request)
            return quote

    quote = asyncio.run(_run())
    assert len(quote["nodes"][0]["blockers"]) == 2 and not quote["nodes"][0]["upstream_missing_only"]


@requires_pg
def test_upstream_failure_blocks_downstream_but_other_branches_continue(pg, monkeypatch):
    from sqlalchemy import select

    from app.db.models import GenerationJob

    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _plan(provider="local_h3"))

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await _batch_canvas(s, owner, videos=1)
            batch = await batches.create_batch(s, canvas, batches.BatchRunRequest(client_request_id="b1"))
            jobs = {
                job.params["canvas_node_id"]: job
                for job in (await s.execute(select(GenerationJob))).scalars().all()
            }
            jobs["img-a"].status = "failed"
            jobs["img-a"].error = "provider timeout"
            await _succeed(s, canvas, jobs["img-b"])
            await batches.advance_batch(s, batch.id)
            mid = batch.status, dict(batch.nodes)
            jobs = {
                job.params["canvas_node_id"]: job
                for job in (await s.execute(select(GenerationJob))).scalars().all()
            }
            for name in ("vid-b", "v0"):
                await _succeed(s, canvas, jobs[name], count=0)
            await batches.advance_batch(s, batch.id)
            return mid, batch, set(jobs)

    mid, batch, submitted = asyncio.run(_run())

    assert (
        mid[0] == "running"
        and mid[1]["vid-a"]["status"] == "blocked"
        and mid[1]["vid-b"]["status"] == "running"
    )
    assert "vid-a" not in submitted
    assert batch.status == "failed"
    assert batch.nodes["img-a"]["status"] == "failed" and "timeout" in batch.nodes["img-a"]["reason"]
    assert batch.nodes["vid-b"]["status"] == "succeeded"


@requires_pg
def test_over_budget_batch_is_rejected_without_creating_jobs(pg, monkeypatch):
    from sqlalchemy import func, select

    from app.db.models import CanvasBatchRun, GenerationJob

    monkeypatch.setenv("DAILY_BUDGET_CNY", "1")
    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _plan("0.72"))

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await service.create_canvas(s, owner, title="t")
            await service.save_graph(
                s,
                canvas,
                {"nodes": [_node("v1", "video", prompt="a"), _node("v2", "video", prompt="b")], "edges": []},
                revision=1,
            )
            quote = await batches.batch_quote(s, canvas, batches.BatchRequest())
            with pytest.raises(service.CanvasError, match=r"1\.44.*0\.00|超出今日剩余额度"):
                await batches.create_batch(s, canvas, batches.BatchRunRequest(client_request_id="b1"))
            return (
                quote,
                await s.scalar(select(func.count(GenerationJob.id))),
                await s.scalar(select(func.count(CanvasBatchRun.id))),
            )

    quote, jobs, rows = asyncio.run(_run())

    assert quote["exceeds_budget"] is True and quote["budget"] == {"cap": 1.0, "remaining": 1.0}
    assert jobs == 0 and rows == 0


@requires_pg
def test_batch_quote_equals_sum_of_node_quotes_and_flags_unknown_prices(pg, monkeypatch):
    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _plan("0.33"))

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await service.create_canvas(s, owner, title="t")
            await service.save_graph(
                s,
                canvas,
                {
                    "nodes": [
                        _node("k", "image", prompt="关键帧"),  # 没有默认图片模型 -> 价格未知
                        _node("v1", "video", prompt="a", duration=4),
                        _node("v2", "video", prompt="b", duration=6, tier="final"),
                        _node("v3", "video"),  # 首帧来自还没生成的 k
                    ],
                    "edges": [_edge("k", "v3", "first_frame")],
                },
                revision=1,
            )
            quote = await batches.batch_quote(s, canvas, batches.BatchRequest())
            singles = [
                await service.quote_node(
                    s, canvas, service.NodeRunRequest(kind="video", prompt="a", duration=4)
                ),
                await service.quote_node(
                    s, canvas, service.NodeRunRequest(kind="video", prompt="b", duration=6, tier="final")
                ),
            ]
            return quote, singles

    quote, singles = asyncio.run(_run())

    by_id = {item["node_id"]: item for item in quote["nodes"]}
    assert by_id["k"]["unavailable"] is True and by_id["k"]["estimated_cost"] is None
    assert [item["node_id"] for item in quote["unavailable_nodes"]] == ["k"]
    assert by_id["v3"]["estimated_cost"] == 0.33  # 以占位首帧估价
    expected = sum((Decimal(str(single["estimated_cost"])) for single in singles), Decimal(0)) + Decimal(
        "0.33"
    )
    assert Decimal(str(quote["estimated_total"])) == expected


@requires_pg
def test_cycle_and_blockers_are_rejected(pg, monkeypatch):
    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _plan())

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            cyc = await service.create_canvas(s, owner, title="c")
            await service.save_graph(
                s,
                cyc,
                {
                    "nodes": [_node("a", "image", prompt="x"), _node("b", "image", prompt="y")],
                    "edges": [_edge("a", "b", "reference"), _edge("b", "a", "reference")],
                },
                revision=1,
            )
            with pytest.raises(service.CanvasError, match="循环"):
                await batches.create_batch(s, cyc, batches.BatchRunRequest(client_request_id="b1"))
            empty = await service.create_canvas(s, owner, title="e")
            await service.save_graph(s, empty, {"nodes": [_node("v", "video")], "edges": []}, revision=1)
            with pytest.raises(service.CanvasError, match="无法运行"):
                await batches.create_batch(s, empty, batches.BatchRunRequest(client_request_id="b2"))

    asyncio.run(_run())


@requires_pg
def test_job_finalize_hook_and_cancel_advance_the_batch(pg, monkeypatch):
    from sqlalchemy import select

    from app.db.models import GenerationJob

    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _plan(provider="local_h3"))

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await _batch_canvas(s, owner, videos=0)
            batch = await batches.create_batch(s, canvas, batches.BatchRunRequest(client_request_id="b1"))
            jobs = {
                job.params["canvas_node_id"]: job
                for job in (await s.execute(select(GenerationJob))).scalars().all()
            }
            await _succeed(s, canvas, jobs["img-a"])
            await batches.advance_for_job(s, jobs["img-a"])  # worker 终态钩子
            submitted_after_hook = "vid-a" in {
                job.params["canvas_node_id"]
                for job in (await s.execute(select(GenerationJob))).scalars().all()
            }
            running = await batches.cancel_batch(s, batch.id)
            await batches.advance_batch(s, batch.id)
            return submitted_after_hook, running, batch

    submitted, running, batch = asyncio.run(_run())

    assert submitted
    assert sorted(running) and batch.status == "canceled"
    assert batch.nodes["vid-b"]["status"] == "canceled" and batch.nodes["img-a"]["status"] == "succeeded"


@requires_pg
def test_cancel_rereads_batch_under_lock(pg, monkeypatch):
    """路由层已加载过批次时，取消不能用缓存的旧节点状态覆盖 worker 刚提交的节点。"""
    from app.db.models import CanvasBatchRun

    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _plan(provider="local_h3"))

    async def _run():
        async with pg() as s1:
            owner = await _user(s1)
            canvas = await _batch_canvas(s1, owner, videos=0)
            batch = await batches.create_batch(s1, canvas, batches.BatchRunRequest(client_request_id="b1"))
            await s1.commit()
            assert batch.nodes["vid-a"]["status"] == "waiting"  # s1 里缓存的是旧状态
            async with pg() as s2:  # 另一个进程（worker）把它提交了
                other = await s2.get(CanvasBatchRun, batch.id)
                nodes = dict(other.nodes)
                nodes["vid-a"] = {"status": "running", "job_id": "job-x", "reason": ""}
                other.nodes = nodes
                await s2.commit()
            running = await batches.cancel_batch(s1, batch.id)
            return running, batch.nodes["vid-a"]

    running, state = asyncio.run(_run())

    assert "job-x" in running and state["job_id"] == "job-x"


@requires_pg
def test_batch_advances_from_creation_snapshot(pg, monkeypatch):
    """运行中编辑画布（加环、改提示词）不影响已报价的批次。"""
    from sqlalchemy import select

    from app.db.models import GenerationJob

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await service.create_canvas(s, owner, title="snap")
            graph = _graph(
                [
                    _node("a", "image", prompt="甲"),
                    _node("b", "image", prompt="乙"),
                    _node("c", "image", prompt="丙"),
                ],
                [_edge("a", "b", "reference"), _edge("b", "c", "reference")],
            )
            await service.save_graph(s, canvas, graph, revision=1)
            batch = await batches.create_batch(s, canvas, batches.BatchRunRequest(client_request_id="b1"))
            # 用户在批次运行中加了 c -> b（成环）并改了 b 的提示词
            edited = _graph(
                [
                    _node("a", "image", prompt="甲"),
                    _node("b", "image", prompt="改过的乙"),
                    _node("c", "image", prompt="丙"),
                ],
                [_edge("a", "b", "reference"), _edge("b", "c", "reference"), _edge("c", "b", "reference")],
            )
            await service.save_graph(s, canvas, edited, revision=canvas.revision)
            for node in ("a", "b", "c"):
                jobs = {
                    j.params["canvas_node_id"]: j for j in (await s.execute(select(GenerationJob))).scalars()
                }
                if node in jobs:
                    await _succeed(s, canvas, jobs[node])
                await batches.advance_batch(s, batch.id)
            jobs = {j.params["canvas_node_id"]: j for j in (await s.execute(select(GenerationJob))).scalars()}
            for job in jobs.values():
                if job.status != "succeeded":
                    await _succeed(s, canvas, job)
            await batches.advance_batch(s, batch.id)
            return batch, jobs

    batch, jobs = asyncio.run(_run())

    assert set(jobs) == {"a", "b", "c"} and batch.status == "succeeded"
    assert jobs["b"].params["prompt"] == "乙"  # 用报价时的提示词


@requires_pg
def test_concurrent_batches_cannot_both_pass_the_budget_check(pg, monkeypatch):
    from sqlalchemy import func, select

    from app.db.models import CanvasBatchRun, GenerationJob, User

    monkeypatch.setenv("DAILY_BUDGET_CNY", "10")
    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _plan("8"))

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await service.create_canvas(s, owner, title="t")
            await service.save_graph(
                s, canvas, {"nodes": [_node("v", "video", prompt="a")], "edges": []}, revision=1
            )
            await s.commit()
            canvas_id, owner_id = canvas.id, owner.id

        async def one(name):
            async with pg() as session:
                user = await session.get(User, owner_id)
                current = await service.get_canvas(session, canvas_id, user)
                try:
                    batch = await batches.create_batch(
                        session, current, batches.BatchRunRequest(client_request_id=name)
                    )
                    await session.commit()
                    return batch.id
                except service.CanvasError as error:
                    await session.rollback()
                    return error

        results = await asyncio.gather(one("x"), one("y"))
        async with pg() as session:
            return (
                results,
                await session.scalar(select(func.count(GenerationJob.id))),
                await session.scalar(select(func.count(CanvasBatchRun.id))),
            )

    results, jobs, rows = asyncio.run(_run())

    rejected = [r for r in results if isinstance(r, service.CanvasError)]
    assert len(rejected) == 1 and "超出今日剩余额度" in str(rejected[0])
    assert jobs == 1 and rows == 1  # 被拒绝的那个没有创建任何任务


@requires_pg
def test_quoted_revision_mismatch_is_rejected(pg, monkeypatch):
    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await service.create_canvas(s, owner, title="t")
            await service.save_graph(
                s, canvas, {"nodes": [_node("i", "image", prompt="x")], "edges": []}, revision=1
            )
            quote = await batches.batch_quote(s, canvas, batches.BatchRequest())
            await service.save_graph(
                s,
                canvas,
                {"nodes": [_node("i", "image", prompt="改")], "edges": []},
                revision=canvas.revision,
            )
            with pytest.raises(batches.BatchRevisionConflict):
                await batches.create_batch(
                    s,
                    canvas,
                    batches.BatchRunRequest(client_request_id="b1", quoted_revision=quote["revision"]),
                )
            return quote["revision"], canvas.revision

    quoted, current = asyncio.run(_run())
    assert quoted == 2 and current == 3


# ── 路由（TestClient + 真实 PG）──────────────────────────────────────────


@pytest.fixture
def api(pg):
    from fastapi.testclient import TestClient

    from app.db.database import get_session
    from app.main import app

    async def _session():
        async with pg() as session:
            yield session

    app.dependency_overrides[get_session] = _session
    with TestClient(app) as client:
        yield client, pg
    app.dependency_overrides.pop(get_session, None)


async def _seed_canvas(pg, graph, name="c"):
    async with pg() as s:
        owner = await _user(s, f"owner-{name}")
        canvas = await service.create_canvas(s, owner, title=name)
        await service.save_graph(s, canvas, graph, revision=1)
        await s.commit()
        return canvas.id, canvas.revision


@requires_pg
def test_batch_routes_reject_cycles_blockers_and_stale_revisions(api):
    client, pg = api
    cyc_id, _ = asyncio.run(
        _seed_canvas(
            pg,
            _graph(
                [_node("a", "image", prompt="x"), _node("b", "image", prompt="y")],
                [_edge("a", "b", "reference"), _edge("b", "a", "reference")],
            ),
            "cyc",
        )
    )
    blocked_id, _ = asyncio.run(_seed_canvas(pg, _graph([_node("v", "video")], []), "blocked"))
    ok_id, revision = asyncio.run(_seed_canvas(pg, _graph([_node("i", "image", prompt="x")], []), "ok"))

    cyc = client.post(f"/api/node-canvases/{cyc_id}/batch-runs", json={"client_request_id": "r1"})
    blocked = client.post(f"/api/node-canvases/{blocked_id}/batch-runs", json={"client_request_id": "r1"})
    stale = client.post(
        f"/api/node-canvases/{ok_id}/batch-runs",
        json={"client_request_id": "r1", "quoted_revision": revision - 1},
    )
    quote = client.post(f"/api/node-canvases/{ok_id}/batch-quote", json={}).json()
    ok = client.post(
        f"/api/node-canvases/{ok_id}/batch-runs",
        json={"client_request_id": "r1", "quoted_revision": quote["revision"]},
    )

    assert cyc.status_code == 400 and "循环" in cyc.json()["detail"]
    assert blocked.status_code == 400 and "无法运行" in blocked.json()["detail"]
    assert stale.status_code == 409 and "重新报价" in stale.json()["detail"]
    assert quote["revision"] == revision
    assert ok.status_code == 202 and ok.json()["total"] == 1


@requires_pg
def test_batch_of_another_canvas_is_404(api):
    client, pg = api
    first, _ = asyncio.run(_seed_canvas(pg, _graph([_node("i", "image", prompt="x")], []), "one"))
    second, _ = asyncio.run(_seed_canvas(pg, _graph([_node("i", "image", prompt="y")], []), "two"))
    batch = client.post(f"/api/node-canvases/{first}/batch-runs", json={"client_request_id": "r1"}).json()

    own = client.get(f"/api/node-canvases/{first}/batch-runs/{batch['id']}")
    foreign_get = client.get(f"/api/node-canvases/{second}/batch-runs/{batch['id']}")
    foreign_cancel = client.post(f"/api/node-canvases/{second}/batch-runs/{batch['id']}/cancel")

    assert own.status_code == 200
    assert foreign_get.status_code == 404 and foreign_cancel.status_code == 404
