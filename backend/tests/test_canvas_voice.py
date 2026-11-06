"""画布配音（任务 1.4）：配音节点的图校验、tts 报价与运行、结果汇总、音色列表、拆镜头带台词、voice 步骤状态。

PG 部分需要 TEST_DATABASE_URL；TTS 一律 mock / monkeypatch，不调用真实厂商。
"""

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from test_node_canvas import _node, _user, pg, requires_pg  # noqa: F401  (pg 是 fixture)

from app.db.models import NodeCanvas
from app.services import canvas_batch_service as batches
from app.services import node_canvas_service as service
from app.services import workspace_overview_service as overview


def _edge(source, target, handle):
    return {"id": f"{source}-{target}-{handle}", "source": source, "target": target, "targetHandle": handle}


def _fake_qwen(monkeypatch):
    """假装配置了 Qwen TTS（云端、计费）；Worker 不会真的被调用。"""
    monkeypatch.setattr(service, "_speech_provider", lambda: (SimpleNamespace(name="qwen_tts"), False))


# ── 图校验与解析 ──────────────────────────────────────────────────────────


def test_graph_accepts_audio_nodes_and_voice_edges_but_not_unknown_types():
    graph = service.Graph.model_validate(
        {
            "nodes": [
                {"id": "a", "type": "audio", "position": {"x": 0, "y": 0}, "data": {"text": "你好"}},
                {"id": "v", "type": "video", "position": {"x": 0, "y": 0}},
                {"id": "i", "type": "image", "position": {"x": 0, "y": 0}},
                {"id": "t", "type": "text", "position": {"x": 0, "y": 0}},
            ],
            "edges": [
                {"id": "e1", "source": "a", "target": "v", "targetHandle": "voice"},
                {"id": "e2", "source": "a", "target": "i", "targetHandle": "voice"},
                {"id": "e3", "source": "t", "target": "a", "targetHandle": "prompt"},
            ],
        }
    )
    assert [node.type for node in graph.nodes] == ["audio", "video", "image", "text"]
    with pytest.raises(ValueError):
        service.Graph.model_validate({"nodes": [{"id": "x", "type": "sound", "position": {"x": 0, "y": 0}}]})


def test_video_resolution_ignores_voice_edges():
    nodes = [
        {"id": "a", "type": "audio", "position": {"x": 0, "y": 0}, "data": {"text": "你好", "voice": "Moon"}},
        {"id": "v", "type": "video", "position": {"x": 0, "y": 0}, "data": {"prompt": "推门"}},
    ]
    with_voice = service.resolve_node_request(
        {"nodes": nodes, "edges": [_edge("a", "v", "voice")]}, "v", {"a": ["art"]}, "9:16"
    )
    without = service.resolve_node_request({"nodes": nodes, "edges": []}, "v", {"a": ["art"]}, "9:16")
    assert with_voice == without
    assert with_voice[0]["first_frame_artifact_id"] is None and with_voice[1] == []


def test_resolve_audio_limits_text_length():
    long_text = "字" * (service.MAX_TTS_CHARS + 1)
    graph = {"nodes": [_node("a", "audio", text=long_text)], "edges": []}
    request, blockers = service.resolve_node_request(graph, "a", {}, "9:16")
    assert request["kind"] == "tts" and blockers == [f"台词不能超过 {service.MAX_TTS_CHARS} 字"]


@requires_pg
def test_save_graph_keeps_audio_nodes_and_voice_edges(pg):
    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await service.create_canvas(s, owner, title="t")
            graph = {
                "nodes": [_node("a", "audio", text="你好", speaker="旁白"), _node("v", "video")],
                "edges": [_edge("a", "v", "voice")],
            }
            await service.save_graph(s, canvas, graph, revision=1)
            return canvas.graph

    saved = asyncio.run(_run())
    assert [node["type"] for node in saved["nodes"]] == ["audio", "video"]
    assert saved["edges"][0]["targetHandle"] == "voice"


# ── tts 报价与运行 ────────────────────────────────────────────────────────


def test_tts_request_fingerprint_follows_text_and_voice():
    a = service.NodeRunRequest(kind="tts", text="你好", voice="Cherry", client_request_id="1")
    b = service.NodeRunRequest(kind="tts", text="你好", voice="Cherry", client_request_id="2")
    c = service.NodeRunRequest(kind="tts", text="你好", voice="Moon")
    assert service.request_fingerprint(a) == service.request_fingerprint(b)
    assert service.request_fingerprint(a) != service.request_fingerprint(c)


