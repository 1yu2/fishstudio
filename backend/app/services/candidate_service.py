"""候选 Artifact 的 QA、选择、淘汰和局部重拍服务。"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Artifact,
    ArtifactCandidate,
    ArtifactFeedback,
    GenerationJob,
    RegenerationRequest,
    Shot,
)
from app.providers.pricing import PRICE_TABLE_VERSION
from app.services import cost_service, dependency_invalidation_service, job_service

VALID_REASONS = {
    "character_inconsistent",
    "wrong_action",
    "wrong_expression",
    "wrong_camera",
    "scene_drift",
    "bad_anatomy",
    "continuity_failed",
    "partial_segment_failed",
    "wardrobe_inconsistent",
    "wrong_character_count",
    "style_drift",
    "technical_quality_failed",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def register_candidate(
    session: AsyncSession, *, artifact: Artifact, shot_id: str, candidate_index: int = 0
) -> ArtifactCandidate:
    existing = (
        await session.execute(
            select(ArtifactCandidate).where(
                ArtifactCandidate.shot_id == shot_id, ArtifactCandidate.artifact_id == artifact.id
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    candidate = ArtifactCandidate(
        project_id=artifact.project_id or "",
        shot_id=shot_id,
        artifact_id=artifact.id,
        job_id=artifact.job_id,
        candidate_index=candidate_index,
    )
    session.add(candidate)
    await session.flush()
    shot = await session.get(Shot, shot_id)
    findings: list[dict[str, Any]] = []
    if artifact.type == "video" and shot and artifact.duration_ms:
        delta = abs(artifact.duration_ms - shot.duration_ms)
        if delta > max(500, int(shot.duration_ms * 0.2)):
            findings.append(
                {
                    "code": "partial_segment_failed",
                    "severity": "warning",
                    "detail": f"候选时长 {artifact.duration_ms}ms 与镜头 {shot.duration_ms}ms 偏差过大",
                }
            )
    if artifact.type == "image" and artifact.width and artifact.height:
        actual = artifact.width / artifact.height
        ratio = (shot.aspect_ratio if shot else "9:16").split(":")
        expected = (int(ratio[0]) / int(ratio[1])) if len(ratio) == 2 else 9 / 16
        if abs(actual - expected) > 0.12:
            findings.append(
                {
                    "code": "wrong_camera",
                    "severity": "warning",
                    "detail": f"候选画幅 {artifact.width}×{artifact.height} 与镜头画幅不符",
                }
            )
    await evaluate_candidate(session, candidate, findings=findings, evaluated_by="deterministic:v1")
    return candidate


async def list_candidates(session: AsyncSession, shot_id: str) -> list[ArtifactCandidate]:
    result = await session.execute(
        select(ArtifactCandidate)
        .where(ArtifactCandidate.shot_id == shot_id)
        .order_by(ArtifactCandidate.candidate_index, ArtifactCandidate.created_at)
    )
    return list(result.scalars().all())


async def evaluate_candidate(
    session: AsyncSession,
    candidate: ArtifactCandidate,
    *,
    findings: Optional[list[dict[str, Any]]] = None,
    score: Optional[float] = None,
    evaluated_by: str = "deterministic",
) -> ArtifactCandidate:
    normalized = []
    for item in findings or []:
        code = str(item.get("code", ""))
        if code not in VALID_REASONS:
            continue
        normalized.append(
            {"code": code, "severity": item.get("severity", "error"), "detail": str(item.get("detail", ""))}
        )
    candidate.qa_findings = normalized
    candidate.score = (
        max(0.0, min(1.0, float(score))) if score is not None else max(0.0, 1.0 - 0.2 * len(normalized))
    )
    candidate.qa_summary = {
        "passed": not normalized,
        "finding_count": len(normalized),
        "score": candidate.score,
    }
    candidate.evaluated_by = evaluated_by or "deterministic"
    candidate.evaluated_at = _now()
    candidate.status = "rejected" if normalized and candidate.score < 0.5 else "evaluating"
    if candidate.status == "rejected":
        candidate.rejection_reason = normalized[0]["code"]
    await session.flush()
    return candidate


async def promote_candidate(
    session: AsyncSession,
    candidate: ArtifactCandidate,
    *,
    user_id: Optional[int] = None,
    is_admin: bool = False,
) -> ArtifactCandidate:
    shot = await session.get(Shot, candidate.shot_id, with_for_update=True)
    artifact = await session.get(Artifact, candidate.artifact_id)
    if shot is None or artifact is None:
        raise ValueError("candidate target not found")
    if candidate.status == "rejected":
        raise ValueError("rejected candidate cannot be promoted")
    kind = "image" if artifact.type == "image" else "video" if artifact.type == "video" else ""
    if not kind:
        raise ValueError("only image/video artifacts can be promoted")
    old_id = shot.selected_image_artifact_id if kind == "image" else shot.selected_video_artifact_id
    if old_id and old_id != artifact.id:
        old = (
            await session.execute(
                select(ArtifactCandidate).where(
                    ArtifactCandidate.shot_id == shot.id, ArtifactCandidate.artifact_id == old_id
                )
            )
        ).scalar_one_or_none()
        if old and old.status == "approved":
            old.status = "superseded"
    if kind == "image":
        shot.selected_image_artifact_id = artifact.id
    else:
        shot.selected_video_artifact_id = artifact.id
    candidate.status = "approved"
    candidate.selected_by = user_id
    candidate.selected_at = _now()
    shot.qa_status = "approved"
    session.add(
        ArtifactFeedback(
            project_id=candidate.project_id,
            artifact_id=artifact.id,
            user_id=user_id,
            action="selected",
            score=round((candidate.score or 0) * 5) or None,
            reason="candidate_promoted",
        )
    )
    await dependency_invalidation_service.apply_invalidation(
        session,
        project_id=candidate.project_id,
        source_type="shot",
        source_id=shot.id,
        changed_fields=[f"selected_{kind}_artifact_id"],
        user_id=user_id,
        is_admin=is_admin,
        reason="candidate_promoted",
        change_token=candidate.id,
    )
    await session.flush()
    return candidate


async def reject_candidate(
    session: AsyncSession, candidate: ArtifactCandidate, *, reason: str, user_id: Optional[int] = None
) -> ArtifactCandidate:
    if reason not in VALID_REASONS:
        raise ValueError(f"invalid rejection reason: {reason}")
    candidate.status = "rejected"
    candidate.rejection_reason = reason
    candidate.selected_by = user_id
    artifact = await session.get(Artifact, candidate.artifact_id)
    if artifact:
        session.add(
            ArtifactFeedback(
                project_id=candidate.project_id,
                artifact_id=artifact.id,
                user_id=user_id,
                action="rejected",
                score=round((candidate.score or 0) * 5) or None,
                reason=reason,
            )
        )
    await session.flush()
    return candidate


async def create_regeneration_job(
    session: AsyncSession,
    *,
    candidate: ArtifactCandidate,
    instruction: str = "",
    reason: str = "",
    preserve_fields: Optional[list[str]] = None,
    replace_fields: Optional[list[str]] = None,
    requested_by: Optional[int] = None,
    confirmed_quote_id: str = "",
) -> GenerationJob:
    if reason and reason not in VALID_REASONS:
        raise ValueError(f"invalid regeneration reason: {reason}")
    quote = (
        await session.execute(
            select(RegenerationRequest).where(
                RegenerationRequest.quote_id == confirmed_quote_id,
                RegenerationRequest.candidate_id == candidate.id,
            )
        )
    ).scalar_one_or_none()
    if quote is None or quote.status != "quoted":
        raise ValueError("请先获取并确认有效的重拍报价")
    expires = quote.quote_expires_at
    if expires and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if not expires or expires < _now() or quote.price_table_version != PRICE_TABLE_VERSION:
        raise ValueError("重拍报价已过期，请重新报价")
    if (
        quote.reason_code != reason
        or quote.instruction != instruction
        or quote.preserve_fields != (preserve_fields or [])
        or quote.replace_fields != (replace_fields or [])
    ):
        raise ValueError("重拍参数已变化，请重新报价")
    shot = await session.get(Shot, candidate.shot_id)
    if shot is None:
        raise ValueError("shot not found")
    artifact = await session.get(Artifact, candidate.artifact_id)
    operation = "generate_image" if artifact and artifact.type == "image" else "generate_video"
    params = {
        "shot_id": shot.id,
        "mode": "regenerate",
        "target_candidate_id": candidate.id,
        "regeneration_reason": reason,
        "instruction": instruction,
        "preserve_fields": preserve_fields or [],
        "replace_fields": replace_fields or [],
        "source_artifact_id": candidate.artifact_id,
        "prompt": shot.visual_prompt or shot.first_frame_prompt,
        "candidate_count": 1,
        "estimated_cost": str(quote.quoted_cost or 0),
        "quote_snapshot": {
            "quote_id": quote.quote_id,
            "price": str(quote.quoted_cost or 0),
            "currency": quote.currency,
            "price_table_version": quote.price_table_version,
        },
    }
    key = f"regenerate:{shot.id}:{candidate.id}:{quote.id}"
    job = await job_service.create_job(
        session,
        operation=operation,
        project_id=candidate.project_id,
        shot_id=shot.id,
        params=params,
        idempotency_key=key,
    )
    quote.job_id = job.id
    quote.status = "queued"
    quote.requested_by = requested_by
    await session.flush()
    params["regeneration_request_id"] = quote.id
    job.params = params
    candidate.status = "superseded" if candidate.status == "approved" else candidate.status
    await session.flush()
    return job


async def quote_regeneration(
    session: AsyncSession,
    *,
    candidate: ArtifactCandidate,
    reason: str,
    instruction: str = "",
    preserve_fields: Optional[list[str]] = None,
    replace_fields: Optional[list[str]] = None,
    requested_by: Optional[int] = None,
) -> RegenerationRequest:
    if reason not in VALID_REASONS:
        raise ValueError(f"invalid regeneration reason: {reason}")
    artifact = await session.get(Artifact, candidate.artifact_id)
    shot = await session.get(Shot, candidate.shot_id)
    if artifact is None or shot is None:
        raise ValueError("candidate target not found")
    source_job = await session.get(GenerationJob, artifact.job_id) if artifact.job_id else None
    operation = "generate_image" if artifact.type == "image" else "generate_video"
    estimate_target = SimpleNamespace(
        operation=operation,
        provider=(source_job.provider if source_job else ""),
        model=(source_job.model if source_job else ""),
        params={
            **(source_job.params if source_job else {}),
            "duration_ms": shot.duration_ms,
            "candidate_count": 1,
        },
    )
    quoted_cost = cost_service.estimate_job_retry_cost(estimate_target)
    request = RegenerationRequest(
        project_id=candidate.project_id,
        shot_id=shot.id,
        candidate_id=candidate.id,
        source_artifact_id=candidate.artifact_id,
        reason_code=reason,
        instruction=instruction,
        preserve_fields=preserve_fields or [],
        replace_fields=replace_fields or [],
        status="quoted",
        quote_id=str(uuid.uuid4()),
        quoted_cost=quoted_cost,
        currency="CNY",
        price_table_version=PRICE_TABLE_VERSION,
        quote_expires_at=_now() + timedelta(minutes=30),
        requested_by=requested_by,
    )
    session.add(request)
    await session.flush()
    return request
