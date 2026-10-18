from app.schemas.series import DirectorPlanPayload, ObserverFinding
from app.services.director_service import (
    deterministic_continuity_findings,
    deterministic_script_findings,
)


def test_director_plan_requires_unique_intent_keys():
    payload = {
        "creative_statement": "压迫感逐层升级",
        "intents": [
            {"key": "beat-1", "scene_number": 1, "beat": "进入"},
            {"key": "beat-2", "scene_number": 1, "beat": "反转"},
        ],
    }
    assert len(DirectorPlanPayload.model_validate(payload).intents) == 2


def test_script_observer_marks_missing_hook_critical():
    findings = deterministic_script_findings({"episode_objective": "逃脱", "scenes": []})
    assert any(item["severity"] == "critical" and item["code"] == "script.missing_hook" for item in findings)


def test_continuity_review_requires_director_trace_and_six_dimensions():
    payload = {
        "episodes": [
            {
                "scenes": [
                    {
                        "number": 1,
                        "shots": [
                            {"number": 1, "director_intent_key": "missing", "continuity_constraints": {}}
                        ],
                    }
                ]
            }
        ]
    }
    findings = deterministic_continuity_findings(payload, {"beat-1"})
    assert {item["code"] for item in findings} == {
        "continuity.director_intent",
        "continuity.constraints_missing",
    }
    ObserverFinding.model_validate(findings[0])