@requires_pg
def test_tts_quote_qwen_by_characters_and_mock_is_free(pg, monkeypatch):
    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await service.create_canvas(s, owner, title="t")
            req = service.NodeRunRequest(kind="tts", text=" " + "字" * 400 + " ")
            monkeypatch.setattr(service, "_speech_provider", lambda: (SimpleNamespace(name="mock_tts"), True))
            mock_quote = await service.quote_node(s, canvas, req)
            _fake_qwen(monkeypatch)
            qwen_quote = await service.quote_node(s, canvas, req)
            with pytest.raises(service.CanvasError, match="需要台词"):
                await service.quote_node(s, canvas, service.NodeRunRequest(kind="tts", text=" "))
            with pytest.raises(service.CanvasError, match="音色"):
                await service.quote_node(
                    s, canvas, service.NodeRunRequest(kind="tts", text="x", voice="Nope")
                )
            return mock_quote, qwen_quote

    mock_quote, qwen_quote = asyncio.run(_run())
    assert mock_quote["estimated_cost"] == 0.0
    # 400 字符 × 0.8 元 / 万字符
    assert qwen_quote == {"kind": "tts", "model": "qwen_tts", "characters": 400, "estimated_cost": 0.032}


@requires_pg
def test_tts_provider_unavailable_gives_clear_reason(pg, monkeypatch):
    from app.model_runtime.media import get_media_runtime

    def boom(*args, **kwargs):
        raise RuntimeError("没有可用语音 Provider")

    monkeypatch.setattr(get_media_runtime(), "resolve_speech", boom)

    async def _run():
        async with pg() as s:
            canvas = await service.create_canvas(s, await _user(s), title="t")
            await service.quote_node(s, canvas, service.NodeRunRequest(kind="tts", text="你好"))

    with pytest.raises(service.CanvasError, match="没有可用的配音服务|还没有可用的配音服务"):
        asyncio.run(_run())


@requires_pg
def test_tts_run_creates_job_with_node_ownership_idempotency_and_budget(pg, monkeypatch):
    monkeypatch.setenv("DAILY_BUDGET_CNY", "0.001")
    _fake_qwen(monkeypatch)

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await service.create_canvas(s, owner, title="t")
            await service.save_graph(
                s, canvas, {"nodes": [_node("a", "audio", text="你好世界")], "edges": []}, revision=1
            )
            req = service.NodeRunRequest(
                kind="tts", text="你好世界", voice="Serena", client_request_id="click-1"
            )
            first = await service.run_node(s, canvas, "a", req)
            again = await service.run_node(s, canvas, "a", req)  # 同一次点击的重试：同一个任务，不重复预留
            # 预算上限 0.001：已在途 0.00032，再来一条 450 字（0.036 元）超额
            with pytest.raises(service.CanvasError, match="超出今日云端花费上限"):
                await service.run_node(
                    s, canvas, "a", service.NodeRunRequest(kind="tts", text="字" * 450, client_request_id="x")
                )
            with pytest.raises(service.CanvasError, match="节点不存在"):
                await service.run_node(s, canvas, "nope", req)
            return first, again

    first, again = asyncio.run(_run())
    assert first.id == again.id
    assert first.operation == "tts" and first.provider == "qwen_tts"
    params = first.params
    assert params["canvas_node_id"] == "a" and params["canvas_id"]
    assert params["text"] == "你好世界" and params["voice_id"] == "Serena"
    assert params["voice_model"]  # 预置音色要带合成模型
    assert params["budget_estimate"] == "0.000320"
    assert params["speech_provider"] == "qwen_tts"


@requires_pg
def test_tts_mock_run_is_free_and_reserves_nothing(pg, monkeypatch):
    monkeypatch.setattr(service, "_speech_provider", lambda: (SimpleNamespace(name="mock_tts"), True))

    async def _run():
        async with pg() as s:
            canvas = await service.create_canvas(s, await _user(s), title="t")
            await service.save_graph(s, canvas, {"nodes": [_node("a", "audio")], "edges": []}, revision=1)
            return await service.run_node(s, canvas, "a", service.NodeRunRequest(kind="tts", text="你好"))

    job = asyncio.run(_run())
    assert job.params["budget_estimate"] == "0" and "voice_id" not in job.params


