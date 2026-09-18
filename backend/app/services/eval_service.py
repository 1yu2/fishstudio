"""固定基准、质量成本聚合、路由反馈与运营指标回连。"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ArtifactCandidate,
    BenchmarkDataset,
    EvalRun,
    GenerationJob,
    ModelInvocation,
    Publication,
    PublicationMetric,
    QCReport,
    RegenerationRequest,
    UsageEvent,
)

QUALITY_DIMENSIONS = (
    "character_consistency", "wardrobe_consistency", "scene_consistency",
    "action_completion", "continuity", "subtitle_quality", "av_sync", "technical_quality",
)


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _dataset_payload(shot_count: int) -> tuple[dict[str, Any], dict[str, Any]]:
    shots = [{
        "key": f"shot-{index + 1}", "character": "anchor-a",
        "wardrobe": "costume-v1", "scene": "room-a",
        "action": "enter-and-look", "duration_ms": 4000,
    } for index in range(shot_count)]
    return (
        {"schema_version": 1, "shots": shots, "seed": 20260917},
        {"required_metrics": list(QUALITY_DIMENSIONS), "min_score": 0.7, "aspect_ratio": "9:16"},
    )


async def seed_benchmarks(session: AsyncSession) -> list[BenchmarkDataset]:
    rows = []
    for count in (3, 8):
        key = f"short-drama-{count}-shot"
        existing = await session.scalar(select(BenchmarkDataset).where(BenchmarkDataset.key == key, BenchmarkDataset.version == 1))
        if existing:
            rows.append(existing)
            continue
        inputs, constraints = _dataset_payload(count)
        row = BenchmarkDataset(
            key=key, version=1, name=f"固定 {count} 镜头短剧基准", shot_count=count,
            input_payload=inputs, expected_constraints=constraints,
            content_hash=_canonical_hash({"input": inputs, "expected": constraints}),
        )
        session.add(row)
        rows.append(row)
    await session.commit()
    return rows


def percentile(values: list[float], percent: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def aggregate_samples(samples: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, int], float, str]:
    dimensions = {
        dimension: [float(sample[dimension]) for sample in samples if sample.get(dimension) is not None]
        for dimension in QUALITY_DIMENSIONS
    }
    quality = {key: round(sum(values) / len(values), 4) if values else None for key, values in dimensions.items()}
    quality_values = [value for value in quality.values() if value is not None]
    score = round(sum(quality_values) / len(quality_values), 4) if quality_values else 0
    latencies = [float(sample["latency_ms"]) for sample in samples if sample.get("latency_ms") is not None]
    priced_costs = [Decimal(str(sample["cost_cny"])) for sample in samples if sample.get("cost_cny") is not None]
    unavailable = sum(sample.get("cost_cny") is None for sample in samples)
    successes = sum(bool(sample.get("success", True)) for sample in samples)
    reasons = Counter(str(reason) for sample in samples for reason in (sample.get("failure_reasons") or []))
    total_cost = sum(priced_costs, Decimal("0")) if priced_costs else None
    unit_quality_cost = (total_cost / Decimal(str(score))) if total_cost is not None and score > 0 else None
    metrics = {
        "quality": quality, "success_rate": round(successes / len(samples), 4) if samples else 0,
        "latency_ms": {"p50": percentile(latencies, 0.5), "p95": percentile(latencies, 0.95)},
        "cost_cny": {
            "total": str(total_cost) if total_cost is not None else "unavailable",
            "unavailable_count": unavailable,
            "unit_quality_cost": str(unit_quality_cost.quantize(Decimal("0.000001"))) if unit_quality_cost is not None else "unavailable",
        },
    }
    weakest = sorted(((key, value) for key, value in quality.items() if value is not None), key=lambda item: item[1])[:3]
    explanation = f"综合质量 {score:.3f}；主要弱项：" + "、".join(f"{key}={value:.3f}" for key, value in weakest)
    if unavailable:
        explanation += f"；{unavailable} 个样本价格 unavailable，未按零成本计算"
    return metrics, dict(reasons), score, explanation


async def create_eval_run(
    session: AsyncSession, *, dataset: BenchmarkDataset, provider: str,
    deployment: str, model: str, samples: list[dict[str, Any]],
    project_id: Optional[str] = None, created_by: Optional[int] = None,
) -> EvalRun:
    if len(samples) != dataset.shot_count:
        raise ValueError(f"benchmark 需要 {dataset.shot_count} 个镜头样本")
    metrics, reasons, score, explanation = aggregate_samples(samples)
    row = EvalRun(
        dataset_id=dataset.id, project_id=project_id, provider=provider,
        deployment=deployment, model=model, input_hash=dataset.content_hash,
        metrics=metrics, failure_reasons=reasons, score=score,
        explanation=explanation, created_by=created_by,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def routing_stats(session: AsyncSession) -> list[dict[str, Any]]:
    rows = list((await session.execute(select(EvalRun).where(EvalRun.status == "completed"))).scalars())
    grouped: dict[str, list[EvalRun]] = {}
    for row in rows:
        grouped.setdefault(row.deployment, []).append(row)
    result = []
    for deployment, runs in grouped.items():
        quality = sum(row.score for row in runs) / len(runs)
        p95s = [float(row.metrics.get("latency_ms", {}).get("p95")) for row in runs if row.metrics.get("latency_ms", {}).get("p95") is not None]
        costs = [Decimal(row.metrics["cost_cny"]["unit_quality_cost"]) for row in runs if row.metrics.get("cost_cny", {}).get("unit_quality_cost") != "unavailable"]
        failures = Counter()
        for row in runs:
            failures.update(row.failure_reasons or {})
        result.append({
            "deployment": deployment, "provider": runs[-1].provider, "model": runs[-1].model,
            "quality_score": round(quality, 4), "p95_latency_ms": percentile(p95s, 0.5),
            "unit_quality_cost_cny": str(sum(costs) / len(costs)) if costs else "unavailable",
            "health_score": round(sum(float(row.metrics.get("success_rate", 0)) for row in runs) / len(runs), 4),
            "sample_runs": len(runs), "main_failure_reasons": failures.most_common(5),
        })
    return sorted(result, key=lambda item: (-item["quality_score"], -item["health_score"], item["p95_latency_ms"] or float("inf")))


async def project_feedback(session: AsyncSession, project_id: str) -> dict[str, Any]:
    candidates = list((await session.execute(select(ArtifactCandidate).where(ArtifactCandidate.project_id == project_id))).scalars())
    regenerations = list((await session.execute(select(RegenerationRequest).where(RegenerationRequest.project_id == project_id))).scalars())
    jobs = list((await session.execute(select(GenerationJob).where(GenerationJob.project_id == project_id))).scalars())
    selected = sum(item.status == "approved" for item in candidates)
    reasons = Counter(item.rejection_reason for item in candidates if item.rejection_reason)
    reasons.update(item.reason_code for item in regenerations if item.reason_code)
    latencies = [float((job.finished_at - job.started_at).total_seconds() * 1000) for job in jobs if job.started_at and job.finished_at]
    return {
        "candidate_selection_rate": selected / len(candidates) if candidates else None,
        "failure_reasons": dict(reasons),
        "provider_success_rate": sum(job.status == "succeeded" for job in jobs) / len(jobs) if jobs else None,
        "latency_ms": {"p50": percentile(latencies, .5), "p95": percentile(latencies, .95)},
    }


async def record_publication_metrics(
    session: AsyncSession, publication: Publication, metrics: dict[str, Any],
    *, content_revisions: dict[str, Any], recorded_by: Optional[int],
) -> PublicationMetric:
    invocation_ids = list((await session.execute(
        select(ModelInvocation.id).where(ModelInvocation.project_id == publication.project_id)
    )).scalars())
    row = PublicationMetric(
        publication_id=publication.id, project_id=publication.project_id,
        episode_id=publication.episode_id, artifact_id=publication.render_artifact_id,
        content_revisions=content_revisions, model_invocation_ids=invocation_ids,
        platform=publication.platform, metrics=metrics, recorded_by=recorded_by,
    )
    publication.metrics = metrics
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row
