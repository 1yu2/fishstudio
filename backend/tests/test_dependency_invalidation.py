"""局部依赖失效规则回归。"""

from app.services.dependency_invalidation_service import (
    make_idempotency_key,
    stages_for_change,
)


def test_dialogue_only_preserves_visual_generation():
    stages = stages_for_change("shot", ["dialogue_tracks"])
    assert {"audio", "subtitle", "timeline", "render", "publication"} <= stages
    assert "image" not in stages
    assert "video" not in stages


def test_shot_duration_preserves_candidate_image():
    stages = stages_for_change("shot", ["duration_ms"])
    assert {"video", "audio", "subtitle", "timeline", "render"} <= stages
    assert "image" not in stages


def test_visual_prompt_invalidates_only_that_shot_visual_chain():
    stages = stages_for_change("shot", ["visual_prompt"])
    assert {"image", "video", "timeline", "render"} <= stages
    assert "audio" not in stages


def test_publication_copy_preserves_media():
    assert stages_for_change("publication", ["title", "description"]) == {"publication"}


def test_bible_invalidates_script_and_full_downstream():
    stages = stages_for_change("series_bible", ["structured_payload"])
    assert {"script", "storyboard", "image", "video", "audio", "render"} <= stages


def test_idempotency_key_is_stable_for_field_order():
    first = make_idempotency_key("p", "shot", "s", 1, ["duration_ms", "visual_prompt"], "x")
    second = make_idempotency_key("p", "shot", "s", 1, ["visual_prompt", "duration_ms"], "x")
    assert first == second


def test_different_change_token_gets_new_event():
    first = make_idempotency_key("p", "shot", "s", None, ["duration_ms"], "before-a")
    second = make_idempotency_key("p", "shot", "s", None, ["duration_ms"], "before-b")
    assert first != second
