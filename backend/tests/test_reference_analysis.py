from types import SimpleNamespace

import pytest

from app.services.reference_analysis_service import (
    build_deterministic_segments,
    validate_segments,
)


def test_eight_second_reference_is_split_into_editable_intervals():
    segments = build_deterministic_segments(8_000)
    assert [(item["start_ms"], item["end_ms"]) for item in segments] == [
        (0, 4_000),
        (4_000, 8_000),
    ]
    assert segments[0]["keyframes_ms"] == [0, 2_000, 3_999]
    assert segments[1]["action_intervals"][0]["end_ms"] == 8_000


def test_manual_segments_must_stay_inside_video_and_have_unique_keys():
    with pytest.raises(ValueError, match="invalid reference segment"):
        validate_segments(
            [
                {"key": "a", "start_ms": 0, "end_ms": 4_000},
                {"key": "a", "start_ms": 4_000, "end_ms": 8_000},
            ],
            8_000,
        )
    with pytest.raises(ValueError, match="invalid reference segment"):
        validate_segments([{"key": "a", "start_ms": 0, "end_ms": 8_001}], 8_000)


def test_detected_scene_boundaries_drive_segments():
    segments = build_deterministic_segments(8_000, [1_500, 5_250])
    assert [(item["start_ms"], item["end_ms"]) for item in segments] == [
        (0, 1_500),
        (1_500, 5_250),
        (5_250, 8_000),
    ]


def test_director_language_defaults_do_not_copy_subject_identity():
    segment = build_deterministic_segments(8_000)[0]
    reference = SimpleNamespace(reference_type="motion", segment_snapshot=segment, replace_subject=True)
    assert reference.segment_snapshot["camera_movement"] == "static"
    assert reference.replace_subject is True
