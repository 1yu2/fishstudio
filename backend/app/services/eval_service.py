"""固定基准、质量成本聚合、路由反馈与运营指标回连。"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import and_, func, or_, select
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
    SeriesProject,
    UsageEvent,
)

QUALITY_DIMENSIONS = (
    "character_consistency",
    "wardrobe_consistency",
    "scene_consistency",
    "action_completion",
    "continuity",
    "subtitle_quality",
    "av_sync",
    "technical_quality",
)


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


# 基准集 v1：ViStoryBench（MIT）中 19 个公版故事的分镜文本 + FishStudio 生成的原创角色设定图。
# 由 scripts/benchmark/build_vistory.py 生成；设定图存放在 storage/benchmarks/vistory/（不入库）。
BENCHMARK_FILE = Path(__file__).resolve().parent.parent / "eval_data" / "vistory_v1.json"
# 早期占位基准（固定 3/8 镜头的假数据），保留历史 EvalRun 的外键，但不再对外列出。
RETIRED_KEYS = ("short-drama-3-shot", "short-drama-8-shot")


@lru_cache(maxsize=1)
def benchmark_stories() -> tuple[dict[str, Any], ...]:
    data = json.loads(BENCHMARK_FILE.read_text(encoding="utf-8"))
    return tuple({**story, "source": data["source"]} for story in data["stories"])


def _dataset_payload(story: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    inputs = {
        "schema_version": 2,
        "source": story["source"],
        "source_id": story["source_id"],
        "characters": story["characters"],
        "shots": story["shots"],
    }
    constraints = {
        "required_metrics": list(QUALITY_DIMENSIONS),
        "min_score": 0.7,
        "aspect_ratio": story["aspect_ratio"],
        "shot_count": len(story["shots"]),
    }
    return inputs, constraints


async def seed_benchmarks(session: AsyncSession) -> list[BenchmarkDataset]:
    """幂等写入基准集，返回当前 active 的基准。

    内容（content_hash）变化时不改旧行：旧版本标记 retired，新内容以 version+1 写入，
    这样历史 EvalRun 始终指向它当时评测的那份数据。旧占位基准一律 retired。
    """
    stories = benchmark_stories()
    rows = (
        (
            await session.execute(
                select(BenchmarkDataset).where(
                    BenchmarkDataset.key.in_([story["key"] for story in stories] + list(RETIRED_KEYS))
                )
            )
        )
        .scalars()
        .all()
    )
    latest: dict[str, BenchmarkDataset] = {}
    for row in rows:
        if row.key in RETIRED_KEYS:
            row.status = "retired"
        elif row.key not in latest or row.version > latest[row.key].version:
            latest[row.key] = row

    active = []
    for story in stories:
        inputs, constraints = _dataset_payload(story)
        content_hash = _canonical_hash({"input": inputs, "expected": constraints})
        current = latest.get(story["key"])
        if current is not None and current.content_hash == content_hash:
            current.status = "active"
            active.append(current)
            continue
        if current is not None:
            current.status = "retired"
        row = BenchmarkDataset(
            key=story["key"],
            version=current.version + 1 if current is not None else 1,
            name=f"{story['name']}（ViStoryBench #{story['source_id']}）",
            shot_count=len(story["shots"]),
            input_payload=inputs,
            expected_constraints=constraints,
            content_hash=content_hash,
            status="active",
        )
        session.add(row)
        active.append(row)
    await session.commit()
    return active


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
    quality = {
        key: round(sum(values) / len(values), 4) if values else None for key, values in dimensions.items()
    }
    quality_values = [value for value in quality.values() if value is not None]
    score = round(sum(quality_values) / len(quality_values), 4) if quality_values else 0
    latencies = [float(sample["latency_ms"]) for sample in samples if sample.get("latency_ms") is not None]
    priced_costs = [
        Decimal(str(sample["cost_cny"])) for sample in samples if sample.get("cost_cny") is not None
    ]
    unavailable = sum(sample.get("cost_cny") is None for sample in samples)
    # 花费是按总花费平均分摊出来的估算值（拿不到镜头自己的用量事件）的样本数
    estimated = sum(
        bool(sample.get("cost_estimated")) and sample.get("cost_cny") is not None for sample in samples
    )
    successes = sum(bool(sample.get("success", True)) for sample in samples)
    reasons = Counter(str(reason) for sample in samples for reason in (sample.get("failure_reasons") or []))
    total_cost = sum(priced_costs, Decimal("0")) if priced_costs else None
    unit_quality_cost = (total_cost / Decimal(str(score))) if total_cost is not None and score > 0 else None
    metrics = {
        "quality": quality,
        "success_rate": round(successes / len(samples), 4) if samples else 0,
        "latency_ms": {"p50": percentile(latencies, 0.5), "p95": percentile(latencies, 0.95)},
        "cost_cny": {
            "total": str(total_cost) if total_cost is not None else "unavailable",
            "unavailable_count": unavailable,
            "estimated_count": estimated,
            "unit_quality_cost": str(unit_quality_cost.quantize(Decimal("0.000001")))
            if unit_quality_cost is not None
            else "unavailable",
        },
    }
    weakest = sorted(
        ((key, value) for key, value in quality.items() if value is not None), key=lambda item: item[1]
    )[:3]
    explanation = f"综合质量 {score:.3f}；主要弱项：" + "、".join(
        f"{key}={value:.3f}" for key, value in weakest
    )
    if estimated:
        explanation += f"；{estimated} 个样本花费为平均分摊的估算值"
    if unavailable:
        explanation += f"；{unavailable} 个样本价格 unavailable，未按零成本计算"
    return metrics, dict(reasons), score, explanation


async def create_eval_run(
    session: AsyncSession,
    *,
    dataset: BenchmarkDataset,
    provider: str,
    deployment: str,
    model: str,
    samples: list[dict[str, Any]],
    project_id: Optional[str] = None,
    created_by: Optional[int] = None,
    partial_shots: Optional[int] = None,
) -> EvalRun:
    if dataset.status != "active":
        raise ValueError(f"benchmark {dataset.key} v{dataset.version} 已停用")
    input_hash = dataset.content_hash
    if partial_shots is None:
        if len(samples) != dataset.shot_count:
            raise ValueError(f"benchmark 需要 {dataset.shot_count} 个镜头样本")
    else:
        # 局部运行（基准脚本 --max-shots 只跑前 N 个镜头）：必须明确声明 N 且样本数等于 N；
        # input_hash 带上 N，metrics 里标记 partial，routing_stats 不统计，避免与完整基准混比
        if not 1 <= partial_shots < dataset.shot_count or len(samples) != partial_shots:
            raise ValueError(f"局部运行需要 1 到 {dataset.shot_count - 1} 个镜头，且样本数与之相等")
        input_hash = f"{dataset.content_hash}:partial-{partial_shots}"
    metrics, reasons, score, explanation = aggregate_samples(samples)
    if partial_shots is not None:
        metrics = {**metrics, "partial": {"shots": partial_shots, "of": dataset.shot_count}}
    row = EvalRun(
        dataset_id=dataset.id,
        project_id=project_id,
        provider=provider,
        deployment=deployment,
        model=model,
        input_hash=input_hash,
        metrics=metrics,
        failure_reasons=reasons,
        score=score,
        explanation=explanation,
        created_by=created_by,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


def visible_eval_runs(user_id: Optional[int] = None):
    """内部/管理员可汇总全局；普通用户仅可见自己的项目及本人独立评测。"""
    stmt = select(EvalRun)
    if user_id is not None:
        owned_projects = select(SeriesProject.id).where(SeriesProject.user_id == user_id)
        stmt = stmt.where(
            or_(
                EvalRun.project_id.in_(owned_projects),
                and_(EvalRun.project_id.is_(None), EvalRun.created_by == user_id),
            )
        )
    return stmt


async def routing_stats(session: AsyncSession, user_id: Optional[int] = None) -> list[dict[str, Any]]:
    rows = [
        row
        for row in (
            await session.execute(visible_eval_runs(user_id).where(EvalRun.status == "completed"))
        ).scalars()
        # 局部运行（只跑了前 N 个镜头）与完整基准不可比，不进入路由统计
        if "partial" not in (row.metrics or {})
    ]
    grouped: dict[str, list[EvalRun]] = {}
    for row in rows:
        grouped.setdefault(row.deployment, []).append(row)
    result = []
    for deployment, runs in grouped.items():
        quality = sum(row.score for row in runs) / len(runs)
        p95s = [
            float(row.metrics.get("latency_ms", {}).get("p95"))
            for row in runs
            if row.metrics.get("latency_ms", {}).get("p95") is not None
        ]
        costs = [
            Decimal(row.metrics["cost_cny"]["unit_quality_cost"])
            for row in runs
            if row.metrics.get("cost_cny", {}).get("unit_quality_cost") != "unavailable"
        ]
        failures = Counter()
        for row in runs:
            failures.update(row.failure_reasons or {})
        result.append(
            {
                "deployment": deployment,
                "provider": runs[-1].provider,
                "model": runs[-1].model,
                "quality_score": round(quality, 4),
                "p95_latency_ms": percentile(p95s, 0.5),
                "unit_quality_cost_cny": str(sum(costs) / len(costs)) if costs else "unavailable",
                "health_score": round(
                    sum(float(row.metrics.get("success_rate", 0)) for row in runs) / len(runs), 4
                ),
                "sample_runs": len(runs),
                "main_failure_reasons": failures.most_common(5),
            }
        )
    return sorted(
        result,
        key=lambda item: (
            -item["quality_score"],
            -item["health_score"],
            item["p95_latency_ms"] or float("inf"),
        ),
    )


async def project_feedback(session: AsyncSession, project_id: str) -> dict[str, Any]:
    candidates = list(
        (
            await session.execute(select(ArtifactCandidate).where(ArtifactCandidate.project_id == project_id))
        ).scalars()
    )
    regenerations = list(
        (
            await session.execute(
                select(RegenerationRequest).where(RegenerationRequest.project_id == project_id)
            )
        ).scalars()
    )
    jobs = list(
        (await session.execute(select(GenerationJob).where(GenerationJob.project_id == project_id))).scalars()
    )
    selected = sum(item.status == "approved" for item in candidates)
    reasons = Counter(item.rejection_reason for item in candidates if item.rejection_reason)
    reasons.update(item.reason_code for item in regenerations if item.reason_code)
    latencies = [
        float((job.finished_at - job.started_at).total_seconds() * 1000)
        for job in jobs
        if job.started_at and job.finished_at
    ]
    return {
        "candidate_selection_rate": selected / len(candidates) if candidates else None,
        "failure_reasons": dict(reasons),
        "provider_success_rate": sum(job.status == "succeeded" for job in jobs) / len(jobs) if jobs else None,
        "latency_ms": {"p50": percentile(latencies, 0.5), "p95": percentile(latencies, 0.95)},
    }


async def record_publication_metrics(
    session: AsyncSession,
    publication: Publication,
    metrics: dict[str, Any],
    *,
    content_revisions: dict[str, Any],
    recorded_by: Optional[int],
) -> PublicationMetric:
    invocation_ids = list(
        (
            await session.execute(
                select(ModelInvocation.id).where(ModelInvocation.project_id == publication.project_id)
            )
        ).scalars()
    )
    row = PublicationMetric(
        publication_id=publication.id,
        project_id=publication.project_id,
        episode_id=publication.episode_id,
        artifact_id=publication.render_artifact_id,
        content_revisions=content_revisions,
        model_invocation_ids=invocation_ids,
        platform=publication.platform,
        metrics=metrics,
        recorded_by=recorded_by,
    )
    publication.metrics = metrics
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


# ── 报价与落账口径一致性（基准脚本真实跑前的价格体检）─────────────────────

PRICE_TOLERANCE = Decimal("1.05")


def _image_slugs(snapshot: Any, image_model: str) -> list[tuple[str, str]]:
    """要检查的图片部署：显式指定的，或默认路由（生成 + 编辑，含备用）里的全部候选。"""
    if image_model:
        return [("指定", image_model)]
    out: list[tuple[str, str]] = []
    for purpose in ("image_generation", "image_edit"):
        for slug in dict.fromkeys(snapshot.route_candidates(purpose, "final")):
            out.append((purpose, slug))
    return out


def price_consistency(
    *, image_model: str = "", video_model: str = "", tier: str = "preview", duration: int = 4
) -> dict[str, Any]:
    """比较“报价口径”与“worker 落账口径”的单价。

    - 图片：报价用 Catalog 部署的 pricing.output_per_image；落账用 estimate_image_cost(provider, model) 的静态价目。
      任一缺失，或落账价 > 报价 × 1.05 都算不一致（低估会让预算与每日上限按偏低价格放行，缺落账价会漏记）。
    - 视频：报价与落账（estimated_cost）都来自同一份 Catalog 价格快照；这里核对价格条目存在且能算出该时长的价格。
    """
    from app.model_runtime.catalog import get_catalog
    from app.providers.pricing import (
        estimate_image_cost,
        estimate_video_cost_from_entry,
        estimate_video_tokens,
        pricing_entry,
    )

    snapshot = get_catalog().snapshot
    problems: list[str] = []
    images: list[dict[str, Any]] = []
    for purpose, slug in _image_slugs(snapshot, image_model):
        item = snapshot.get(slug)
        row: dict[str, Any] = {"purpose": purpose, "slug": slug, "quote": None, "booked": None, "ok": False}
        images.append(row)
        if item is None or item.modality != "image":
            problems.append(f"图片部署 {slug} 不存在")
            continue
        raw = (item.pricing or {}).get("output_per_image")
        try:
            quote = Decimal(str(raw)) if raw not in (None, "") else None
        except ArithmeticError:
            quote = None
        provider = getattr(item.instance, "name", "") or item.driver
        booked = estimate_image_cost(provider, item.model, count=1)
        row.update(provider=provider, model=item.model, quote=str(quote) if quote is not None else None)
        row["booked"] = str(booked) if booked is not None else None
        if quote is None or booked is None:
            problems.append(
                f"图片部署 {slug}：{'Catalog 报价' if quote is None else '落账静态价目'}缺失"
                f"（报价 {row['quote']}，落账 {row['booked']}）"
            )
        elif booked > quote * PRICE_TOLERANCE:
            problems.append(f"图片部署 {slug}：落账价 {booked} 高于报价 {quote} 的 1.05 倍")
        else:
            row["ok"] = True
    videos: list[dict[str, Any]] = []
    if video_model:
        video_slugs = [video_model]
    else:
        video_slugs = list(dict.fromkeys(snapshot.route_candidates("shot_video", tier)))
    for slug in video_slugs:
        item = snapshot.get(slug)
        row = {"slug": slug, "quote": None, "booked": None, "ok": False}
        videos.append(row)
        if item is None or item.modality != "video":
            problems.append(f"视频部署 {slug} 不存在")
            continue
        entry = pricing_entry(item.pricing)
        cost = (
            estimate_video_cost_from_entry(
                entry,
                output_seconds=duration,
                video_tokens=estimate_video_tokens("480p" if tier == "preview" else "720p", duration),
            )
            if entry
            else None
        )
        # 落账用同一份价格快照（job.params.price_snapshot）还原，必须与报价一致
        row["quote"] = row["booked"] = str(cost) if cost is not None else None
        if cost is None:
            problems.append(f"视频部署 {slug}：价格条目缺失，或算不出 {duration} 秒镜头的价格")
        else:
            row["ok"] = True
    if not videos:
        problems.append("没有可检查的视频部署（未配置默认路由）")
    if not images:
        problems.append("没有可检查的图片部署（未配置默认路由）")
    return {"image": images, "video": videos, "ok": not problems, "problems": problems}