@requires_pg
def test_node_runs_include_audio_results_with_duration(pg, monkeypatch):
    from app.db.models import Artifact

    _fake_qwen(monkeypatch)

    async def _run():
        async with pg() as s:
            canvas = await service.create_canvas(s, await _user(s), title="t")
            await service.save_graph(s, canvas, {"nodes": [_node("a", "audio")], "edges": []}, revision=1)
            job = await service.run_node(s, canvas, "a", service.NodeRunRequest(kind="tts", text="你好"))
            job.status = "succeeded"
            s.add(
                Artifact(
                    project_id=canvas.project_id,
                    job_id=job.id,
                    type="audio",
                    storage_key="audios/a.wav",
                    duration_ms=3200,
                    artifact_metadata={"canvas_node_id": "a", "duration_ms": 3200},
                )
            )
            await s.flush()
            return await service.node_runs(s, canvas)

    runs = asyncio.run(_run())
    result = runs["nodes"]["a"]["results"][0]
    assert result["type"] == "audio" and result["duration_ms"] == 3200
    assert runs["nodes"]["a"]["jobs"][0]["status"] == "succeeded"


@requires_pg
def test_node_runs_report_cost_per_node_and_shot(pg):
    """usage_events 经 job → 节点汇总：实际优先、没有才用估算并标记；镜头 = 画面 + 视频 + 配音。"""
    from app.db.models import GenerationJob, UsageEvent

    async def _run():
        async with pg() as s:
            canvas = await service.create_canvas(s, await _user(s), title="t")
            graph = {
                "nodes": [
                    _node("img", "image"),
                    _node("vid", "video", prompt="推门"),
                    _node("voice", "audio"),
                ],
                "edges": [
                    {"id": "e1", "source": "img", "target": "vid", "targetHandle": "first_frame"},
                    _edge("voice", "vid", "voice"),
                ],
            }
            await service.save_graph(s, canvas, graph, revision=1)
            jobs = {}
            for node_id in ("img", "vid", "voice"):
                job = GenerationJob(
                    project_id=canvas.project_id,
                    operation="x",
                    provider="p",
                    model="m",
                    status="succeeded",
                    idempotency_key=f"cost-{node_id}",
                    params={"canvas_node_id": node_id},
                )
                s.add(job)
                jobs[node_id] = job
            await s.flush()

            def usage(node_id, **kwargs):
                return UsageEvent(
                    project_id=canvas.project_id, job_id=jobs[node_id].id, operation="x", **kwargs
                )

            s.add_all(
                [
                    usage("img", actual_cost=Decimal("0.10"), estimated_cost=Decimal("0.12")),
                    usage("img", actual_cost=Decimal("0.05"), estimated_cost=Decimal("0.05")),
                    usage("vid", estimated_cost=Decimal("2.00")),  # 还没结算：只有估算
                    usage("voice", actual_cost=Decimal("0"), estimated_cost=Decimal("0")),  # mock 为 0
                ]
            )
            await s.flush()
            return await service.node_runs(s, canvas)

    runs = asyncio.run(_run())
    img, vid, voice = (runs["nodes"][key]["cost"] for key in ("img", "vid", "voice"))
    assert img == {
        "actual": pytest.approx(0.15),
        "estimated": 0.0,
        "total": pytest.approx(0.15),
        "is_estimate": False,
    }
    assert (
        vid["actual"] == 0 and vid["estimated"] == 2.0 and vid["total"] == 2.0 and vid["is_estimate"] is True
    )
    assert voice["total"] == 0 and voice["is_estimate"] is False
    # 镜头 = 视频节点为主节点；花费是三个节点之和，含估算部分所以标记为估算
    shot = runs["shot_costs"]["vid"]
    assert shot["total"] == pytest.approx(2.15) and shot["actual"] == pytest.approx(0.15)
    assert shot["is_estimate"] is True


# ── Worker：execute_tts ───────────────────────────────────────────────────


