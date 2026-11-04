"""画布成片的 AIGC 标识（显式角标 + 隐式元数据）与出片检查：ffmpeg 实跑，不调用任何厂商。"""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select
from test_canvas_render import (  # noqa: F401  (storage / stub_artifacts 是 fixture)
    _art,
    _ff,
    _prepare,
    _run_render,
    _session_for,
    _silence,
    _tone,
    _video,
    storage,
    stub_artifacts,
)
from test_node_canvas import pg, requires_pg  # noqa: F401  (pg 是 fixture)

from app.db.models import Artifact
from app.routers import canvas_render as router
from app.services import render_qc
from app.services import timeline_renderer as tr
from app.services import workspace_overview_service as overview
from app.worker import generation_worker as worker

META = tr.build_aigc_metadata("job-1", "hash-1")
META_JSON = json.dumps(META, ensure_ascii=False, separators=(",", ":"))


def _mp4(
    path: Path,
    *,
    video_s=4.0,
    audio_s=4.0,
    meta=True,
    black=None,
    mute=None,
    still=False,
):
    """合成测试成片：video_s 秒画面 + audio_s 秒声音（audio_s=0 表示没有声音轨）。

    black=(起, 止) 秒：该段画面涂黑；mute=(起, 止) 秒：该段声音静音；still=True：整段画面是静帧。
    """
    args = ["-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=24:duration={video_s}"]
    vf = "format=yuv420p"
    if still:
        args = ["-f", "lavfi", "-i", f"color=c=0x336699:size=320x180:rate=24:duration={video_s}"]
    if black:
        vf += f",drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,{black[0]},{black[1]})'"
    maps = ["-map", "0:v"]
    if audio_s:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={audio_s}"]
        if mute:
            args += ["-af", f"volume=enable='between(t,{mute[0]},{mute[1]})':volume=0"]
        maps += ["-map", "1:a"]
    out = ["-vf", vf, *maps, "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio_s:
        out += ["-c:a", "aac"]
    if meta:
        out += ["-metadata", f"comment={META_JSON}", "-metadata", f"aigc={META_JSON}"]
    out += ["-movflags", "use_metadata_tags+faststart", str(path)]
    _ff(*args, *out)
    return path


def _check(path, *, total_ms=4000, audio=((0, 4000),), expects_audio=True, still=False, **kwargs):
    shots = [{"offset_ms": 0, "duration_ms": total_ms, "placeholder": False, "still": still}]
    return render_qc.check_render(
        path,
        total_ms=total_ms,
        shots=shots,
        audio_ranges=list(audio),
        expects_audio=expects_audio,
        badge_applied=kwargs.pop("badge_applied", True),
        **kwargs,
    )


def _codes(report, level=None):
    return {i["code"] for i in report["issues"] if level is None or i["level"] == level}


# ── 检查项与分级 ───────────────────────────────────────────────────────────


def test_normal_clip_passes(tmp_path):
    report = _check(_mp4(tmp_path / "ok.mp4"))
    assert report["status"] == "passed", report["issues"]
    checks = report["checks"]
    assert checks["playable"]["status"] == "passed" and checks["has_audio"]["status"] == "passed"
    assert abs(checks["av_duration_diff_ms"]["value"]) <= 200
    assert checks["duration_match"]["status"] == "passed" and checks["missing_shots"]["status"] == "passed"
    assert checks["aigc_label"]["status"] == "passed"
    assert checks["loudness_lufs"]["status"] == "info" and checks["loudness_lufs"]["value"] is not None


def test_black_segment_is_a_warning_with_timestamps(tmp_path):
    report = _check(_mp4(tmp_path / "black.mp4", black=(1, 2)))
    assert report["status"] == "warning" and _codes(report) == {"black_segments"}
    (issue,) = report["issues"]
    assert issue["level"] == "warning" and issue["message"] == "黑屏 1.0 秒"
    assert abs(issue["start_ms"] - 1000) <= 100 and abs(issue["end_ms"] - 2000) <= 100


def test_av_duration_diff_blocks_with_chinese_reason(tmp_path):
    report = _check(_mp4(tmp_path / "av.mp4", video_s=4, audio_s=5))
    assert report["status"] == "failed"
    diff = report["checks"]["av_duration_diff_ms"]
    assert diff["status"] == "failed" and 800 <= diff["value"] <= 1100
    assert any(i["message"].startswith("声音比画面长 ") for i in report["issues"])
    assert "duration_match" in _codes(report, "blocking")  # 容器时长也比时间线长 1 秒


def test_duration_mismatch_with_edl_blocks(tmp_path):
    report = _check(_mp4(tmp_path / "short.mp4", video_s=3, audio_s=3), total_ms=4000)
    assert report["status"] == "failed" and "duration_match" in _codes(report, "blocking")
    assert report["checks"]["av_duration_diff_ms"]["status"] == "passed"


def test_missing_audio_track_blocks_only_when_timeline_has_audio(tmp_path):
    path = _mp4(tmp_path / "noaudio.mp4", audio_s=0)
    blocked = _check(path, expects_audio=True)
    assert blocked["status"] == "failed" and "has_audio" in _codes(blocked, "blocking")
    fine = _check(path, expects_audio=False, audio=())
    assert fine["checks"]["has_audio"]["status"] == "passed"


def test_truncated_file_is_not_playable(tmp_path):
    good = _mp4(tmp_path / "good.mp4")
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(good.read_bytes()[: good.stat().st_size // 2])
    report = _check(bad)
    assert report["status"] == "failed" and report["checks"]["playable"]["status"] == "failed"
    assert "视频无法完整解码，请重新渲染" in {i["message"] for i in report["issues"]}
    assert report["checks"]["black_segments"]["status"] == "skipped"


def test_still_shot_is_excluded_from_freeze_detection(tmp_path):
    path = _mp4(tmp_path / "still.mp4", still=True)
    assert _check(path, still=True)["status"] == "passed"
    # 对照：同一个文件如果不是静帧镜头，冻帧要被报出来
    control = _check(path, still=False)
    assert control["status"] == "warning" and _codes(control) == {"frozen_segments"}
    assert control["issues"][0]["message"].startswith("画面静止 ")


def test_silence_inside_audio_warns_but_gaps_without_audio_are_excluded(tmp_path):
    path = _mp4(tmp_path / "mute.mp4", mute=(1, 4))
    warned = _check(path, audio=[(0, 4000)])
    assert warned["status"] == "warning" and _codes(warned) == {"silent_segments"}
    # 1–4 秒时间线上本来就没有音频（声音只覆盖 0–1 秒）：不算静音
    excluded = _check(path, audio=[(0, 1000)])
    assert excluded["status"] == "passed" and excluded["checks"]["silent_segments"]["segments"] == []


def test_timeline_without_any_audio_only_notes_and_does_not_fail(tmp_path):
    path = tmp_path / "silent.mp4"
    _ff(
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=4",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-map", "0:v", "-map", "1:a", "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-metadata", f"comment={META_JSON}", "-metadata", f"aigc={META_JSON}",
        "-movflags", "use_metadata_tags+faststart", str(path),
    )  # fmt: skip
    report = _check(path, audio=(), expects_audio=False)
    assert report["status"] == "passed" and report["issues"] == []
    assert report["notes"] == ["时间线上没有配音和背景音乐，成片是静音的"]
    assert report["checks"]["silent_segments"]["no_audio"] is True


def test_missing_aigc_metadata_or_badge_blocks(tmp_path):
    bare = _check(_mp4(tmp_path / "bare.mp4", meta=False))
    assert bare["status"] == "failed" and bare["checks"]["aigc_label"]["metadata"] is False
    no_badge = _check(_mp4(tmp_path / "meta.mp4"), badge_applied=False)
    assert no_badge["status"] == "failed" and no_badge["checks"]["aigc_label"]["badge"] is False


def test_missing_shots_from_manifest_block(tmp_path):
    report = render_qc.check_render(
        _mp4(tmp_path / "m.mp4"),
        total_ms=4000,
        shots=[{"offset_ms": 0, "duration_ms": 4000, "placeholder": True}],
        audio_ranges=[(0, 4000)],
        expects_audio=True,
        badge_applied=True,
    )
    assert report["status"] == "failed" and "镜头 1 的视频素材缺失" in {
        i["message"] for i in report["issues"]
    }


def test_subtitle_overflow_is_a_warning_from_the_edl_only():
    cues = [
        {"start_ms": 0, "end_ms": 1000, "text": "短句"},
        {"start_ms": 3500, "end_ms": 4500, "text": "超出时间线"},
        {"start_ms": 1000, "end_ms": 2000, "text": "一\n二\n三"},
        {"start_ms": 2000, "end_ms": 3000, "text": "字" * 41},
    ]
    items = render_qc.check_subtitles(cues, 4000)
    assert [i["start_ms"] for i in items] == [3500, 1000, 2000]
    assert all(i["level"] == "warning" and i["code"] == "subtitle_overflow" for i in items)


def test_audio_coverage_and_interval_helpers():
    spec = {
        "audio_tracks": [
            {"clips": [{"timeline_start_ms": 0, "source_out_ms": 1500}, {"timeline_start_ms": 1500, "source_out_ms": 1200}]},
            {"clips": [{"kind": "bgm", "timeline_start_ms": 5000}]},
        ]
    }  # fmt: skip
    assert render_qc.audio_coverage(spec, 9000) == [(0, 2700), (5000, 9000)]
    assert render_qc.audio_coverage({}, 9000) == []
    assert render_qc._subtract([(0, 10)], [(2, 4), (8, 20)]) == [(0, 2), (4, 8)]
    assert render_qc._intersect([(0, 10)], [(2, 4), (8, 20)]) == [(2, 4), (8, 10)]


def test_error_report_does_not_block():
    report = render_qc.error_report("ffmpeg 超时")
    assert report["status"] == "error" and report["issues"] == []


# ── AIGC 标识：隐式元数据 + 显式角标 ───────────────────────────────────────


def _spec(aspect, seconds=1.0, cues=()):
    spec = {
        "aspect_ratio": aspect,
        "video_tracks": [
            {
                "clips": [
                    dict(id="c1", artifact_id="a", source_out_ms=int(seconds * 1000), timeline_start_ms=0)
                ]
            }
        ],
    }
    if cues:
        spec["subtitle_tracks"] = [{"id": "s", "cues": list(cues)}]
    return spec


def _gray_frame(path: Path, width: int, height: int) -> bytes:
    proc = subprocess.run(
        [tr._ffmpeg_exe(), "-v", "error", "-ss", "0.4", "-i", str(path), "-frames:v", "1"]
        + ["-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True,
        check=True,
    )
    assert len(proc.stdout) == width * height
    return proc.stdout


def _region_diff(a: bytes, b: bytes, width: int, box: tuple[int, int, int, int]) -> float:
    x0, y0, x1, y1 = box
    total = count = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            total += abs(a[y * width + x] - b[y * width + x])
            count += 1
    return total / count


def test_badge_geometry_scales_with_short_side():
    assert tr.badge_geometry(720, 1280) == (46, 22)
    assert tr.badge_geometry(1280, 720) == (46, 22)
    assert tr.badge_geometry(1080, 1920) == (70, 32)
    for width, height in ((720, 1280), (1280, 720), (1080, 1920), (1920, 1080)):
        badge_h, margin = tr.badge_geometry(width, height)
        assert 0.06 <= badge_h / min(width, height) <= 0.08 and margin > 0


def test_badge_filter_and_metadata_shape():
    flt = tr.build_badge_filter("[0:v]", 2, 720, 1280)
    assert flt == "[2:v]scale=-2:46[badge];[0:v][badge]overlay=x=W-w-22:y=22:format=auto[vout]"
    assert tr.AIGC_BADGE_PATH.is_file()
    assert list(META["AIGC"]) == [
        "Label", "ContentProducer", "ProduceID", "ReservedCode1",
        "ContentPropagator", "PropagateID", "ReservedCode2",
    ]  # fmt: skip
    assert META["AIGC"]["Label"] == "1" and META["AIGC"]["ContentProducer"] == "FishStudio"


@pytest.mark.parametrize(
    "aspect,short_side",
    [("9:16", 720), ("16:9", 720), ("9:16", 1080), ("16:9", 1080)],
)
async def test_render_overlays_badge_top_right_and_writes_metadata(
    storage, stub_artifacts, monkeypatch, aspect, short_side
):
    _video(storage / "media/a.mp4", 1)
    arts = [_art("a", "media/a.mp4")]
    commands = []
    original = tr._run_ffmpeg

    def spy(args, timeout=600):
        commands.append(list(args))
        return original(args, timeout)

    monkeypatch.setattr(tr, "_run_ffmpeg", spy)
    kwargs = dict(
        project_id="p", spec=_spec(aspect), burn_subtitles=False, short_side=short_side, job_id="job-xyz"
    )
    labeled = await tr.render_timeline(
        _session_for(arts), aigc_label=True, aigc_reserved_code="hash-abc", **kwargs
    )
    plain = await tr.render_timeline(_session_for(arts), **kwargs)

    # 命令里有 overlay 和角标路径
    encode = next(c for c in commands if str(tr.AIGC_BADGE_PATH) in c and "-filter_complex" in c)
    assert "overlay=" in encode[encode.index("-filter_complex") + 1]
    assert "use_metadata_tags+faststart" in encode
    assert labeled["aigc"]["badge"] is True and plain["aigc"]["badge"] is False

    # 隐式标识：comment 与 aigc 标签都能读回，ProduceID / ReservedCode1 正确
    out = storage / labeled["storage_key"]
    tags = render_qc.read_container(out)["tags"]
    for key in ("comment", "aigc"):
        label = json.loads(tags[key])["AIGC"]
        assert label["Label"] == "1" and label["ContentProducer"] == "FishStudio"
        assert label["ProduceID"] == "job-xyz" and label["ReservedCode1"] == "hash-abc"
    assert tags["comment"] == tags["aigc"]
    assert "aigc" not in render_qc.read_container(storage / plain["storage_key"])["tags"]

    # 显式标识：右上角像素差明显，左下角（字幕区）几乎没变
    width, height = tr.output_size(aspect, short_side)
    badge_h, margin = tr.badge_geometry(width, height)
    badge_w = round(badge_h * 2.6)
    with_badge = _gray_frame(out, width, height)
    without = _gray_frame(storage / plain["storage_key"], width, height)
    top_right = (width - margin - badge_w, margin, width - margin, margin + badge_h)
    assert _region_diff(with_badge, without, width, top_right) > 12
    bottom_left = (0, height - badge_h * 2, badge_w, height)
    assert _region_diff(with_badge, without, width, bottom_left) < 3


async def test_badge_survives_subtitle_burn_and_burn_fallback(storage, stub_artifacts, monkeypatch):
    _video(storage / "media/a.mp4", 1)
    arts = [_art("a", "media/a.mp4")]
    cues = [{"id": "s1", "start_ms": 0, "end_ms": 900, "text": "你好"}]
    seen = []
    original = tr._run_ffmpeg

    def spy(args, timeout=600):
        seen.append(list(args))
        if "subtitles=" in " ".join(args) and fail_burn:
            raise RuntimeError("no libass")
        return original(args, timeout)

    monkeypatch.setattr(tr, "_run_ffmpeg", spy)
    for fail_burn in (False, True):
        seen.clear()
        result = await tr.render_timeline(
            _session_for(arts), project_id="p", spec=_spec("16:9", cues=cues), aigc_label=True, job_id="j"
        )
        assert result["subtitles_burned"] is (not fail_burn)
        encodes = [c for c in seen if str(tr.AIGC_BADGE_PATH) in c]
        assert encodes and all("overlay=" in c[c.index("-filter_complex") + 1] for c in encodes)
        tags = render_qc.read_container(storage / result["storage_key"])["tags"]
        assert json.loads(tags["aigc"])["AIGC"]["Label"] == "1"
    # 烧录失败退化时 SRT 照常保留，第一条字幕时间不受影响
    srt = next(Path(storage / "videos").glob("*_sub.srt")).read_text(encoding="utf-8")
    assert srt.startswith("1\n00:00:00,000 --> 00:00:00,900\n你好")


async def test_render_without_audio_still_gets_badge_index_right(storage, stub_artifacts):
    """没有任何音频时多一个 anullsrc 输入，角标输入序号要跟着后移。"""
    _video(storage / "media/a.mp4", 1)
    result = await tr.render_timeline(
        _session_for([_art("a", "media/a.mp4")]),
        project_id="p",
        spec=_spec("9:16"),
        aigc_label=True,
        burn_subtitles=False,
    )
    assert render_qc.read_container(storage / result["storage_key"])["has_audio"] is True
    assert result["aigc"]["badge"] is True


# ── 数据库场景：worker 写 qc、GET /final、步骤状态 ──────────────────────────


def _step(summary, key):
    return next(step["status"] for step in summary["steps"] if step["key"] == key)


@requires_pg
def test_worker_runs_qc_and_final_exposes_it(pg, storage):
    async def _run():
        async with pg() as s:
            owner, canvas = await _prepare(s, storage, settings={"burn_subtitles": False})
            job = await _run_render(s, canvas, owner)
            assert job.status == "succeeded", job.error
            final = await router.get_final(canvas.id, session=s, user=owner)
            artifact = await s.get(Artifact, job.result["artifact_id"])
            summary = await overview.project_summary(s, owner, canvas.id)
            return job, final, artifact, summary

    job, final, artifact, summary = asyncio.run(_run())
    qc = job.result["qc"]
    assert qc["status"] in ("passed", "warning"), qc["issues"]
    assert qc["checks"]["aigc_label"]["status"] == "passed" and qc["checks"]["playable"]["status"] == "passed"
    assert artifact.artifact_metadata["qc"] == qc and artifact.artifact_metadata["aigc_label"] is True
    assert final["latest_render"]["qc"] == qc and final["last_success"]["qc"] == qc
    assert _step(summary, "final") == "done"


@requires_pg
def test_failed_qc_keeps_final_step_open_with_reason(pg, storage, monkeypatch):
    failed = {
        "version": 1,
        "status": "failed",
        "checks": {},
        "notes": [],
        "issues": [{"code": "av_duration_diff_ms", "level": "blocking", "message": "声音比画面长 0.8 秒"}],
    }
    monkeypatch.setattr(render_qc, "check_render", lambda *a, **k: failed)

    async def _run():
        async with pg() as s:
            owner, canvas = await _prepare(s, storage, settings={"burn_subtitles": False})
            # 镜头 1 的视频是任务产物：选定后才不会出现“选图”提示抢在成片提示前面
            first = (
                await s.execute(select(Artifact).where(Artifact.storage_key == "media/v1.mp4"))
            ).scalar_one()
            graph = json.loads(json.dumps(canvas.graph))
            next(n for n in graph["nodes"] if n["id"] == "v1")["data"]["selected"] = first.id
            canvas.graph = graph
            canvas.revision += 1
            await s.commit()
            job = await _run_render(s, canvas, owner)
            final = await router.get_final(canvas.id, session=s, user=owner)
            return (
                job,
                final,
                await overview.project_summary(s, owner, canvas.id),
                await overview.overview(s, owner),
            )

    job, final, summary, board = asyncio.run(_run())
    assert job.status == "succeeded" and final["last_success"]["qc"]["status"] == "failed"
    assert final["stale"] is False
    assert _step(summary, "final") != "done" and _step(summary, "final") == "active"
    assert summary["next_hint"] == "出片检查没通过：声音比画面长 0.8 秒"
    assert board["projects"][0]["next_hint"] == "出片检查没通过：声音比画面长 0.8 秒"


@requires_pg
def test_qc_crash_becomes_error_status_and_does_not_fail_the_job(pg, storage, monkeypatch):
    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffmpeg", 1)

    monkeypatch.setattr(render_qc, "check_render", boom)

    async def _run():
        async with pg() as s:
            owner, canvas = await _prepare(s, storage, settings={"burn_subtitles": False})
            job = await _run_render(s, canvas, owner)
            summary = await overview.project_summary(s, owner, canvas.id)
            return job, summary

    job, summary = asyncio.run(_run())
    assert job.status == "succeeded" and job.result["qc"]["status"] == "error"
    assert "出片检查" in job.result["qc"]["error"]
    assert _step(summary, "final") == "done"  # error 按提醒处理，不阻止


@requires_pg
def test_render_before_qc_existed_has_no_qc_and_is_not_blocked(pg, storage):
    async def _run():
        async with pg() as s:
            owner, canvas = await _prepare(s, storage, settings={"burn_subtitles": False})
            job = await _run_render(s, canvas, owner)
            result = dict(job.result)
            result.pop("qc")
            job.result = result  # 模拟本功能上线前产生的渲染结果
            await s.commit()
            final = await router.get_final(canvas.id, session=s, user=owner)
            return final, await overview.project_summary(s, owner, canvas.id)

    final, summary = asyncio.run(_run())
    assert final["last_success"]["url"] and final["last_success"]["qc"] is None
    assert _step(summary, "final") == "done" and worker  # 旧渲染不阻止


# ── 复核补充：包时间戳测量 / 执行故障归 error / 保存点 / stale + failed ──────


def _clip_ends(path):
    return render_qc._stream_end_ms(path, "v"), render_qc._stream_end_ms(path, "a")


def test_stream_end_uses_packet_timestamps_without_extra_frame(tmp_path):
    path = _mp4(tmp_path / "exact.mp4", video_s=4, audio_s=4)
    video_end, audio_end = _clip_ends(path)
    assert abs(video_end - 4000) <= 1  # 4 秒 24fps：不能多算一帧（42ms）
    assert abs(audio_end - 4000) <= 30  # AAC 帧约 23ms


def test_av_diff_boundary_150ms_passes_and_300ms_fails(tmp_path):
    near = _check(_mp4(tmp_path / "d150.mp4", video_s=4, audio_s=4.15), total_ms=4000)
    assert near["checks"]["av_duration_diff_ms"]["status"] == "passed", near["checks"]["av_duration_diff_ms"]
    assert 100 <= near["checks"]["av_duration_diff_ms"]["value"] <= 200
    far = _check(_mp4(tmp_path / "d300.mp4", video_s=4, audio_s=4.3), total_ms=4000)
    assert far["checks"]["av_duration_diff_ms"]["status"] == "failed"
    assert 250 <= far["checks"]["av_duration_diff_ms"]["value"] <= 360
    assert any(i["message"].startswith("声音比画面长 0.3 秒") for i in far["issues"])


def test_decode_failure_is_split_into_corrupt_file_and_execution_failure(monkeypatch):
    path = Path("x.mp4")
    for text, code in (("Invalid data found when processing input", 1), ("moov atom not found", 1)):
        monkeypatch.setattr(render_qc, "_ffmpeg", lambda *a, text=text, code=code, **k: (text, code))
        assert render_qc._decodes_fully(path)[0] is False
    for text, code in (("", -9), ("", 137), ("", 1), ("Cannot allocate memory", 1), ("something odd", 1)):
        monkeypatch.setattr(render_qc, "_ffmpeg", lambda *a, text=text, code=code, **k: (text, code))
        with pytest.raises(render_qc.QcExecError):
            render_qc._decodes_fully(path)


def test_evidence_with_nonzero_exit_or_missing_summary_is_an_error(tmp_path, monkeypatch):
    path = _mp4(tmp_path / "ok.mp4")
    monkeypatch.setattr(render_qc, "_ffmpeg", lambda *a, **k: ("black_start:1 black_end:2", 1))
    with pytest.raises(render_qc.QcExecError):
        render_qc._evidence(path, 4000)
    monkeypatch.setattr(render_qc, "_ffmpeg", lambda *a, **k: ("no events and no summary", 0))
    with pytest.raises(render_qc.QcExecError):
        render_qc._evidence(path, 4000)
    # 整次检查：内容检测故障 → check_render 抛 QcExecError（worker 记为 error），不会变成“全部通过”
    monkeypatch.undo()
    monkeypatch.setattr(
        render_qc, "_evidence", lambda *a, **k: (_ for _ in ()).throw(render_qc.QcExecError("x"))
    )
    with pytest.raises(render_qc.QcExecError):
        _check(path)


def test_track_probe_failure_is_an_error_not_a_pass(tmp_path, monkeypatch):
    path = _mp4(tmp_path / "ok.mp4")
    monkeypatch.setattr(render_qc, "_ffmpeg_raw", lambda *a, **k: ("", "", 1))
    with pytest.raises(render_qc.QcExecError):
        render_qc._stream_end_ms(path, "a")
    monkeypatch.setattr(render_qc, "_ffmpeg_raw", lambda *a, **k: ("#tb 0: 1/1000\n", "", 0))
    with pytest.raises(render_qc.QcExecError):
        render_qc._stream_end_ms(path, "v")  # 没有任何包
    monkeypatch.setattr(render_qc, "_ffmpeg_raw", lambda *a, **k: ("", "", 0))
    with pytest.raises(render_qc.QcExecError):
        render_qc._stream_end_ms(path, "v")  # 没有时间基


def _seed_v1_selected(s, canvas):
    async def _go():
        first = (await s.execute(select(Artifact).where(Artifact.storage_key == "media/v1.mp4"))).scalar_one()
        graph = json.loads(json.dumps(canvas.graph))
        next(n for n in graph["nodes"] if n["id"] == "v1")["data"]["selected"] = first.id
        canvas.graph = graph
        canvas.revision += 1
        await s.commit()

    return _go()


@requires_pg
def test_qc_metadata_write_failure_is_isolated_by_savepoint(pg, storage, monkeypatch):
    bad = {"version": 1, "status": "passed", "checks": {}, "issues": [], "notes": [], "x": float("nan")}
    monkeypatch.setattr(
        render_qc, "check_render", lambda *a, **k: bad
    )  # NaN 写进 JSONB 会让 flush 报数据库错误

    async def _run():
        async with pg() as s:
            owner, canvas = await _prepare(s, storage, settings={"burn_subtitles": False})
            job = await _run_render(s, canvas, owner)
            artifact = await s.get(Artifact, job.result["artifact_id"])
            return job, artifact.artifact_metadata

    job, meta = asyncio.run(_run())
    assert job.status == "succeeded", job.error
    assert job.result["qc"]["status"] == "error"
    assert "qc" not in meta


@requires_pg
def test_stale_and_failed_qc_hint_keeps_the_check_failure(pg, storage, monkeypatch):
    failed = {
        "version": 1,
        "status": "failed",
        "checks": {},
        "notes": [],
        "issues": [{"code": "av_duration_diff_ms", "level": "blocking", "message": "声音比画面长 0.8 秒"}],
    }
    monkeypatch.setattr(render_qc, "check_render", lambda *a, **k: failed)

    async def _run():
        async with pg() as s:
            owner, canvas = await _prepare(s, storage, settings={"burn_subtitles": False})
            await _seed_v1_selected(s, canvas)
            await _run_render(s, canvas, owner)
            # 渲染后镜头 1 又多了一个视频并被选用 → stale
            _video(storage / "media/v1b.mp4", 3)
            newer = Artifact(
                project_id=canvas.project_id,
                type="video",
                storage_key="media/v1b.mp4",
                duration_ms=3000,
                content_hash="v1b",
                artifact_metadata={"source": "canvas_upload", "canvas_node_id": "v1"},
            )
            s.add(newer)
            await s.flush()
            graph = json.loads(json.dumps(canvas.graph))
            next(n for n in graph["nodes"] if n["id"] == "v1")["data"]["selected"] = newer.id
            canvas.graph = graph
            canvas.revision += 1
            await s.commit()
            final = await router.get_final(canvas.id, session=s, user=owner)
            return (
                final,
                await overview.project_summary(s, owner, canvas.id),
                await overview.overview(s, owner),
            )

    final, summary, board = asyncio.run(_run())
    assert final["stale"] is True and final["last_success"]["qc"]["status"] == "failed"
    assert _step(summary, "final") != "done"
    hint = "出片检查没通过，素材也有变化，需要重新渲染"
    assert summary["next_hint"] == hint and board["projects"][0]["next_hint"] == hint
