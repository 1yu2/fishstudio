"""画布视频节点的角色参考图（R2.2）：参考图与首帧互斥、路由到支持参考图的部署、幂等、批量运行与镜头口径。

不调用任何真实厂商：路由用 monkeypatch 的 resolve_video_plan 或装配的假 Catalog + 真实 ArkVideoProvider 的能力判断。
需要真实 PG 的用例在没有 TEST_DATABASE_URL 时跳过。
"""

import asyncio
from datetime import datetime, timezone
from types import MappingProxyType, SimpleNamespace

import pytest
from pydantic import ValidationError
from test_canvas_batch import _edge, _plan
from test_node_canvas import _node, _user, pg, requires_pg  # noqa: F401  (pg 是 fixture)

from app.model_runtime import video as video_module
from app.model_runtime.catalog import CatalogDeployment, CatalogSnapshot
from app.providers.ark_video import ArkVideoProvider
from app.providers.base import VideoGenerationRequest
from app.services import canvas_batch_service as batches
from app.services import node_canvas_service as service
from app.services.canvas_shots import canvas_shots


def _recording_plan(calls):
    inner = _plan()

    async def fake_plan(session, **kwargs):
        calls.append(kwargs)
        return await inner(session, **kwargs)

    return fake_plan


async def _canvas_with_images(s, names=("a", "b", "c")):
    """一张画布：若干已有上传结果的角色图节点 + 一个视频节点。返回 (canvas, {节点名: artifact})。"""
    owner = await _user(s)
    canvas = await service.create_canvas(s, owner, title="t")
    nodes = [_node(name, "image", role="character", character=name) for name in names]
    nodes += [_node("vid", "video", prompt="三人对视"), _node("frame", "image")]
    await service.save_graph(s, canvas, {"nodes": nodes, "edges": []}, revision=1)
    artifacts = {}
    for name in (*names, "frame"):
        artifacts[name] = await service.upload(
            s, canvas, node_id=name, filename=f"{name}.png", content_type="image/png", data=b"png"
        )
    return canvas, artifacts


def _video_request(**kwargs):
    return service.NodeRunRequest(kind="video", prompt="三人对视", duration=5, **kwargs)


# ── 请求与路由 ───────────────────────────────────────────────────────────


def test_reference_ids_are_capped_at_nine():
    service.NodeRunRequest(kind="video", prompt="x", reference_artifact_ids=[f"a{i}" for i in range(9)])
    with pytest.raises(ValidationError):
        service.NodeRunRequest(kind="video", prompt="x", reference_artifact_ids=[f"a{i}" for i in range(10)])


@requires_pg
def test_reference_request_creates_job_with_reference_mode_and_lineage(pg, monkeypatch):
    from app.db.models import Artifact
    from app.services import lineage_service

    calls = []
    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _recording_plan(calls))

    async def _run():
        async with pg() as s:
            canvas, art = await _canvas_with_images(s)
            ids = [art["a"].id, art["b"].id]
            job = await service.run_node(s, canvas, "vid", _video_request(reference_artifact_ids=ids))
            quote = await service.quote_node(s, canvas, _video_request(reference_artifact_ids=ids))
            # 视频产物的血缘里能查到参考图
            video = Artifact(
                project_id=canvas.project_id, job_id=job.id, type="video", storage_key="v/out.mp4"
            )
            s.add(video)
            await s.flush()
            await lineage_service.attach_lineage(video, job)
            return job, art, quote, video

    job, art, quote, video = asyncio.run(_run())

    assert job.params["mode"] == "reference"
    assert job.params["reference_images"] == [art["a"].storage_key, art["b"].storage_key]
    assert "first_frame" not in job.params and "fallback_reason" not in job.params
    assert [call["reference_count"] for call in calls] == [2, 2]
    assert all(call["mode"] == "reference" for call in calls)
    assert quote["mode"] == "reference"
    inputs = video.artifact_metadata["lineage"]["inputs"]
    assert [(item["role"], item["artifact_id"]) for item in inputs] == [
        ("reference", art["a"].id),
        ("reference", art["b"].id),
    ]


