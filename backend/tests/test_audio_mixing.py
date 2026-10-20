import json
import subprocess
from pathlib import Path

import pytest

from app.tools import audio_mixing


def _tool_call(tool_object, **kwargs):
    function = getattr(tool_object, "func", tool_object)
    return function(**kwargs)


def _make_tone(path: Path, duration: float, frequency: int = 440) -> None:
    subprocess.run(
        [
            audio_mixing.FFMPEG_PATH,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture()
def audio_dirs(monkeypatch, tmp_path):
    audios = tmp_path / "audios"
    bgm = tmp_path / "bgm"
    podcasts = tmp_path / "podcasts"
    for directory in (audios, bgm, podcasts):
        directory.mkdir()
    monkeypatch.setattr(audio_mixing, "AUDIOS_DIR", audios)
    monkeypatch.setattr(audio_mixing, "BGM_DIR", bgm)
    monkeypatch.setattr(audio_mixing, "PODCASTS_DIR", podcasts)
    return audios, bgm, podcasts


def test_imageio_ffmpeg_is_available():
    assert audio_mixing.FFMPEG_AVAILABLE
    assert Path(audio_mixing.FFMPEG_PATH).is_file()


def test_probe_audio_duration_without_ffprobe(audio_dirs):
    audios, _, _ = audio_dirs
    source = audios / "tone.wav"
    _make_tone(source, 0.6)
    assert audio_mixing.probe_audio_duration(source) == pytest.approx(0.6, abs=0.03)


def test_concatenate_audio_with_silence(audio_dirs):
    audios, _, _ = audio_dirs
    first = audios / "first.wav"
    second = audios / "second.wav"
    _make_tone(first, 0.4, 440)
    _make_tone(second, 0.4, 660)

    raw = _tool_call(
        audio_mixing.concatenate_audio_tool,
        audio_files=[str(first), str(second)],
        crossfade_duration=0,
        silence_duration=200,
    )
    result = json.loads(raw)
    output = audios / Path(result["audio_url"]).name
    assert output.is_file()
    assert result["duration_seconds"] == pytest.approx(1.0, abs=0.06)


def test_concatenate_audio_with_crossfade(audio_dirs):
    audios, _, _ = audio_dirs
    first = audios / "first.wav"
    second = audios / "second.wav"
    _make_tone(first, 0.6, 440)
    _make_tone(second, 0.6, 660)

    raw = _tool_call(
        audio_mixing.concatenate_audio_tool,
        audio_files=[str(first), str(second)],
        crossfade_duration=100,
        silence_duration=0,
    )
    result = json.loads(raw)
    assert result["duration_seconds"] == pytest.approx(1.1, abs=0.06)


def test_concatenate_three_clips_with_crossfade(audio_dirs):
    audios, _, _ = audio_dirs
    clips = []
    for name, seconds, frequency in (("a", 0.6, 440), ("b", 0.4, 550), ("c", 0.5, 660)):
        path = audios / f"{name}.wav"
        _make_tone(path, seconds, frequency)
        clips.append(str(path))

    raw = _tool_call(
        audio_mixing.concatenate_audio_tool, audio_files=clips, crossfade_duration=100, silence_duration=0
    )
    # 0.6 + 0.4 + 0.5 - 两处 0.1 的交叠
    assert json.loads(raw)["duration_seconds"] == pytest.approx(1.3, abs=0.06)


def test_select_bgm_loops_and_trims(audio_dirs):
    audios, bgm, _ = audio_dirs
    source = bgm / "欢快 开场.wav"
    _make_tone(source, 0.4, 330)

    raw = _tool_call(
        audio_mixing.select_bgm_tool,
        scene_description="欢快 开场",
        duration_seconds=1.2,
    )
    result = json.loads(raw)
    output = audios / Path(result["bgm_path"]).name
    assert output.is_file()
    assert result["match_score"] == 2
    assert result["adjusted_duration"] == pytest.approx(1.2, abs=0.08)


@pytest.mark.parametrize("normalize", [False, True])
def test_mix_audio_with_bgm(audio_dirs, normalize):
    audios, bgm, podcasts = audio_dirs
    voice = audios / "voice.wav"
    music = bgm / "music.wav"
    _make_tone(voice, 0.7, 660)
    _make_tone(music, 0.3, 220)

    raw = _tool_call(
        audio_mixing.mix_audio_with_bgm_tool,
        voice_audio=str(voice),
        bgm_audio=str(music),
        bgm_volume=-24,
        intro_duration=0.2,
        normalize=normalize,
    )
    result = json.loads(raw)
    output = podcasts / Path(result["audio_url"]).name
    assert output.is_file()
    assert result["duration_seconds"] == pytest.approx(0.9, abs=0.08)
    assert result["normalized"] is normalize