def _tts_harness(monkeypatch):
    from app.providers.base import ProviderResult
    from app.worker import generation_worker as gw

    class _Provider:
        name = "qwen_tts"

        async def synthesize(self, req):
            return ProviderResult(
                success=True,
                provider="qwen_tts",
                model="qwen3-tts-flash",
                artifacts=[{"storage_key": "audios/a.wav", "type": "audio", "duration_ms": 2000}],
                usage={"output_audio_seconds": 2.0},
            )

    class _Session:
        gets = 0

        async def get(self, model, key):
            _Session.gets += 1
            return None

        async def flush(self):
            pass

    async def no_abort(session, job):
        return False

    async def noop(*args, **kwargs):
        return None

    created, finalized = {}, {}

    async def fake_create_artifact(session, **kw):
        created.update(kw)
        return SimpleNamespace(id="art1", **kw)

    async def fake_finalize(session, job, **kw):
        finalized.update(kw)

    monkeypatch.setattr(gw, "_should_abort_before_submit", no_abort)
    resolved: list = []

    def fake_resolve(explicit_name=None):
        resolved.append(explicit_name)
        return _Provider(), False

    monkeypatch.setattr(gw._media_runtime, "resolve_speech", fake_resolve)
    gw.resolved_calls = resolved
    monkeypatch.setattr(gw, "_ensure_media_invocation", noop)
    monkeypatch.setattr(gw.asset_service, "create_artifact", fake_create_artifact)
    monkeypatch.setattr(gw, "finalize_job", fake_finalize)
    return gw, _Session(), created, finalized


def _capture_warnings(monkeypatch, gw):
    """直接截获 Worker 的 warning（日志是否传播到 caplog 取决于其他测试的日志配置）。"""
    messages: list[str] = []
    monkeypatch.setattr(gw.logger, "warning", lambda message, *args, **kwargs: messages.append(str(message)))
    return messages


async def test_execute_tts_canvas_node_artifact_without_shot_warning(monkeypatch):
    gw, session, created, finalized = _tts_harness(monkeypatch)
    warnings = _capture_warnings(monkeypatch, gw)
    job = SimpleNamespace(
        id="job1",
        project_id="p1",
        provider=None,
        params={"text": "你好世界", "canvas_id": "c1", "canvas_node_id": "n1", "track_type": "canvas"},
    )
    await gw.execute_tts(session, job)

    assert created["project_id"] == "p1" and created["type"] == "audio"
    assert created["meta"]["canvas_node_id"] == "n1"
    assert created["meta"]["duration_ms"] == 2000 and created["meta"]["text"] == "你好世界"
    assert created["meta"]["duration_estimated"] is True  # 文件读不出来才退回估算
    assert warnings == []
    # 4 字符 × 0.8 / 万字符
    assert finalized["usage_fields"]["actual_cost"] == Decimal("0.000320")


async def test_execute_tts_canvas_uses_real_audio_duration(monkeypatch):
    gw, session, created, _ = _tts_harness(monkeypatch)

    async def fake_probe(path):
        return {"ok": True, "duration": 3.4567}

    monkeypatch.setattr(gw.media_probe, "probe_media", fake_probe)
    job = SimpleNamespace(
        id="job1", project_id="p1", provider=None, params={"text": "你好", "canvas_node_id": "n1"}
    )
    await gw.execute_tts(session, job)
    assert created["duration_ms"] == 3457 and created["meta"]["duration_ms"] == 3457
    assert "duration_estimated" not in created["meta"]


async def test_execute_tts_canvas_follows_submitted_provider_or_fails(monkeypatch):
    gw, session, created, finalized = _tts_harness(monkeypatch)
    job = SimpleNamespace(
        id="job1",
        project_id="p1",
        provider=None,
        params={"text": "你好", "canvas_node_id": "n1", "speech_provider": "mock_tts"},
    )
    await gw.execute_tts(session, job)
    assert gw.resolved_calls == ["mock_tts"] and created  # 按提交时的 provider 解析

    def unavailable(explicit_name=None):
        raise RuntimeError("语音 Provider 不可用")

    created.clear()
    monkeypatch.setattr(gw._media_runtime, "resolve_speech", unavailable)
    await gw.execute_tts(session, job)
    # 不能悄悄换成别的（可能计费的）provider：终态失败并说明原因
    assert not created
    assert finalized["success"] is False and finalized["error_code"] == "no_provider"
    assert "mock_tts" in finalized["error"] and "不可用" in finalized["error"]


async def test_execute_tts_episode_path_still_resolves_without_name(monkeypatch):
    gw, session, _, _ = _tts_harness(monkeypatch)
    job = SimpleNamespace(
        id="job2",
        project_id="p1",
        provider=None,
        params={"text": "你好", "shot_id": "s1", "speech_provider": "x"},
    )
    await gw.execute_tts(session, job)
    assert gw.resolved_calls == [None]