@requires_pg
def test_reference_with_first_or_last_frame_is_rejected(pg, monkeypatch):
    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _recording_plan([]))

    async def _run():
        async with pg() as s:
            canvas, art = await _canvas_with_images(s)
            errors = []
            for frames in (
                {"first_frame_artifact_id": art["frame"].id},
                {"last_frame_artifact_id": art["frame"].id},
            ):
                req = _video_request(reference_artifact_ids=[art["a"].id], **frames)
                for call in (service.run_node(s, canvas, "vid", req), service.quote_node(s, canvas, req)):
                    with pytest.raises(service.CanvasError) as caught:
                        await call
                    errors.append(str(caught.value))
            return errors

    assert set(asyncio.run(_run())) == {service.REFERENCE_WITH_FRAME}
    assert "去掉首帧连线" in service.REFERENCE_WITH_FRAME


@requires_pg
def test_reference_artifacts_must_be_images_of_the_same_project(pg, monkeypatch):
    from app.db.models import Artifact

    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _recording_plan([]))

    async def _run():
        async with pg() as s:
            canvas, art = await _canvas_with_images(s)
            other = await service.create_canvas(s, await _user(s, "other-user"), title="other")
            foreign = await service.upload(
                s, other, node_id="x", filename="x.png", content_type="image/png", data=b"png"
            )
            video = Artifact(project_id=canvas.project_id, type="video", storage_key="videos/v.mp4")
            s.add(video)
            await s.flush()
            messages = []
            for bad in (foreign.id, video.id, "missing"):
                with pytest.raises(service.CanvasError) as caught:
                    await service.run_node(
                        s, canvas, "vid", _video_request(reference_artifact_ids=[art["a"].id, bad])
                    )
                messages.append(str(caught.value))
            return messages

    messages = asyncio.run(_run())
    assert messages[0] == messages[2] == "引用的素材不属于这个画布" and "图片" in messages[1]


@requires_pg
def test_pending_references_are_allowed_only_when_quoting(pg, monkeypatch):
    calls = []
    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _recording_plan(calls))

    async def _run():
        async with pg() as s:
            canvas, _ = await _canvas_with_images(s)
            req = _video_request(reference_artifact_ids=[f"{service.PENDING_PREFIX}a"])
            quote, _ = await service.quote_node_with_budget(s, canvas, req, quote_only=True)
            with pytest.raises(service.CanvasError):
                await service.run_node(s, canvas, "vid", req)
            return quote

    assert asyncio.run(_run())["mode"] == "reference"
    assert calls[0]["reference_count"] == 1


@requires_pg
def test_idempotency_key_changes_with_references(pg, monkeypatch):
    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _recording_plan([]))

    async def _run():
        async with pg() as s:
            canvas, art = await _canvas_with_images(s)
            a, b = art["a"].id, art["b"].id

            def req(ids):
                return _video_request(reference_artifact_ids=ids, client_request_id="click-1")

            first = await service.run_node(s, canvas, "vid", req([a]))
            same = await service.run_node(s, canvas, "vid", req([a]))
            other = await service.run_node(s, canvas, "vid", req([a, b]))
            reordered = await service.run_node(s, canvas, "vid", req([b, a]))
            return first, same, other, reordered

    first, same, other, reordered = asyncio.run(_run())
    assert first.id == same.id
    assert len({first.idempotency_key, other.idempotency_key, reordered.idempotency_key}) == 3
    assert len({first.id, other.id, reordered.id}) == 3


# ── 路由失败的原因 ───────────────────────────────────────────────────────


def _ark(model, *, modes, max_refs=0):
    return ArkVideoProvider(
        api_key="test-key",
        base_url="https://ark.test/api/v3",
        model_name=model,
        deployment_name=model,
        capabilities={
            "modes": modes,
            "min_duration": 4,
            "max_duration": 15,
            "resolutions": ["480p", "720p"],
            "max_reference_images": max_refs,
        },
    )


def _catalog_deployment(slug, provider):
    return CatalogDeployment(
        id=slug,
        slug=slug,
        name=slug,
        driver="ark_video",
        modality="video",
        model=provider.model_name,
        base_url="https://ark.test/api/v3",
        enabled=True,
        priority=100,
        capabilities=MappingProxyType({}),
        pricing=MappingProxyType({"output_per_million_tokens": "23"}),
        revision=1,
        config_hash=f"hash-{slug}",
        instance=provider,
    )


