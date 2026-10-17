import pytest

from app.services.timeline_renderer import build_audio_mix_filter, build_timeline_srt
from app.services.timeline_service import TimelineSpec, VideoClip, canonical_hash, video_clips


def test_video_clip_supports_source_range_and_speed():
    clip = VideoClip(
        id="c1",
        artifact_id="a1",
        source_in_ms=600,
        source_out_ms=4700,
        timeline_start_ms=1200,
        speed=1.25,
        transition={"type": "crossfade", "duration_ms": 200},
    )
    assert clip.source_in_ms == 600
    assert clip.source_out_ms == 4700
    assert clip.speed == 1.25


def test_invalid_video_clip_range_rejected():
    with pytest.raises(ValueError):
        VideoClip(id="c1", artifact_id="a1", source_in_ms=900, source_out_ms=800, timeline_start_ms=0)


def test_timeline_hash_is_key_order_independent():
    assert canonical_hash({"b": 2, "a": 1}) == canonical_hash({"a": 1, "b": 2})


def test_video_clips_are_sorted_deterministically():
    spec = TimelineSpec(
        video_tracks=[
            {
                "id": "main",
                "clips": [
                    {"id": "b", "artifact_id": "a2", "source_out_ms": 1000, "timeline_start_ms": 1000},
                    {"id": "a", "artifact_id": "a1", "source_out_ms": 1000, "timeline_start_ms": 0},
                ],
            }
        ]
    ).model_dump(mode="json")
    assert [clip.id for clip in video_clips(spec)] == ["a", "b"]


def test_timeline_subtitle_and_audio_trim_filters():
    srt = build_timeline_srt(
        {
            "subtitle_tracks": [
                {
                    "cues": [
                        {"id": "s1", "start_ms": 100, "end_ms": 900, "text": "你好"},
                    ]
                }
            ]
        }
    )
    assert "00:00:00,100 --> 00:00:00,900" in srt
    filters, label = build_audio_mix_filter(
        [
            {
                "input_index": 1,
                "delay_ms": 200,
                "source_in_ms": 600,
                "source_out_ms": 4700,
                "volume_db": -3,
                "is_bgm": False,
            }
        ],
        -18,
    )
    assert "atrim=start=0.600:end=4.700" in filters
    assert "adelay=200|200" in filters
    assert label == "[aout]"
