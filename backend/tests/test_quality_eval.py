from decimal import Decimal
from types import SimpleNamespace

from app.services import cost_service
from app.services.eval_service import aggregate_samples, percentile


def _sample(score, latency, cost, *, success=True, reasons=None):
    return {
        "character_consistency": score, "wardrobe_consistency": score,
        "scene_consistency": score, "action_completion": score,
        "continuity": score, "subtitle_quality": score,
        "av_sync": score, "technical_quality": score,
        "latency_ms": latency, "cost_cny": cost, "success": success,
        "failure_reasons": reasons or [],
    }


def test_eval_is_repeatable_for_same_samples_and_explains_failures():
    samples = [_sample(.8, 100, "1.2"), _sample(.6, 300, None, success=False, reasons=["continuity_drift"])]
    first = aggregate_samples(samples)
    second = aggregate_samples(samples)
    assert first == second
    metrics, reasons, score, explanation = first
    assert score == .7
    assert metrics["latency_ms"] == {"p50": 200.0, "p95": 290.0}
    assert metrics["cost_cny"]["unavailable_count"] == 1
    assert reasons == {"continuity_drift": 1}
    assert "unavailable" in explanation


def test_unknown_price_is_not_zero_cost():
    metrics, _, _, _ = aggregate_samples([_sample(.9, 100, None)])
    assert metrics["cost_cny"]["total"] == "unavailable"
    assert metrics["cost_cny"]["unit_quality_cost"] == "unavailable"


def test_percentile_handles_empty_and_interpolates():
    assert percentile([], .95) is None
    assert percentile([100, 200, 300], .95) == 290


def test_budget_hard_limit_blocks_new_paid_work():
    run = SimpleNamespace(cost_limit=Decimal("10"), estimated_cost=Decimal("10"))
    assert cost_service.check_budget(Decimal("0.01"), run) is False
