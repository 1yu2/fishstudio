from types import SimpleNamespace

import pytest

from app.schemas.series import AssetCardCreate
from app.services.job_planner import build_video_job_params


def test_character_profile_is_typed_and_preserved():
    body = AssetCardCreate(
        type="character",
        name="阿青",
        profile={
            "stable_traits": ["黑色短发", "左眉疤痕"],
            "costume": {"key": "school-v2"},
            "forbidden_changes": ["发色", "疤痕位置"],
        },
    )
    assert body.profile["costume"]["key"] == "school-v2"


def test_unknown_asset_type_is_rejected():
    with pytest.raises(ValueError, match="资产 type"):
        AssetCardCreate(type="unknown", name="x")


def test_reference_slots_compile_to_first_last_frame_request():
    compiled = SimpleNamespace(
        prompt="结构化 prompt",
        prompt_version="shot-prompt-v1",
        input_hash="hash",
        reference_slots=[{"type": "first_frame"}, {"type": "last_frame"}],
        reference_images=(),
        reference_videos=(),
        reference_audios=(),
        first_frame="images/a.png",
        last_frame="images/b.png",
        source_video=None,
    )
    params = build_video_job_params(
        shot_id="s1",
        step_id="step1",
        run_id="run1",
        prompt="old",
        duration_ms=5000,
        aspect_ratio="9:16",
        policy={},
        first_frame=None,
        compiled=compiled,
    )
    assert params["mode"] == "first_last_frame"
    assert params["prompt"] == "结构化 prompt"
    assert params["prompt_version"] == "shot-prompt-v1"