async def test_execute_tts_episode_path_still_warns_when_shot_missing(monkeypatch):
    gw, session, created, _ = _tts_harness(monkeypatch)
    warnings = _capture_warnings(monkeypatch, gw)
    job = SimpleNamespace(
        id="job2",
        project_id="p1",
        provider=None,
        params={"text": "你好", "shot_id": "s1", "track_type": "dialogue", "track_index": 0},
    )
    await gw.execute_tts(session, job)

    assert "canvas_node_id" not in created["meta"] and created["meta"]["shot_id"] == "s1"
    assert any("shot s1 不存在" in message for message in warnings)


# ── 音色列表 ──────────────────────────────────────────────────────────────


def test_voices_endpoint_returns_preset_list_without_provider_calls():
    from app.routers import node_canvases as router

    voices = asyncio.run(router.voices())
    assert 6 <= len(voices) <= 10
    assert len({voice["id"] for voice in voices}) == len(voices)
    for voice in voices:
        assert voice["name"] and voice["style"] and voice["sample_text"]
        assert voice["gender"] in ("male", "female")
        assert voice["sample_url"] is None
    # 固定路径必须排在 /node-canvases/{canvas_id} 之前，否则 voices 会被当成画布 id
    paths = [getattr(route, "path", "") for route in router.router.routes]
    assert paths.index("/node-canvases/voices") < paths.index("/node-canvases/{canvas_id}")


# ── 拆镜头带台词 ──────────────────────────────────────────────────────────


def _split(monkeypatch, reply):
    async def fake_llm(messages):
        return reply

    monkeypatch.setattr(service, "_call_llm", fake_llm)
    return asyncio.run(service.copilot_split(SimpleNamespace(aspect_ratio="9:16"), idea="x", shots=2))


def test_split_shot_carries_lines_tidied(monkeypatch):
    long_text = "话" * 80
    result = _split(
        monkeypatch,
        '{"shots": [{"title": "a", "prompt": "p", "lines": ['
        '{"speaker": "狐妖", "text": "你来了"}, {"text": "旁白一句"}, {"speaker": "书生", "text": "%s"},'
        '{"speaker": "x", "text": "第四句丢弃"}, {"speaker": "y", "text": ""}]}]}' % long_text,
    )
    lines = result.shots[0].lines
    assert [(line.speaker, line.text[:3]) for line in lines] == [
        ("狐妖", "你来了"),
        ("旁白", "旁白一"),
        ("书生", "话话话"),
    ]
    assert len(lines[2].text) == 60


def test_split_without_lines_is_still_valid(monkeypatch):
    result = _split(monkeypatch, '{"shots": [{"title": "a", "prompt": "p"}]}')
    assert result.shots[0].lines == []
    assert "lines" in service.SPLIT_PROMPT and "60" in service.SPLIT_PROMPT


# ── voice 步骤状态 ────────────────────────────────────────────────────────


def _step(steps, key):
    return next(step["status"] for step in steps if step["key"] == key)


def test_voice_step_rules():
    # 有配音节点：全部有结果才完成
    assert _step(overview._steps(3, 3, True, [], (4, 3)), "voice") == "active"
    assert _step(overview._steps(3, 3, True, [], (4, 4)), "voice") == "done"
    # 前面步骤没完成时配音是 todo
    assert _step(overview._steps(1, 3, True, [], (4, 0)), "voice") == "todo"
    # 配音节点都有结果，但分镜还没完成：不能提前显示完成（也不是进行中，分镜才是进行中）
    steps = overview._steps(1, 3, True, [], (4, 4))
    assert _step(steps, "voice") == "todo" and _step(steps, "storyboard") == "active"
    # 没有镜头时同理
    assert _step(overview._steps(0, 0, True, [], (2, 2)), "voice") == "todo"
    # 没有配音节点：分镜完成后算完成（可跳过），之前是 todo
    assert _step(overview._steps(3, 3, True, [], (0, 0)), "voice") == "done"
    assert _step(overview._steps(1, 3, True, [], (0, 0)), "voice") == "todo"
    # 旧调用方式（不传 voice）保持旧口径：分镜完成后配音进行中
    assert _step(overview._steps(3, 3, True), "voice") == "active"
    assert overview._next_hint(0, 0, 3, 3, [], 2) == "还有 2 句台词没配音"
    assert overview._next_hint(0, 0, 3, 3, [], 0) == "去配音"