@pytest.fixture
def catalog(monkeypatch):
    """装配假 Catalog 与默认 Route（真实 ArkVideoProvider 做能力判断）。"""

    def _configure(*deployments, fallbacks=None):
        snapshot = CatalogSnapshot(
            MappingProxyType({item.slug: item for item in deployments}),
            "rev",
            datetime.now(timezone.utc),
        )
        fake = SimpleNamespace(snapshot=snapshot)
        monkeypatch.setattr(video_module, "get_catalog", lambda: fake)
        monkeypatch.setattr("app.model_runtime.catalog.get_catalog", lambda: fake)
        route = SimpleNamespace(
            purpose="shot_video",
            tier="preview",
            modality="video",
            primary_deployment=deployments[0].slug,
            fallback_deployments=(
                [item.slug for item in deployments[1:]] if fallbacks is None else fallbacks
            ),
            enabled=True,
            revision=1,
        )

        async def effective(session, purpose, tier="final"):
            return route

        monkeypatch.setattr("app.services.model_route_service.get_effective_route", effective)

    return _configure


@requires_pg
def test_reference_routes_to_the_deployment_that_supports_references(pg, catalog):
    pro_fast = _catalog_deployment("pro-fast", _ark("seedance-1-0-pro-fast", modes=["text", "first_frame"]))
    mini = _catalog_deployment(
        "seedance-2-mini",
        _ark("seedance-2-0-mini", modes=["text", "first_frame", "reference"], max_refs=9),
    )
    catalog(pro_fast, mini)

    async def _run():
        async with pg() as s:
            canvas, art = await _canvas_with_images(s)
            req = _video_request(reference_artifact_ids=[art["a"].id, art["b"].id])
            job = await service.run_node(s, canvas, "vid", req)
            plain = await service.quote_node(s, canvas, _video_request())
            return job, plain

    job, plain = asyncio.run(_run())
    assert job.params["routing_snapshot"]["deployment_name"] == "seedance-2-mini"
    assert job.params["mode"] == "reference" and len(job.params["reference_images"]) == 2
    assert job.model == "seedance-2-0-mini"
    # 不带参考图的请求仍走默认模型
    assert plain["model"] == "pro-fast"


@requires_pg
def test_no_reference_capable_model_gives_plain_chinese_reason(pg, catalog):
    catalog(_catalog_deployment("pro-fast", _ark("seedance-1-0-pro-fast", modes=["text", "first_frame"])))

    async def _run():
        async with pg() as s:
            canvas, art = await _canvas_with_images(s)
            with pytest.raises(service.CanvasError) as caught:
                await service.run_node(s, canvas, "vid", _video_request(reference_artifact_ids=[art["a"].id]))
            with pytest.raises(service.CanvasError) as many:
                await service.quote_node(
                    s, canvas, _video_request(reference_artifact_ids=[art["a"].id, art["b"].id])
                )
            return str(caught.value), str(many.value)

    one, many = asyncio.run(_run())
    assert one == many == service.NO_REFERENCE_MODEL
    assert "Seedance 2.0 mini" in one and "模型服务" in one
    assert "Deployment" not in one and "unsupported" not in one


def _mini(slug, max_refs, low=4, high=15):
    provider = ArkVideoProvider(
        api_key="test-key",
        base_url="https://ark.test/api/v3",
        model_name=f"{slug}-model",
        deployment_name=slug,
        capabilities={
            "modes": ["text", "first_frame", "reference"],
            "min_duration": low,
            "max_duration": high,
            "resolutions": ["480p", "720p"],
            "max_reference_images": max_refs,
        },
    )
    return _catalog_deployment(slug, provider)


@requires_pg
def test_too_many_references_uses_the_limit_of_the_routed_deployment(pg, catalog):
    # 默认 Route 只有 two（2 张）；nine（9 张）已启用但不在 Route 里，也没被选择
    two, nine = _mini("two", 2), _mini("nine", 9)
    catalog(two, nine, fallbacks=[])

    async def _run():
        async with pg() as s:
            canvas, art = await _canvas_with_images(s)
            ids = [art["a"].id, art["b"].id, art["c"].id]
            with pytest.raises(service.CanvasError) as default_route:
                await service.quote_node(s, canvas, _video_request(reference_artifact_ids=ids))
            with pytest.raises(service.CanvasError) as chosen:
                await service.quote_node(s, canvas, _video_request(reference_artifact_ids=ids, model="two"))
            ok = await service.quote_node(s, canvas, _video_request(reference_artifact_ids=ids, model="nine"))
            return str(default_route.value), str(chosen.value), ok

    default_route, chosen, ok = asyncio.run(_run())
    expected = "参考图最多 2 张，现在连了 3 张：去掉几条参考图连线"
    assert default_route == chosen == expected
    assert ok["mode"] == "reference"


