"""画布成片：镜头口径、成片节点校验、EDL 生成、渲染（ffmpeg 实跑）、BGM 压低 / 循环 / 淡入淡出、
SRT 时间、problems / stale / 幂等 / 权限、BGM 上传嗅探、步骤状态。"""

import asyncio
import io
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, UploadFile
from test_node_canvas import _user, pg, requires_pg  # noqa: F401  (pg 是 fixture)

from app.db.models import Artifact, GenerationJob
from app.routers import canvas_render as router
from app.services import asset_service, timeline_service
from app.services import canvas_render_service as render_service
from app.services import node_canvas_service as canvas_service
from app.services import timeline_renderer as tr
from app.services import workspace_overview_service as overview
from app.services.canvas_shots import canvas_shots
from app.worker import generation_worker as worker

# ── 镜头口径（纯函数） ─────────────────────────────────────────────────────


def _n(node_id, kind, x=0, y=0, **data):
    return {"id": node_id, "type": kind, "position": {"x": x, "y": y}, "data": data}


def _e(source, target, handle):
    return {"id": f"e-{source}-{target}-{handle}", "source": source, "target": target, "targetHandle": handle}


def test_shots_exclude_characters_and_first_frames():
    graph = {
        "nodes": [
            _n("char", "image", y=-400, role="character", label="角色 · 狐妖"),
            _n("old-char", "image", y=-300),
            _n("k1", "image", y=0, label="1 · 开场 关键帧"),
            _n("v1", "video", x=400, y=0, label="1 · 开场", duration=4),
            _n("text", "text"),
        ],
        "edges": [_e("old-char", "k1", "reference"), _e("k1", "v1", "first_frame")],
    }
    shots = canvas_shots(graph)
    assert [s["main_id"] for s in shots] == ["v1"]
    only = shots[0]
    assert (only["frame_id"], only["video_id"], only["title"], only["duration_s"]) == ("k1", "v1", "开场", 4)


def test_shots_image_only_and_default_duration():
    graph = {"nodes": [_n("k1", "image", label="3 · 远景 关键帧")], "edges": []}
    (shot,) = canvas_shots(graph)
    assert (shot["main_id"], shot["video_id"], shot["frame_id"]) == ("k1", None, "k1")
    assert shot["title"] == "远景" and shot["duration_s"] == 5


def test_shots_ordering_matches_storyboard():
    graph = {
        "nodes": [
            _n("late", "video", y=0),  # 没有 order：排在有 order 的后面
            _n("b", "video", y=500, order=1),
            _n("a", "video", y=900, order=0),
            _n("below", "video", y=100),
        ],
        "edges": [],
    }
    assert [s["main_id"] for s in canvas_shots(graph)] == ["a", "b", "late", "below"]
    assert [s["index"] for s in canvas_shots(graph)] == [0, 1, 2, 3]


def test_shots_voice_edges_belong_to_main_node_and_sort_by_order():
    graph = {
        "nodes": [
            _n("k1", "image", y=0),
            _n("v1", "video", y=0),
            _n("img", "image", y=900),
            _n("a-late", "audio", y=300, order=2),
            _n("a-first", "audio", y=900, order=0),
            _n("a-noorder", "audio", y=100),
            _n("a-img", "audio", y=0),
            _n("a-other", "audio", y=0),
        ],
        "edges": [
            _e("k1", "v1", "first_frame"),
            _e("a-late", "v1", "voice"),
            _e("a-first", "v1", "voice"),
            _e("a-noorder", "v1", "voice"),
            _e("a-first", "v1", "voice"),  # 重复边不重复计入
            _e("a-img", "img", "voice"),
            _e("a-other", "v1", "prompt"),  # 不是 voice 边
        ],
    }
    by_main = {s["main_id"]: s for s in canvas_shots(graph)}
    assert by_main["v1"]["audio_ids"] == ["a-first", "a-late", "a-noorder"]
    assert by_main["img"]["audio_ids"] == ["a-img"]


# ── 渲染器纯函数 ───────────────────────────────────────────────────────────


def test_output_size_and_ranges():
    assert tr.output_size("9:16") == (720, 1280)
    assert tr.output_size("16:9", 1080) == (1920, 1080)
    assert tr.output_size("4:3", 720) == (960, 720)
    assert tr.output_size("21:9", 720)[1] == 720
    assert tr.merge_ranges([(0, 100), (100, 200), (500, 600), (50, 80), (700, 700)]) == [(0, 200), (500, 600)]


def test_duck_expression_empty_and_nested():
    assert tr.build_duck_expression([]) == "1"
    expr = tr.build_duck_expression([(i * 1000, i * 1000 + 400) for i in range(0, 40, 2)])
    assert expr.startswith("1-0.8") and expr.count("min(") == 20


def test_mix_filter_has_loudnorm_target_and_pad():
    fc, label = tr.build_audio_mix_filter(
        [{"input_index": 1, "delay_ms": 500, "is_bgm": True, "loop": True, "fade_in_ms": 1500}],
        -18,
        4000,
    )
    assert label == "[aout]"
    assert "I=-14" in fc and "apad=whole_dur=4.000" in fc and "afade=t=in" in fc
    assert "volume=-18.0dB" in fc and "adelay=500|500" in fc