@requires_pg
def test_project_summary_counts_voice_nodes_and_results(pg):
    from app.db.models import Artifact, GenerationJob

    async def _run():
        async with pg() as s:
            user = await _user(s)
            canvas = await service.create_canvas(s, user, title="t")
            canvas.graph = {
                "nodes": [
                    _node("t", "text", text="剧本"),
                    _node("v1", "video"),
                    _node("a1", "audio", text="一"),
                    _node("a2", "audio", text="二"),
                ],
                "edges": [_edge("a1", "v1", "voice"), _edge("a2", "v1", "voice")],
            }
            video_job = GenerationJob(
                project_id=canvas.project_id,
                operation="generate_video",
                status="succeeded",
                params={"canvas_id": canvas.id, "canvas_node_id": "v1"},
                idempotency_key="v1",
            )
            audio_job = GenerationJob(
                project_id=canvas.project_id,
                operation="tts",
                status="succeeded",
                params={"canvas_id": canvas.id, "canvas_node_id": "a1"},
                idempotency_key="a1",
            )
            s.add_all([video_job, audio_job])
            await s.flush()
            s.add_all(
                [
                    Artifact(
                        project_id=canvas.project_id, job_id=video_job.id, type="video", storage_key="v"
                    ),
                    Artifact(
                        project_id=canvas.project_id, job_id=audio_job.id, type="audio", storage_key="a"
                    ),
                ]
            )
            await s.flush()
            video_art = (await s.execute(select(Artifact).where(Artifact.type == "video"))).scalar_one()
            canvas.graph = {
                **canvas.graph,
                "nodes": [
                    {**n, "data": {**n["data"], "selected": video_art.id}} if n["id"] == "v1" else n
                    for n in canvas.graph["nodes"]
                ],
            }
            await s.flush()
            first = await overview.project_summary(s, user, canvas.id)
            canvas.graph = {**canvas.graph, "nodes": [n for n in canvas.graph["nodes"] if n["id"] != "a2"]}
            await s.flush()
            second = await overview.project_summary(s, user, canvas.id)
            return first, second

    first, second = asyncio.run(_run())
    assert _step(first["steps"], "voice") == "active"
    assert first["next_hint"] == "还有 1 句台词没配音"
    assert first["shots_total"] == 1  # 配音节点不是镜头
    assert _step(second["steps"], "voice") == "done"


# ── 批量运行 ──────────────────────────────────────────────────────────────


def test_batch_plan_excludes_audio_unless_tts_selected_and_ignores_voice_edges():
    graph = {
        "nodes": [
            _node("v", "video", prompt="p"),
            _node("a", "audio", text="你好"),
            _node("b", "audio", text="二"),
        ],
        "edges": [_edge("a", "v", "voice"), _edge("b", "v", "voice")],
    }
    assert batches.plan_batch(graph, batches.BatchRequest(), {})[0] == ["v"]
    assert batches.plan_batch(graph, batches.BatchRequest(kinds=["image", "video"]), {})[0] == ["v"]
    assert batches.plan_batch(graph, batches.BatchRequest(kinds=["tts"]), {})[0] == ["a", "b"]
    order, skipped = batches.plan_batch(graph, batches.BatchRequest(kinds=["tts"]), {"a": ["done"]})
    assert order == ["a", "b"] and skipped == {"a"}
    # voice 边不是依赖：视频与配音一起运行时，视频不等配音
    order, _ = batches.plan_batch(graph, batches.BatchRequest(kinds=["video", "tts"]), {})
    assert sorted(order) == ["a", "b", "v"]
    assert batches._predecessors(graph, {"v", "a", "b"})["v"] == []
    with pytest.raises(service.CanvasError, match="没有可运行的配音节点"):
        batches.plan_batch(
            {"nodes": [_node("v", "video")], "edges": []}, batches.BatchRequest(kinds=["tts"]), {}
        )


