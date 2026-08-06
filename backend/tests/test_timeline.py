"""timeline_renderer 纯函数与 prompt 瘦身测试（不连 DB）。"""

from pathlib import Path

import pytest

from app.services import prompt as prompt_module
from app.services import timeline_renderer as tr

# ─────────────────────────────────────────
# compute_shot_offsets
# ─────────────────────────────────────────


class _Shot:
    def __init__(self, duration_ms, subtitle_tracks=None):
        self.duration_ms = duration_ms
        self.subtitle_tracks = subtitle_tracks or []


def test_compute_shot_offsets_cumulative():
    shots = [_Shot(5000), _Shot(3000), _Shot(8000)]
    assert tr.compute_shot_offsets(shots) == [0, 5000, 8000]


def test_compute_shot_offsets_empty():
    assert tr.compute_shot_offsets([]) == []


def test_compute_shot_offsets_dict_input():
    shots = [{"duration_ms": 2000}, {"duration_ms": 1000}]
    assert tr.compute_shot_offsets(shots) == [0, 2000]


# ─────────────────────────────────────────
# build_srt
# ─────────────────────────────────────────


def test_build_srt_time_format():
    shots = [
        (
            _Shot(5000, subtitle_tracks=[{"text": "你好", "start_ms": 500, "end_ms": 2000}]),
            0,
        )
    ]
    srt = tr.build_srt(shots)
    assert "1\n" in srt
    assert "00:00:00,500 --> 00:00:02,000" in srt
    assert "你好" in srt


def test_build_srt_cross_shot_offset():
    shots = [
        (_Shot(5000, subtitle_tracks=[{"text": "第一句", "start_ms": 0, "end_ms": 1000}]), 0),
        (_Shot(3000, subtitle_tracks=[{"text": "第二句", "start_ms": 500, "end_ms": 1500}]), 5000),
    ]
    srt = tr.build_srt(shots)
    assert "00:00:00,000 --> 00:00:01,000" in srt
    # 第二镜头偏移 5000 + start 500 = 5500ms
    assert "00:00:05,500 --> 00:00:06,500" in srt
    # 序号递增
    assert srt.count("\n\n") >= 1
    assert "\n2\n" in srt


def test_build_srt_empty_tracks():
    shots = [(_Shot(5000), 0), (_Shot(3000, subtitle_tracks=[]), 5000)]
    assert tr.build_srt(shots) == ""


def test_build_srt_skips_invalid_tracks():
    tracks = [
        {"text": "", "start_ms": 0, "end_ms": 1000},  # 无文本
        {"text": "倒退", "start_ms": 2000, "end_ms": 1000},  # end <= start
        {"text": "正常", "start_ms": 0, "end_ms": 900},
    ]
    srt = tr.build_srt([(_Shot(5000, subtitle_tracks=tracks), 0)])
    assert "正常" in srt
    assert "倒退" not in srt
    assert srt.startswith("1\n")


# ─────────────────────────────────────────
# ffmpeg 命令构造纯函数
# ─────────────────────────────────────────


def test_build_normalize_filter():
    f = tr.build_normalize_filter(720, 1280, 24)
    assert "scale=720:1280:force_original_aspect_ratio=decrease" in f
    assert "pad=720:1280" in f
    assert "fps=24" in f
    assert "setsar=1" in f


def test_build_black_placeholder_args():
    args = tr.build_black_placeholder_args(1500, 720, 1280, 24)
    joined = " ".join(args)
    assert "lavfi" in joined
    assert "color=c=black:size=720x1280:rate=24:duration=1.500" in joined


def test_build_concat_file_list():
    content = tr.build_concat_file_list(["/tmp/a.mp4", "/tmp/b.mp4"])
    assert content == "file '/tmp/a.mp4'\nfile '/tmp/b.mp4'\n"


def test_build_audio_mix_filter():
    specs = [
        {"input_index": 1, "delay_ms": 500, "is_bgm": False},
        {"input_index": 2, "delay_ms": 0, "is_bgm": True},
    ]
    fc, aout = tr.build_audio_mix_filter(specs, bgm_volume_db=-18.0)
    assert "[1:a]adelay=500|500[a0]" in fc
    assert "volume=-18.0dB" in fc
    assert "amix=inputs=2:duration=longest" in fc
    assert "loudnorm" in fc
    assert aout == "[aout]"


# ─────────────────────────────────────────
# ffmpeg 黑场占位（真跑，环境无 ffmpeg 则 skip）
# ─────────────────────────────────────────


def _ffmpeg_available() -> bool:
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        return bool(exe) and Path(exe).exists()
    except Exception:
        return False


@pytest.mark.skipif(not _ffmpeg_available(), reason="imageio-ffmpeg 不可用")
def test_black_placeholder_video_generation(tmp_path):
    out = tmp_path / "black.mp4"
    args = [
        *tr.build_black_placeholder_args(1000, 720, 1280, 24),
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(out),
    ]
    tr._run_ffmpeg(args, timeout=60)
    assert out.is_file() and out.stat().st_size > 0


def test_srt_ts_format():
    assert tr._fmt_srt_ts(0) == "00:00:00,000"
    assert tr._fmt_srt_ts(3_661_500) == "01:01:01,500"
    assert tr._fmt_srt_ts(-5) == "00:00:00,000"


# ─────────────────────────────────────────
# prompt 瘦身
# ─────────────────────────────────────────

_TOOLS = "- example_tool: does work"


def test_full_prompt_short_drama_skips_digital_and_understanding():
    text = prompt_module.get_full_prompt(
        tools_list_text=_TOOLS, project_type="short_drama"
    )
    # short_drama 只注入 VIDEO + AUDIO
    assert prompt_module.VIDEO_PROMPT[:80] in text
    assert prompt_module.AUDIO_PROMPT[:80] in text
    assert prompt_module.DIGITAL_PROMPT[:80] not in text
    assert prompt_module.UNDERSTANDING_PROMPT[:80] not in text


def test_full_prompt_default_injects_all_workflows():
    text = prompt_module.get_full_prompt(tools_list_text=_TOOLS)
    assert prompt_module.UNDERSTANDING_PROMPT[:80] in text
    assert prompt_module.VIDEO_PROMPT[:80] in text
    assert prompt_module.DIGITAL_PROMPT[:80] in text
    assert prompt_module.AUDIO_PROMPT[:80] in text


def test_full_prompt_backward_compatible_positional():
    # 旧的调用方式（位置/关键字，无 project_type）不报错
    text = prompt_module.get_full_prompt(_TOOLS, "", "")
    assert _TOOLS in text


def test_prompt_size_reduction():
    slim = prompt_module.get_full_prompt(tools_list_text=_TOOLS, project_type="short_drama")
    full = prompt_module.get_full_prompt(tools_list_text=_TOOLS)
    reduction = len(full) - len(slim)
    print(f"\nprompt 瘦身：全量 {len(full)} 字符 → short_drama {len(slim)} 字符，减少 {reduction}")
    assert reduction > 0