@requires_pg
def test_fallback_on_the_route_raises_the_limit(pg, catalog):
    catalog(_mini("two", 2), _mini("nine", 9))  # nine 是 two 的备用：3 张能路由成功

    async def _run():
        async with pg() as s:
            canvas, art = await _canvas_with_images(s)
            ids = [art["a"].id, art["b"].id, art["c"].id]
            return await service.quote_node(s, canvas, _video_request(reference_artifact_ids=ids))

    assert asyncio.run(_run())["model"] == "nine"


@requires_pg
def test_unsupported_duration_is_reported_as_duration_not_missing_model(pg, catalog):
    catalog(_mini("mini", 9))

    async def _run():
        async with pg() as s:
            canvas, art = await _canvas_with_images(s)
            messages = []
            for count in (1, 3):
                req = service.NodeRunRequest(
                    kind="video",
                    prompt="x",
                    duration=3,
                    reference_artifact_ids=[art["a"].id, art["b"].id, art["c"].id][:count],
                )
                with pytest.raises(service.CanvasError) as caught:
                    await service.quote_node(s, canvas, req)
                messages.append(str(caught.value))
            return messages

    messages = asyncio.run(_run())
    expected = "直接参考角色图需要 4–15 秒的镜头（现在是 3 秒）：把镜头时长改到这个范围"
    assert messages == [expected, expected]


@requires_pg
def test_duration_and_count_both_wrong_reports_the_count(pg, catalog):
    catalog(_mini("mini", 2))

    async def _run():
        async with pg() as s:
            canvas, art = await _canvas_with_images(s)
            req = service.NodeRunRequest(
                kind="video",
                prompt="x",
                duration=3,
                reference_artifact_ids=[art["a"].id, art["b"].id, art["c"].id],
            )
            with pytest.raises(service.CanvasError) as caught:
                await service.quote_node(s, canvas, req)
            return str(caught.value)

    assert "最多 2 张" in asyncio.run(_run())


# ── 音频 ─────────────────────────────────────────────────────────────────


def test_reference_payload_has_reference_roles_and_audio_off(tmp_path, monkeypatch):
    """2.0 mini 默认会出声；画布的声音来自配音和 BGM，所以请求里必须显式 generate_audio=false。"""
    from app.providers import ark_video

    monkeypatch.setattr(ark_video, "STORAGE_DIR", tmp_path)
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a.png").write_bytes(b"png")
    provider = _ark("seedance-2-0-mini", modes=["text", "reference"], max_refs=9)
    req = VideoGenerationRequest(
        mode="reference",
        prompt="三人对视",
        reference_images=["images/a.png"],
        duration=5,
        resolution="480p",
        aspect_ratio="9:16",
    )

    assert provider.supports(req)
    payload = provider.build_payload(req, "seedance-2-0-mini")
    roles = [item.get("role") for item in payload["content"] if item["type"] == "image_url"]
    assert roles == ["reference_image"] and payload["generate_audio"] is False


# ── 批量运行 ─────────────────────────────────────────────────────────────


@requires_pg
def test_batch_carries_reference_images_and_quotes_pending_references(pg, monkeypatch):
    from sqlalchemy import select

    from app.db.models import GenerationJob

    calls = []
    monkeypatch.setattr("app.model_runtime.video.resolve_video_plan", _recording_plan(calls))

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await service.create_canvas(s, owner, title="batch")
            graph = {
                "nodes": [
                    _node("a", "image", role="character", character="a", prompt="角色a"),
                    _node("b", "image", role="character", character="b", prompt="角色b"),
                    _node("vid", "video", prompt="对视"),
                ],
                "edges": [_edge("a", "vid", "reference"), _edge("b", "vid", "reference")],
            }
            await service.save_graph(s, canvas, graph, revision=1)
            # 角色图还没生成：整批报价把它们当占位，视频参考图数量按 2 张估价
            quote = await batches.batch_quote(s, canvas, batches.BatchRequest())
            ups = {}
            for name in ("a", "b"):
                ups[name] = await service.upload(
                    s, canvas, node_id=name, filename=f"{name}.png", content_type="image/png", data=b"png"
                )
            batch = await batches.create_batch(
                s, canvas, batches.BatchRunRequest(client_request_id="b-ref", kinds=["video"])
            )
            jobs = (await s.execute(select(GenerationJob))).scalars().all()
            return quote, ups, batch, jobs

    quote, ups, batch, jobs = asyncio.run(_run())

    assert "vid" not in {item["node_id"] for item in quote["unavailable_nodes"]}
    assert calls[0]["reference_count"] == 2
    [job] = jobs
    assert job.params["mode"] == "reference"
    assert job.params["reference_images"] == [ups["a"].storage_key, ups["b"].storage_key]
    assert batch.nodes["vid"]["status"] == "running"