@requires_pg
def test_batch_quote_and_run_only_ungenerated_tts_nodes(pg, monkeypatch):
    from app.db.models import Artifact

    _fake_qwen(monkeypatch)

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await service.create_canvas(s, owner, title="t")
            await service.save_graph(
                s,
                canvas,
                {
                    "nodes": [
                        _node("a", "audio", text="你好世界"),
                        _node("b", "audio", text="再见"),
                        _node("c", "audio", text=""),
                    ],
                    "edges": [],
                },
                revision=1,
            )
            quote = await batches.batch_quote(s, canvas, batches.BatchRequest(kinds=["tts"]))
            done_job = await service.run_node(
                s, canvas, "a", service.NodeRunRequest(kind="tts", text="你好世界")
            )
            done_job.status = "succeeded"
            s.add(
                Artifact(
                    project_id=canvas.project_id, job_id=done_job.id, type="audio", storage_key="audios/a.wav"
                )
            )
            await s.flush()
            canvas.graph = {
                **canvas.graph,
                "nodes": [n for n in canvas.graph["nodes"] if n["id"] != "c"],
            }
            await s.flush()
            after = await batches.batch_quote(s, canvas, batches.BatchRequest(kinds=["tts"]))
            batch = await batches.create_batch(
                s, canvas, batches.BatchRunRequest(kinds=["tts"], client_request_id="b1")
            )
            return quote, after, batch

    quote, after, batch = asyncio.run(_run())
    # 空台词的节点显示为不能运行，但不拦住其他台词
    assert quote["blocking_count"] == 0 and quote["runnable_count"] == 2
    empty = [item for item in quote["unavailable_nodes"] if item["node_id"] == "c"]
    assert empty and empty[0]["runs"] is False and empty[0]["blocks_batch"] is False
    assert after["runnable_count"] == 1  # a 已有结果被跳过，只剩 b（c 已删除）
    assert after["estimated_total"] == pytest.approx(float(Decimal("0.8") * 2 / 10000))
    assert batch.nodes["a"]["status"] == "skipped" and batch.nodes["b"]["status"] == "running"
    assert batch.nodes["b"]["job_id"]


@requires_pg
def test_batch_skips_empty_and_running_tts_nodes_without_failing_the_batch(pg, monkeypatch):
    _fake_qwen(monkeypatch)

    async def _run():
        async with pg() as s:
            canvas = await service.create_canvas(s, await _user(s), title="t")
            await service.save_graph(
                s,
                canvas,
                {
                    "nodes": [
                        _node("ok", "audio", text="你好"),
                        _node("busy", "audio", text="再见"),
                        _node("blank", "audio", text="  "),
                    ],
                    "edges": [],
                },
                revision=1,
            )
            await service.run_node(
                s, canvas, "busy", service.NodeRunRequest(kind="tts", text="再见")
            )  # 在排队
            quote = await batches.batch_quote(s, canvas, batches.BatchRequest(kinds=["tts"]))
            first = await batches.create_batch(
                s, canvas, batches.BatchRunRequest(kinds=["tts"], client_request_id="one")
            )
            # 第二个批次（比如关掉面板又重新确认）不会给运行中的节点再建任务
            second = await batches.create_batch(
                s, canvas, batches.BatchRunRequest(kinds=["tts"], client_request_id="two")
            )
            from app.db.models import GenerationJob

            count = len(
                (await s.execute(select(GenerationJob).where(GenerationJob.operation == "tts")))
                .scalars()
                .all()
            )
            second.count = count
            return quote, first, second

    quote, first, second = asyncio.run(_run())
    assert quote["runnable_count"] == 1 and quote["blocking_count"] == 0
    assert first.nodes["ok"]["status"] == "running" and first.nodes["busy"]["status"] == "skipped"
    assert first.nodes["blank"]["status"] == "skipped"
    assert second.nodes["ok"]["status"] == "skipped" and second.nodes["busy"]["status"] == "skipped"
    assert second.count == 2  # busy 的 1 个 + ok 的 1 个，第二批没有重复建任务


@requires_pg
def test_voice_step_ignores_empty_lines_and_counts_text_linked_lines(pg):
    async def _run():
        async with pg() as s:
            user = await _user(s)
            canvas = await service.create_canvas(s, user, title="t")
            canvas.graph = {
                "nodes": [
                    _node("t", "text", text="剧本"),
                    _node("src", "text", text="连入的台词"),
                    _node("v1", "video"),
                    _node("blank", "audio", text=""),
                    _node("linked", "audio", text=""),
                    _node("loose", "audio", text="没有归属镜头的台词"),
                ],
                "edges": [_edge("src", "linked", "prompt")],
            }
            await s.flush()
            row = (
                await s.execute(select(*overview._CANVAS_COLUMNS).where(NodeCanvas.id == canvas.id))
            ).first()
            data = await overview._collect(s, [row])
            return data.stats[canvas.project_id]

    stats = asyncio.run(_run())
    # blank 不算；linked（台词来自文本节点）和 loose（没有 voice 边）都算：共 2 句、0 句有结果
    assert stats["voice"] == (2, 0)