# ── 媒体素材 ───────────────────────────────────────────────────────────────


def _ff(*args):
    subprocess.run([tr._ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def _video(path: Path, seconds: float, size="320x180"):
    _ff(
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={size}:rate=24:duration={seconds}",
        "-an",
        "-pix_fmt",
        "yuv420p",
        str(path),
    )


def _silence(path: Path, seconds: float):
    _ff("-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", str(seconds), str(path))


def _tone(path: Path, seconds: float, freq=440, extra=()):
    _ff("-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=44100:duration={seconds}", *extra, str(path))


def _mean_volume(path: Path, start: float, length: float) -> float:
    proc = subprocess.run(
        [tr._ffmpeg_exe(), "-hide_banner", "-ss", str(start), "-t", str(length), "-i", str(path)]
        + ["-vn", "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", proc.stderr).group(1))


def _stream_info(path: Path) -> dict:
    import imageio_ffmpeg

    frames = imageio_ffmpeg.read_frames(str(path))
    meta = next(frames)
    frames.close()
    return meta


@pytest.fixture
def storage(tmp_path, monkeypatch):
    from app.services.storage_backend import storage as backend

    monkeypatch.setattr(backend, "backend", "local")
    monkeypatch.setattr(asset_service, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(canvas_service, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(tr, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(tr, "VIDEOS_DIR", tmp_path / "videos")
    (tmp_path / "media").mkdir()
    return tmp_path


# ── 无数据库的渲染器行为（间隙 / 静帧 / 音频缺失 / 缺文件中文原因） ─────────


def _session_for(artifacts):
    session = AsyncMock()
    session.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: artifacts))
    session.get.side_effect = lambda model, aid: next((a for a in artifacts if a.id == aid), None)
    return session


def _art(aid, key, type_="video", **kwargs):
    return Artifact(id=aid, project_id="p", type=type_, storage_key=key, content_hash="", **kwargs)


@pytest.fixture
def stub_artifacts(monkeypatch):
    created = []

    async def create(session, **kwargs):
        item = SimpleNamespace(id=f"out-{len(created)}", **kwargs)
        created.append(item)
        return item

    monkeypatch.setattr(asset_service, "create_artifact", create)
    return created


async def test_gap_between_clips_is_filled_not_added_to_duration(storage, stub_artifacts):
    _video(storage / "media/a.mp4", 2)
    _video(storage / "media/b.mp4", 2)
    arts = [_art("a", "media/a.mp4"), _art("b", "media/b.mp4")]
    spec = {
        "aspect_ratio": "16:9",
        "video_tracks": [
            {
                "clips": [
                    dict(id="c1", artifact_id="a", source_out_ms=2000, timeline_start_ms=0),
                    dict(id="c2", artifact_id="b", source_out_ms=2000, timeline_start_ms=3000),
                ]
            }
        ],
    }
    result = await tr.render_timeline(_session_for(arts), project_id="p", spec=spec, burn_subtitles=False)
    # 2 秒 + 1 秒黑场间隙 + 2 秒；总时长必须与实际视频一致，不再偏大
    assert result["duration_ms"] == 5000
    info = _stream_info(storage / result["storage_key"])
    assert abs(info["duration"] - 5.0) <= 0.1


async def test_overlapping_clips_do_not_inflate_duration(storage, stub_artifacts):
    _video(storage / "media/a.mp4", 2)
    arts = [_art("a", "media/a.mp4")]
    spec = {
        "video_tracks": [
            {
                "clips": [
                    dict(id="c1", artifact_id="a", source_out_ms=2000, timeline_start_ms=0),
                    dict(id="c2", artifact_id="a", source_out_ms=2000, timeline_start_ms=1000),
                ]
            }
        ]
    }
    result = await tr.render_timeline(_session_for(arts), project_id="p", spec=spec, burn_subtitles=False)
    assert result["duration_ms"] == 4000
    assert abs(_stream_info(storage / result["storage_key"])["duration"] - 4.0) <= 0.1


async def test_image_clip_renders_as_still(storage, stub_artifacts):
    _ff(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=200x300:rate=1:duration=1",
        "-frames:v",
        "1",
        str(storage / "media/f.png"),
    )
    arts = [_art("img", "media/f.png", "image")]
    spec = {
        "aspect_ratio": "1:1",
        "video_tracks": [
            {"clips": [dict(id="c1", artifact_id="img", source_out_ms=2000, timeline_start_ms=0)]}
        ],
    }
    result = await tr.render_timeline(_session_for(arts), project_id="p", spec=spec, burn_subtitles=False)
    info = _stream_info(storage / result["storage_key"])
    assert abs(info["duration"] - 2.0) <= 0.1 and info["size"] == (720, 720)


async def test_missing_audio_fails_before_any_video_encoding(storage, stub_artifacts, monkeypatch):
    _video(storage / "media/a.mp4", 2)
    arts = [_art("a", "media/a.mp4"), _art("voice", "media/gone.wav", "audio")]
    spec = {
        "video_tracks": [
            {"clips": [dict(id="c1", artifact_id="a", source_out_ms=2000, timeline_start_ms=0)]}
        ],
        "audio_tracks": [
            {
                "clips": [
                    dict(
                        id="v",
                        kind="dialogue",
                        artifact_id="voice",
                        timeline_start_ms=0,
                        label="镜头 1 的配音「你好」",
                    )
                ]
            }
        ],
    }
    calls = []
    real = tr._run_ffmpeg
    monkeypatch.setattr(
        tr, "_run_ffmpeg", lambda args, timeout=600: calls.append(args) or real(args, timeout)
    )
    with pytest.raises(ValueError, match="镜头 1 的配音「你好」的音频文件不存在"):
        await tr.render_timeline(_session_for(arts), project_id="p", spec=spec, render_mode="final")
    assert calls == []


async def test_missing_video_file_reports_shot_number(storage, stub_artifacts):
    _video(storage / "media/a.mp4", 2)
    arts = [_art("a", "media/a.mp4"), _art("b", "media/gone.mp4"), _art("c", "media/a.mp4")]
    spec = {
        "video_tracks": [
            {
                "clips": [
                    dict(id=f"s{i}", artifact_id=aid, source_out_ms=2000, timeline_start_ms=i * 2000)
                    for i, aid in enumerate(["a", "b", "c"])
                ]
            }
        ]
    }
    with pytest.raises(ValueError, match="镜头 2 的视频文件不存在"):
        await tr.render_timeline(_session_for(arts), project_id="p", spec=spec, render_mode="final")


async def test_bgm_loops_truncates_and_fades(storage, stub_artifacts):
    """短 BGM 循环到全片长度；长 BGM 截断；首尾淡入淡出（片头片尾几乎无声，中段有声）。"""
    _video(storage / "media/a.mp4", 6)
    _tone(storage / "media/short.wav", 1.5)
    _tone(storage / "media/long.wav", 12)
    arts = [
        _art("a", "media/a.mp4"),
        _art("short", "media/short.wav", "audio"),
        _art("long", "media/long.wav", "audio"),
    ]

    def spec_for(bgm):
        return {
            "video_tracks": [
                {"clips": [dict(id="c1", artifact_id="a", source_out_ms=6000, timeline_start_ms=0)]}
            ],
            "audio_tracks": [
                {
                    "clips": [
                        dict(
                            id="bgm",
                            kind="bgm",
                            artifact_id=bgm,
                            timeline_start_ms=0,
                            volume_keyframes=[{"time_ms": 0, "volume_db": -6}],
                            fade_in_ms=1500,
                            fade_out_ms=1500,
                        )
                    ]
                }
            ],
        }

    for bgm in ("short", "long"):
        result = await tr.render_timeline(
            _session_for(arts), project_id="p", spec=spec_for(bgm), burn_subtitles=False, render_mode="final"
        )
        out = storage / result["storage_key"]
        info = _stream_info(out)
        assert abs(info["duration"] - 6.0) <= 0.15 and info["audio_codec"] == "aac"
        middle = _mean_volume(out, 2.5, 1.0)
        # 循环的短 BGM 在 3 秒（第二轮）之后仍有声；淡入淡出让首尾比中段轻很多
        assert middle > -40
        assert _mean_volume(out, 0.0, 0.3) < middle - 8
        assert _mean_volume(out, 5.7, 0.3) < middle - 8
        assert _mean_volume(out, 4.0, 1.0) > -40  # 短 BGM 第三轮仍在循环


# ── 数据库场景 ─────────────────────────────────────────────────────────────

FIXTURE_SECONDS = 3


async def _add_result(s, canvas, node_id, key, type_, *, duration_ms=None, via="upload"):
    """给节点加一个结果素材：upload（上传到节点）或 job（成功任务的产物）。"""
    artifact_kwargs = dict(
        project_id=canvas.project_id,
        type=type_,
        storage_key=key,
        duration_ms=duration_ms,
        content_hash=f"hash-{key}",
    )
    if via == "job":
        job = GenerationJob(
            operation="generate_video" if type_ == "video" else "tts",
            project_id=canvas.project_id,
            status="succeeded",
            params={"canvas_id": canvas.id, "canvas_node_id": node_id},
            idempotency_key=f"seed-{node_id}-{key}",
        )
        s.add(job)
        await s.flush()
        artifact = Artifact(job_id=job.id, **artifact_kwargs)
    else:
        artifact = Artifact(
            artifact_metadata={"source": "canvas_upload", "canvas_node_id": node_id}, **artifact_kwargs
        )
    s.add(artifact)
    await s.flush()
    return artifact


def _graph(with_audio=True):
    nodes = []
    edges = []
    for i in (1, 2, 3):
        nodes += [
            _n(f"k{i}", "image", 0, i * 400, label=f"{i} · 镜头{i} 关键帧"),
            _n(f"v{i}", "video", 400, i * 400, label=f"{i} · 镜头{i}", duration=FIXTURE_SECONDS, order=i),
        ]
        edges.append(_e(f"k{i}", f"v{i}", "first_frame"))
    if with_audio:
        nodes += [
            _n("a1", "audio", 800, 400, speaker="狐妖", text="你终于来了。", order=0),
            _n("a2", "audio", 800, 500, speaker="旁白", text="雪落无声。", order=1),
            _n("a3", "audio", 800, 800, speaker="书生", text="我等了很久。"),
        ]
        edges += [_e("a1", "v1", "voice"), _e("a2", "v1", "voice"), _e("a3", "v2", "voice")]
    nodes.append(_n("ep", "episode", 1200, 400, **canvas_service.EPISODE_DEFAULTS))
    return {"nodes": nodes, "edges": edges}


def _save_raw(canvas, graph):
    """测试里直接写 graph（含 6A 才会校验的 audio 节点），不经过 save_graph。"""
    canvas.graph = graph
    canvas.revision += 1


async def _prepare(s, tmp_path, *, settings=None, aspect="16:9", missing_video=None, bgm=False):
    owner = await _user(s)
    canvas = await canvas_service.create_canvas(s, owner, title="成片测试", aspect_ratio=aspect)
    graph = _graph()
    if settings:
        next(n for n in graph["nodes"] if n["type"] == "episode")["data"].update(settings)
    media = tmp_path / "media"
    for i in (1, 2, 3):
        _video(media / f"v{i}.mp4", FIXTURE_SECONDS)
        if i != missing_video:
            await _add_result(
                s,
                canvas,
                f"v{i}",
                f"media/v{i}.mp4",
                "video",
                duration_ms=FIXTURE_SECONDS * 1000,
                via="job" if i == 1 else "upload",
            )
    # 三句配音是静音文件：BGM 电平测量不被人声盖住；时长 1.5 / 1.2 / 1.0 秒
    for node, seconds in (("a1", 1.5), ("a2", 1.2), ("a3", 1.0)):
        _silence(media / f"{node}.wav", seconds)
        await _add_result(
            s, canvas, node, f"media/{node}.wav", "audio", duration_ms=int(seconds * 1000), via="job"
        )
    if bgm:
        _tone(media / "bgm.wav", 4, 330)
        artifact = Artifact(
            project_id=canvas.project_id,
            type="audio",
            storage_key="media/bgm.wav",
            duration_ms=4000,
            content_hash="bgm",
            artifact_metadata={"source": "bgm_upload", "filename": "雪夜钢琴.wav"},
        )
        s.add(artifact)
        await s.flush()
        next(n for n in graph["nodes"] if n["type"] == "episode")["data"]["bgm_artifact_id"] = artifact.id
    _save_raw(canvas, graph)
    await s.commit()
    return owner, canvas


def _srt_cues(path: Path):
    cues = []
    for block in path.read_text(encoding="utf-8").strip().split("\n\n"):
        lines = block.splitlines()
        a, b = lines[1].split(" --> ")

        def ms(t):
            h, m, rest = t.split(":")
            sec, milli = rest.split(",")
            return (int(h) * 3600 + int(m) * 60 + int(sec)) * 1000 + int(milli)

        cues.append((ms(a), ms(b), lines[2]))
    return cues


async def _run_render(s, canvas, user, request_id="r1"):
    body = router.RenderRequest(base_revision=canvas.revision, client_request_id=request_id)
    response = await router.render_final(canvas.id, body, session=s, user=user)
    job = await s.get(GenerationJob, response["job_id"])
    await worker.execute_render_canvas(s, job)
    await s.refresh(job)
    return job


@requires_pg
def test_full_render_with_dubbing_subtitles_bgm_and_ducking(pg, storage):
    async def _run():
        async with pg() as s:
            owner, canvas = await _prepare(
                s, storage, bgm=True, settings={"fades": False, "bgm_volume_db": -12}
            )
            state = await router.get_final(canvas.id, session=s, user=owner)
            assert state["problems"] == [] and state["warnings"] == []
            assert state["total_ms"] == 3 * FIXTURE_SECONDS * 1000
            assert [(v["speaker"], v["start_ms"], v["end_ms"]) for v in state["timeline"]["voices"]] == [
                ("狐妖", 0, 1500),
                ("旁白", 1500, 2700),
                ("书生", 3000, 4000),
            ]
            assert state["timeline"]["bgm"]["name"] == "雪夜钢琴.wav"
            assert state["latest_render"] is None and state["stale"] is False
            job = await _run_render(s, canvas, owner)
            assert job.status == "succeeded", job.error
            final = await router.get_final(canvas.id, session=s, user=owner)
            return job, final

    job, final = asyncio.run(_run())
    latest = final["latest_render"]
    assert latest["status"] == "succeeded" and latest["progress"] == 100 and latest["url"]
    assert latest["input_hash"] == final["input_hash"] and final["stale"] is False
    video_key = _artifact_key(pg, latest["artifact_id"])
    out = storage / video_key
    info = _stream_info(out)
    assert info["codec"] == "h264" and "yuv420p" in info["pix_fmt"] and info["audio_codec"] == "aac"
    assert info["size"] == (1280, 720)
    assert abs(info["duration"] - 9.0) <= 0.2
    data = out.read_bytes()
    assert data.index(b"moov") < data.index(b"mdat")
    # 人声区间（0–2.7 秒）里 BGM 至少比无人声区间（5–8 秒）低 10 dB
    voiced = _mean_volume(out, 0.5, 2.0)
    free = _mean_volume(out, 5.0, 3.0)
    assert free - voiced >= 10, (voiced, free)
    # SRT 单独保存，起止与配音起止误差 ≤ 200ms
    srt_key = _artifact_key(pg, latest["srt_url"].split("/")[3])
    cues = _srt_cues(storage / srt_key)
    expected = [(0, 1500, "你终于来了。"), (1500, 2700, "雪落无声。"), (3000, 4000, "我等了很久。")]
    assert [c[2] for c in cues] == [e[2] for e in expected]
    for got, want in zip(cues, expected):
        assert abs(got[0] - want[0]) <= 200 and abs(got[1] - want[1]) <= 200


def _artifact_key(pg, artifact_id):
    async def _get():
        async with pg() as s:
            return (await s.get(Artifact, artifact_id)).storage_key

    return asyncio.run(_get())


@requires_pg
def test_render_without_bgm_has_audio_track_and_node_attribution(pg, storage):
    async def _run():
        async with pg() as s:
            owner, canvas = await _prepare(
                s, storage, settings={"burn_subtitles": False, "export_srt": False}
            )
            job = await _run_render(s, canvas, owner)
            assert job.status == "succeeded", job.error
            artifact = await s.get(Artifact, job.result["artifact_id"])
            return job, artifact

    job, artifact = asyncio.run(_run())
    assert artifact.type == "video" and artifact.mime_type == "video/mp4"
    assert artifact.artifact_metadata["canvas_node_id"] == "ep" and artifact.job_id == job.id
    assert job.result["srt_artifact_id"] is None  # export_srt 关闭且没有烧录失败：不单独导出
    info = _stream_info(storage / artifact.storage_key)
    assert info["audio_codec"] == "aac" and abs(info["duration"] - 9.0) <= 0.2


@requires_pg
def test_problems_missing_video_and_still_frame_fallback(pg, storage):
    async def _run():
        async with pg() as s:
            owner, canvas = await _prepare(s, storage, missing_video=3)
            # 镜头 3 只有首帧图片：用静帧代替，不阻止渲染
            _ff(
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=200x300:rate=1:duration=1",
                "-frames:v",
                "1",
                str(storage / "media/k3.png"),
            )
            await _add_result(s, canvas, "k3", "media/k3.png", "image")
            await s.commit()
            with_still = await router.get_final(canvas.id, session=s, user=owner)
            # 再去掉镜头 2 的视频：没有视频也没有首帧 → problem
            graph = json.loads(json.dumps(canvas.graph))
            for node in graph["nodes"]:
                if node["id"] == "v2":
                    node["id"] = "v2-new"
            for edge in graph["edges"]:
                if edge["target"] == "v2":
                    edge["target"] = "v2-new"
                if edge["source"] == "v2":
                    edge["source"] = "v2-new"
            _save_raw(canvas, graph)
            await s.commit()
            broken = await router.get_final(canvas.id, session=s, user=owner)
            response = await router.render_final(
                canvas.id,
                router.RenderRequest(base_revision=canvas.revision, client_request_id="x"),
                session=s,
                user=owner,
            )
            jobs = (
                await s.execute(
                    GenerationJob.__table__.select().where(GenerationJob.operation == "render_canvas")
                )
            ).all()
            return with_still, broken, response, jobs

    with_still, broken, response, jobs = asyncio.run(_run())
    assert with_still["problems"] == []
    assert [w["code"] for w in with_still["warnings"]] == ["still_frame"]
    assert with_still["timeline"]["shots"][2]["still"] is True
    assert [(p["code"], p["shot_index"]) for p in broken["problems"]] == [("missing_video", 1)]
    assert broken["problems"][0]["message"] == "镜头 2 还没有视频"
    assert response.status_code == 422
    body = json.loads(response.body)
    assert body["problems"][0]["shot_index"] == 1 and "镜头 2 还没有视频" in body["error"]["message"]
    assert jobs == []


@requires_pg
def test_voice_overflow_warns_and_truncates(pg, storage):
    async def _run():
        async with pg() as s:
            owner, canvas = await _prepare(s, storage)
            # 镜头 1 只有 3 秒，把三句配音拉长到 2.5 + 1.2 秒
            _silence(storage / "media/a1.wav", 2.5)
            a1 = (
                await s.execute(Artifact.__table__.select().where(Artifact.storage_key == "media/a1.wav"))
            ).first()
            (await s.get(Artifact, a1.id)).duration_ms = 2500
            await s.commit()
            return await router.get_final(canvas.id, session=s, user=owner)

    state = asyncio.run(_run())
    codes = [w["code"] for w in state["warnings"]]
    assert codes == ["voice_overflow"]
    assert "镜头 1" in state["warnings"][0]["message"]
    voices = [(v["start_ms"], v["end_ms"]) for v in state["timeline"]["voices"] if v["shot_index"] == 0]
    assert voices == [(0, 2500), (2500, 3000)]  # 超出镜头末尾的部分被截断
    cues = state["timeline"]["subtitles"]
    assert cues[1]["end_ms"] == 3000


@requires_pg
def test_stale_after_assets_change_and_step_status(pg, storage):
    async def _run():
        async with pg() as s:
            owner, canvas = await _prepare(s, storage, settings={"burn_subtitles": False})
            before = await overview.project_summary(s, owner, canvas.id)
            job = await _run_render(s, canvas, owner)
            assert job.status == "succeeded", job.error
            fresh = await router.get_final(canvas.id, session=s, user=owner)
            fresh_summary = await overview.project_summary(s, owner, canvas.id)
            # 镜头 1 新增一个视频结果（选用变化）→ 素材已变更
            _video(storage / "media/v1b.mp4", FIXTURE_SECONDS)
            newer = await _add_result(s, canvas, "v1", "media/v1b.mp4", "video", duration_ms=3000)
            graph = json.loads(json.dumps(canvas.graph))
            next(n for n in graph["nodes"] if n["id"] == "v1")["data"]["selected"] = newer.id
            _save_raw(canvas, graph)
            await s.commit()
            stale = await router.get_final(canvas.id, session=s, user=owner)
            stale_summary = await overview.project_summary(s, owner, canvas.id)
            return before, fresh, fresh_summary, stale, stale_summary

    before, fresh, fresh_summary, stale, stale_summary = asyncio.run(_run())
    final_status = lambda summary: next(step["status"] for step in summary["steps"] if step["key"] == "final")  # noqa: E731
    assert fresh["stale"] is False and stale["stale"] is True
    assert stale["latest_render"]["input_hash"] != stale["input_hash"]
    assert final_status(before) != "done"
    assert final_status(fresh_summary) == "done" and final_status(stale_summary) != "done"
    assert stale_summary["next_hint"] == "素材有变化，需要重新渲染"


def test_final_step_active_when_previous_steps_done():
    steps = overview._steps(3, 3, True, [], voice=(0, 0))
    assert [(s["key"], s["status"]) for s in steps] == [
        ("script", "done"),
        ("characters", "done"),
        ("storyboard", "done"),
        ("voice", "done"),
        ("final", "active"),
    ]
    done = overview._steps(3, 3, True, [], voice=(0, 0), render="fresh")
    assert done[-1]["status"] == "done"
    assert overview._steps(3, 3, True, [], voice=(0, 0), render="stale")[-1]["status"] == "active"
    assert overview._next_hint(0, 0, 3, 3, final_state="ready") == "可以导出成片了"
    assert overview._next_hint(0, 0, 3, 3, final_state="stale") == "素材有变化，需要重新渲染"


@requires_pg
def test_render_is_idempotent_by_client_request_id(pg, storage):
    async def _run():
        async with pg() as s:
            owner, canvas = await _prepare(s, storage)

            async def go(request_id):
                body = router.RenderRequest(base_revision=canvas.revision, client_request_id=request_id)
                result = await router.render_final(canvas.id, body, session=s, user=owner)
                return result["job_id"]

            first, again, other = await go("same"), await go("same"), await go("different")
            job = await s.get(GenerationJob, first)
            return first, again, other, job

    first, again, other, job = asyncio.run(_run())
    assert first == again and other != first
    assert job.operation == "render_canvas" and job.worker_class == "render"
    assert job.params["canvas_node_id"] == "ep" and job.params["input_hash"] and job.params["spec"]


@requires_pg
def test_stale_revision_conflicts_and_failure_has_chinese_reason(pg, storage):
    async def _run():
        async with pg() as s:
            owner, canvas = await _prepare(s, storage)
            with pytest.raises(HTTPException) as conflict:
                await router.render_final(
                    canvas.id, router.RenderRequest(base_revision=canvas.revision - 1), session=s, user=owner
                )
            # 渲染前镜头 3 的视频文件被删：任务失败，原因是中文，且不会自动重试
            (storage / "media/v3.mp4").unlink()
            job = await _run_render(s, canvas, owner, "gone")
            state = await router.get_final(canvas.id, session=s, user=owner)
            return conflict.value.status_code, job, state

    status, job, state = asyncio.run(_run())
    assert status == 409
    assert job.status == "failed" and job.error == "镜头 3 的视频文件不存在"
    assert state["latest_render"]["status"] == "failed"
    assert state["latest_render"]["error"] == "镜头 3 的视频文件不存在"
    assert state["last_success"] is None and state["stale"] is False


@requires_pg
def test_ensure_node_creates_once_right_of_shots(pg, storage):
    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await canvas_service.create_canvas(s, owner, title="t")
            graph = _graph(with_audio=False)
            graph["nodes"] = [n for n in graph["nodes"] if n["type"] != "episode"]
            _save_raw(canvas, graph)
            await s.commit()
            before = canvas.revision
            with pytest.raises(HTTPException) as conflict:
                await router.ensure_node(
                    canvas.id, router.EnsureNode(base_revision=before - 1), session=s, user=owner
                )
            await s.refresh(canvas)  # 409 路径回滚过，属性已过期
            await s.refresh(owner)
            first = await router.ensure_node(
                canvas.id, router.EnsureNode(base_revision=before), session=s, user=owner
            )
            second = await router.ensure_node(
                canvas.id, router.EnsureNode(base_revision=before), session=s, user=owner
            )
            episode = next(n for n in canvas.graph["nodes"] if n["type"] == "episode")
            return before, conflict.value.status_code, first, second, episode, canvas.graph["nodes"]

    before, status, first, second, episode, nodes = asyncio.run(_run())
    assert status == 409
    assert first["revision"] == before + 1 and second == first
    assert episode["id"] == first["episode_node_id"] and episode["data"]["resolution"] == "720p"
    assert episode["position"]["x"] > max(n["position"]["x"] for n in nodes if n["type"] != "episode")
    assert sum(n["type"] == "episode" for n in nodes) == 1


@requires_pg
def test_episode_node_validation(pg, storage):
    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await canvas_service.create_canvas(s, owner, title="t")
            base = [_n("v1", "video"), _n("ep", "episode", **canvas_service.EPISODE_DEFAULTS)]
            ok = await canvas_service.save_graph(s, canvas, {"nodes": base, "edges": []}, canvas.revision)
            errors = {}

            async def attempt(name, nodes, edges=()):
                try:
                    await canvas_service.save_graph(
                        s, canvas, {"nodes": nodes, "edges": list(edges)}, canvas.revision
                    )
                except canvas_service.CanvasError as error:
                    errors[name] = str(error)

            def ep(**data):
                return _n("ep", "episode", **{**canvas_service.EPISODE_DEFAULTS, **data})

            await attempt("two", [*base, _n("ep2", "episode")])
            await attempt("in_edge", base, [_e("v1", "ep", "prompt")])
            await attempt("out_edge", base, [_e("ep", "v1", "prompt")])
            await attempt("volume_high", [_n("v1", "video"), ep(bgm_volume_db=3)])
            await attempt("volume_low", [_n("v1", "video"), ep(bgm_volume_db=-41)])
            await attempt("resolution", [_n("v1", "video"), ep(resolution="4k")])
            await attempt("flag", [_n("v1", "video"), ep(ducking="yes")])
            await attempt("bgm", [_n("v1", "video"), ep(bgm_artifact_id="missing")])
            await attempt("edge_ok_when_not_episode", base, [_e("v1", "v1", "prompt")])
            good = await canvas_service.save_graph(
                s,
                canvas,
                {"nodes": [_n("v1", "video"), ep(bgm_volume_db=-40, resolution="1080p")], "edges": []},
                canvas.revision,
            )
            return ok.revision, errors, good.revision

    _, errors, good = asyncio.run(_run())
    assert "只能有一个成片节点" in errors["two"]
    assert "不接受连线" in errors["in_edge"] and "不接受连线" in errors["out_edge"]
    assert "-40 到 0" in errors["volume_high"] and "-40 到 0" in errors["volume_low"]
    assert "720p / 1080p" in errors["resolution"] and "开关" in errors["flag"]
    assert "背景音乐不属于" in errors["bgm"]
    assert good >= 2


# ── 权限与 BGM 上传 ────────────────────────────────────────────────────────


def _upload(data: bytes, name="bgm.mp3"):
    return UploadFile(file=io.BytesIO(data), filename=name)


@requires_pg
def test_foreign_canvas_is_404_for_every_endpoint(pg, storage):
    async def _run():
        async with pg() as s:
            owner, other = await _user(s, "owner"), await _user(s, "other")
            canvas = await canvas_service.create_canvas(s, owner, title="t")
            calls = [
                router.get_final(canvas.id, session=s, user=other),
                router.ensure_node(canvas.id, router.EnsureNode(base_revision=1), session=s, user=other),
                router.render_final(canvas.id, router.RenderRequest(base_revision=1), session=s, user=other),
                router.upload_bgm(canvas.id, file=_upload(b"ID3" + b"\0" * 64), session=s, user=other),
                router.get_final("missing", session=s, user=owner),
            ]
            codes = []
            for call in calls:
                try:
                    await call
                except HTTPException as error:
                    codes.append(error.status_code)
            return codes

    assert asyncio.run(_run()) == [404] * 5


def test_sniff_audio_by_content_not_name():
    assert render_service.sniff_audio(b"RIFF\0\0\0\0WAVEfmt ")[0] == "wav"
    assert render_service.sniff_audio(b"\0\0\0\x20ftypM4A \0\0\0\0")[0] == "m4a"
    assert render_service.sniff_audio(b"ID3\x04\0\0")[0] == "mp3"
    assert render_service.sniff_audio(b"\xff\xfb\x90\x00")[0] == "mp3"
    assert render_service.sniff_audio(b"\xff\xf1\x50\x80")[0] == "aac"
    assert render_service.sniff_audio(b"\x89PNG\r\n\x1a\n") is None
    assert render_service.sniff_audio(b"<html>fake.mp3</html>") is None


@requires_pg
def test_bgm_upload_sniffs_content_and_limits_size(pg, storage):
    wav = storage / "tone.wav"
    mp3 = storage / "tone.mp3"
    m4a = storage / "tone.m4a"
    _tone(wav, 1.2)
    _tone(mp3, 1.2)
    _tone(m4a, 1.2)
    video = storage / "clip.mp4"
    _video(video, 1)

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await canvas_service.create_canvas(s, owner, title="t")
            ok = {}
            for path in (wav, mp3, m4a):
                # 文件名故意写错：按内容判断，Content-Type 不参与
                ok[path.suffix] = await router.upload_bgm(
                    canvas.id, file=_upload(path.read_bytes(), "我的歌.png"), session=s, user=owner
                )
            artifact = await s.get(Artifact, ok[".wav"]["artifact_id"])
            bad = {}
            for name, data in {
                "png": b"\x89PNG\r\n\x1a\n" + b"\0" * 64,
                "text": "hello".encode(),
                "empty": b"",
                "video": video.read_bytes(),
                "toobig": b"ID3" + b"\0" * (20 * 1024 * 1024),
                "corrupt": b"ID3\x04\0\0\0\0\0\0" + b"\x01" * 64,
            }.items():
                try:
                    await router.upload_bgm(canvas.id, file=_upload(data, "a.mp3"), session=s, user=owner)
                    bad[name] = None
                except HTTPException as error:
                    bad[name] = (error.status_code, error.detail)
            return ok, artifact, bad

    ok, artifact, bad = asyncio.run(_run())
    for item in ok.values():
        assert item["name"] == "我的歌.png" and abs(item["duration_ms"] - 1200) <= 150
    assert artifact.type == "audio" and artifact.mime_type == "audio/wav"
    assert (
        artifact.artifact_metadata["source"] == "bgm_upload"
        and artifact.artifact_metadata["filename"] == "我的歌.png"
    )
    assert (storage / artifact.storage_key).is_file()
    for name in ("png", "text", "empty", "video", "corrupt"):
        assert bad[name] is not None and bad[name][0] == 422, name
    assert bad["toobig"] == (422, "背景音乐不能超过 20MB")


async def test_spec_validates_as_timeline():
    """build_spec 产出的结构能通过 TimelineSpec 校验（轻量检查：不依赖数据库）。"""
    spec = {
        "aspect_ratio": "4:3",
        "video_tracks": [
            {
                "id": "video",
                "clips": [dict(id="s1", artifact_id="a", source_out_ms=1000, timeline_start_ms=0)],
            }
        ],
        "audio_tracks": [
            {
                "clips": [
                    dict(
                        id="bgm",
                        kind="bgm",
                        artifact_id="b",
                        timeline_start_ms=0,
                        volume_keyframes=[{"time_ms": 0, "volume_db": -18.0}],
                        ducking={"enabled": True, "duck_db": 14},
                        fade_in_ms=1500,
                        fade_out_ms=1500,
                    )
                ]
            }
        ],
    }
    assert timeline_service.validate_spec(spec)["aspect_ratio"] == "4:3"


@requires_pg
def test_summary_final_step_active_through_real_entry(pg, storage):
    """真实入口 project_summary：配音完成（或没有配音）且分镜完成后，成片进入 active。"""

    async def _run():
        async with pg() as s:
            owner = await _user(s)
            canvas = await canvas_service.create_canvas(s, owner, title="t", idea="想法")
            graph = {
                "nodes": [
                    canvas.graph["nodes"][0],
                    _n("v1", "video", 400, 0, duration=3),
                    _n("a1", "audio", 800, 0, speaker="狐妖", text="你好"),
                ],
                "edges": [_e("a1", "v1", "voice")],
            }
            _save_raw(canvas, graph)
            await _add_result(s, canvas, "v1", "media/v1.mp4", "video", duration_ms=3000)
            await s.commit()

            async def final_status():
                summary = await overview.project_summary(s, owner, canvas.id)
                return next(x["status"] for x in summary["steps"] if x["key"] == "final"), summary

            pending, _ = await final_status()  # 有配音节点还没结果
            await _add_result(s, canvas, "a1", "media/a1.wav", "audio", duration_ms=1000, via="job")
            await s.commit()
            voiced, summary = await final_status()
            return pending, voiced, summary

    pending, voiced, summary = asyncio.run(_run())
    assert pending == "todo" and voiced == "active"
    assert summary["next_hint"] == "可以导出成片了" and summary["active_step"] == "final"


@requires_pg
def test_retry_with_same_request_id_reuses_job_even_if_assets_changed(pg, storage):
    async def _run():
        async with pg() as s:
            owner, canvas = await _prepare(s, storage)
            body = router.RenderRequest(base_revision=canvas.revision, client_request_id="same")
            first = await router.render_final(canvas.id, body, session=s, user=owner)
            # 响应丢失期间镜头 1 又生成了新视频（没有显式选用，最新结果会变）→ 重新计算的 input_hash 不同
            _video(storage / "media/v1c.mp4", FIXTURE_SECONDS)
            await _add_result(s, canvas, "v1", "media/v1c.mp4", "video", duration_ms=3000)
            await s.commit()
            again = await router.render_final(canvas.id, body, session=s, user=owner)
            count = len(
                (
                    await s.execute(
                        GenerationJob.__table__.select().where(GenerationJob.operation == "render_canvas")
                    )
                ).all()
            )
            return first["job_id"], again["job_id"], count

    first, again, count = asyncio.run(_run())
    assert first == again and count == 1