def test_batch_blocks_references_mixed_with_first_frame():
    graph = {
        "nodes": [
            _node("c", "image", role="character"),
            _node("k", "image"),
            _node("v", "video", prompt="x"),
        ],
        "edges": [_edge("c", "v", "reference"), _edge("k", "v", "first_frame")],
    }
    _, blockers = service.resolve_node_request(graph, "v", {"c": ["rc"], "k": ["rk"]}, "9:16")
    assert blockers == [service.REFERENCE_WITH_FRAME]


# ── 镜头口径 ─────────────────────────────────────────────────────────────


def _shot_graph(*, reference_mode):
    nodes = [
        _node("char", "image", role="character", character="林夏"),
        _node("legacy-char", "image"),  # 旧画布角色：没有 role，出边全是 reference
        _node("frame", "image", label="1 · 开门 关键帧"),
        _node("vid", "video", label="1 · 开门"),
    ]
    edges = [_edge("char", "frame", "reference"), _edge("legacy-char", "frame", "reference")]
    if reference_mode:
        nodes[3]["data"]["parked_first_frame"] = "frame"
        edges += [_edge("char", "vid", "reference"), _edge("legacy-char", "vid", "reference")]
    else:
        edges.append(_edge("frame", "vid", "first_frame"))
    return {"nodes": nodes, "edges": edges}


@pytest.mark.parametrize("reference_mode", [False, True])
def test_reference_edges_do_not_turn_characters_into_shots_or_shots_into_characters(reference_mode):
    shots = canvas_shots(_shot_graph(reference_mode=reference_mode))

    assert [(shot["main_id"], shot["video_id"], shot["frame_id"]) for shot in shots] == [
        ("vid", "vid", "frame")
    ]


def test_unparked_frame_without_edge_is_still_its_own_shot():
    graph = _shot_graph(reference_mode=True)
    graph["nodes"][3]["data"].pop("parked_first_frame")
    assert [shot["main_id"] for shot in canvas_shots(graph)] == ["frame", "vid"]


def test_audio_is_off_by_default_in_bootstrapped_reference_deployment():
    """启动引导里的 2.0 mini 部署没有开 generate_audio，适配器默认关闭。"""
    from app.services import model_deployment_bootstrap as bootstrap

    source = open(bootstrap.__file__, encoding="utf-8").read()
    assert "generate_audio" not in source


# ── 停放的首帧不是角色 ────────────────────────────────────────────────────


def test_parked_frame_reused_as_reference_is_not_a_character():
    from app.services import character_library_service as library

    # A 是镜头 X 的停放首帧，同时是镜头 Y 的参考图：剩余出边全是 reference
    nodes = [
        _node("A", "image"),
        _node("char", "image", role="character", character="林夏"),
        _node("X", "video", prompt="x", parked_first_frame="A"),
        _node("Y", "video", prompt="y"),
    ]
    edges = [
        _edge("char", "X", "reference"),
        _edge("A", "Y", "reference"),
    ]
    graph = {"nodes": nodes, "edges": edges}

    assert library.is_character_node(nodes[0], edges) is True  # 不传 nodes 时保持旧口径
    assert library.is_character_node(nodes[0], edges, nodes) is False
    assert set(library.character_groups(graph)) == {"林夏"}
    assert [(s["main_id"], s["frame_id"]) for s in canvas_shots(graph)] == [("X", "A"), ("Y", None)]


def test_parked_without_reference_edges_is_blocked_in_resolver():
    graph = {
        "nodes": [_node("k", "image"), _node("v", "video", prompt="对视", parked_first_frame="k")],
        "edges": [],
    }
    request, blockers = service.resolve_node_request(graph, "v", {"k": ["fk"]}, "9:16")
    assert blockers == [service.REFERENCE_MISSING] and request["first_frame_artifact_id"] is None