def test_duration_limits_exposed_for_extending_shots():
    assert service.duration_limits({"min_duration": 4, "max_duration": "12", "modes": []}) == {
        "min_duration": 4,
        "max_duration": 12,
    }
    assert service.duration_limits({"min_duration": "x", "max_duration": 0}) == {}
    assert service.duration_limits(None) == {}


@requires_pg
@pytest.mark.parametrize("missing", ["no_event", "null_event", "partial_job"])
def test_node_runs_unknown_cost_is_null_and_propagates_to_shot(pg, missing):
    from app.db.models import GenerationJob, UsageEvent

    async def run():
        async with pg() as s:
            canvas = await service.create_canvas(s, await _user(s), title="unknown cost")
            await service.save_graph(s, canvas, {"nodes": [_node("v", "video")], "edges": []}, revision=1)
            job = GenerationJob(
                project_id=canvas.project_id,
                operation="x",
                provider="p",
                model="m",
                status="succeeded",
                idempotency_key="unknown-cost",
                params={"canvas_node_id": "v"},
            )
            s.add(job)
            await s.flush()
            if missing != "no_event":
                s.add(UsageEvent(project_id=canvas.project_id, job_id=job.id, operation="x"))
            if missing == "partial_job":
                other = GenerationJob(
                    project_id=canvas.project_id,
                    operation="x",
                    provider="p",
                    model="m",
                    status="succeeded",
                    idempotency_key="known-cost",
                    params={"canvas_node_id": "v"},
                )
                s.add(other)
                await s.flush()
                s.add(
                    UsageEvent(
                        project_id=canvas.project_id, job_id=other.id, operation="x", actual_cost=Decimal("1")
                    )
                )
            await s.flush()
            return await service.node_runs(s, canvas)

    runs = asyncio.run(run())
    assert runs["nodes"]["v"]["cost"]["total"] is None
    assert runs["shot_costs"]["v"]["total"] is None


@requires_pg
def test_node_runs_ignore_unpriced_events_and_unfinished_jobs(pg):
    """视频 submit 事件不带金额，不能让每个视频节点都变成"费用未知"；未完成/取消的任务按 0 计。"""
    from app.db.models import GenerationJob, UsageEvent

    async def run():
        async with pg() as s:
            canvas = await service.create_canvas(s, await _user(s), title="priced cost")
            graph = {"nodes": [_node("v", "video"), _node("w", "video")], "edges": []}
            await service.save_graph(s, canvas, graph, revision=1)

            def job(key, status, node):
                return GenerationJob(
                    project_id=canvas.project_id,
                    operation="x",
                    provider="p",
                    model="m",
                    status=status,
                    idempotency_key=key,
                    params={"canvas_node_id": node},
                )

            done, canceled, pending = (
                job("done", "succeeded", "v"),
                job("c", "canceled", "v"),
                job("p", "pending", "w"),
            )
            s.add_all([done, canceled, pending])
            await s.flush()
            s.add_all(
                [
                    UsageEvent(
                        project_id=canvas.project_id, job_id=done.id, operation="generate_video.submit"
                    ),
                    UsageEvent(
                        project_id=canvas.project_id,
                        job_id=done.id,
                        operation="generate_video",
                        estimated_cost=Decimal("0.5"),
                    ),
                ]
            )
            await s.flush()
            return await service.node_runs(s, canvas)

    runs = asyncio.run(run())
    assert runs["nodes"]["v"]["cost"] == {"actual": 0.0, "estimated": 0.5, "total": 0.5, "is_estimate": True}
    assert runs["nodes"]["w"]["cost"]["total"] == 0


@requires_pg
def test_available_models_exposes_current_tier_primary_and_fallback(pg, monkeypatch):
    from app.db.models import ModelRoute

    async def problem(*args, **kwargs):
        return ""

    monkeypatch.setattr(service, "image_model_problem", problem)

    async def run():
        async with pg() as s:
            s.add_all(
                [
                    ModelRoute(
                        purpose="shot_video",
                        modality="video",
                        tier="preview",
                        primary_deployment="a",
                        fallback_deployments=["b"],
                    ),
                    ModelRoute(
                        purpose="shot_video",
                        modality="video",
                        tier="final",
                        primary_deployment="c",
                        fallback_deployments=[],
                    ),
                ]
            )
            await s.flush()
            return await service.available_models(s)

    assert asyncio.run(run())["video_routes"] == {"preview": ["a", "b"], "final": ["c"]}
