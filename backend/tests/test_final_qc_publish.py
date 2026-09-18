from types import SimpleNamespace

from app.services.publish_service import evaluate_manifest


def _timeline(**overrides):
    spec = {
        "fps": 24,
        "aspect_ratio": "9:16",
        "video_tracks": [{"id": "main", "clips": [{
            "id": "c1", "shot_id": "sh1", "artifact_id": "source1",
            "source_in_ms": 600, "source_out_ms": 4700,
            "timeline_start_ms": 0, "speed": 1,
        }]}],
        "subtitle_tracks": [{"id": "sub", "cues": [{
            "id": "s1", "start_ms": 100, "end_ms": 900, "text": "测试",
            "safe_area": {"bottom_percent": 8},
        }]}],
    }
    spec.update(overrides.pop("spec", {}))
    return SimpleNamespace(id="tl1", status="approved", spec=spec, **overrides)


def test_final_qc_passes_with_complete_evidence():
    artifact = SimpleNamespace(id="render1", width=720, height=1280, duration_ms=4100)
    technical, semantic, issues = evaluate_manifest(
        artifact=artifact, timeline=_timeline(),
        manifest={"is_publishable": True, "placeholder_shots": []},
        media_probe={"decodable": True, "black_detected": False, "freeze_detected": False, "loudness_lufs": -16},
    )
    assert all(item["passed"] for item in technical + semantic)
    assert issues == []


def test_final_qc_failure_points_to_artifact_and_repairable_issue():
    artifact = SimpleNamespace(id="render1", width=540, height=960, duration_ms=3000)
    _, _, issues = evaluate_manifest(
        artifact=artifact, timeline=_timeline(),
        manifest={"is_publishable": False, "placeholder_shots": ["sh1"]},
        media_probe={"decodable": True, "black_detected": True, "freeze_detected": True, "loudness_lufs": -30},
    )
    codes = {item["code"] for item in issues}
    assert {"placeholder_or_black", "static_frame", "resolution", "loudness", "av_sync", "render_publishable"} <= codes
    assert next(item for item in issues if item["code"] == "placeholder_or_black")["target"]["artifact_id"] == "render1"


def test_subtitle_outside_safe_area_is_located_to_cue():
    timeline = _timeline()
    timeline.spec["subtitle_tracks"][0]["cues"][0]["safe_area"] = {"bottom_percent": 1}
    artifact = SimpleNamespace(id="render1", width=720, height=1280, duration_ms=4100)
    _, _, issues = evaluate_manifest(
        artifact=artifact, timeline=timeline,
        manifest={"is_publishable": True, "placeholder_shots": []},
        media_probe={"decodable": True, "loudness_lufs": -16},
    )
    issue = next(item for item in issues if item["code"] == "subtitle_safe_area")
    assert issue["target"] == {"track_or_cue_id": "s1"}
