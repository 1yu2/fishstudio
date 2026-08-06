"""系列剧（series）路由：项目 / 分镜 / 资产 / 审批 / 生成 Gate。

错误映射约定：PermissionError → 403；目标不存在 → 404；参数/版本错误 → 400。
"""

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, require_admin
from app.db.database import get_session
from app.db.models import (
    Artifact,
    AssetCard,
    CloneVoiceQuoteRecord,
    Episode,
    GenerationJob,
    SeriesProject,
    UsageEvent,
    User,
    WorkflowRun,
    WorkflowStep,
)
from app.schemas.series import (
    ApprovalOut,
    AssetCardCreate,
    AssetCardOut,
    ProjectCreate,
    ProjectOut,
    StoryboardRevisionOut,
)
from app.services import (
    approval_service,
    asset_service,
    feedback_service,
    series_service,
    storyboard_service,
    workflow_service,
)

router = APIRouter(dependencies=[Depends(get_current_user)])


def _403(e: PermissionError) -> HTTPException:
    return HTTPException(status_code=403, detail=str(e) or "无权访问")


async def _owned_project(session: AsyncSession, project_id: str, user: User):
    try:
        project = await series_service.get_project(
            session, project_id, user.id, is_admin=user.is_admin
        )
    except PermissionError as e:
        raise _403(e)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


async def _owned_episode_project(session: AsyncSession, episode_id: str, user: User):
    episode = await session.get(Episode, episode_id)
    if episode is None:
        raise HTTPException(status_code=404, detail="集不存在")
    return episode, await _owned_project(session, episode.project_id, user)


# ─────────────────────────────────────────
# 项目
# ─────────────────────────────────────────


@router.post("/series/projects", response_model=ProjectOut)
async def create_project(
    body: ProjectCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    try:
        return await series_service.create_project(
            session,
            user.id,
            canvas_id=body.canvas_id,
            project_type=body.project_type,
            title=body.title,
            workflow_template_id=body.workflow_template_id,
            active_skill_ids=body.active_skill_ids,
            settings=body.settings,
        )
    except PermissionError as e:
        raise _403(e)
    except ValueError as e:
        # canvas 不存在按 404，其余（如已绑定其他项目）按 400
        status_code = 404 if "不存在" in str(e) else 400
        raise HTTPException(status_code=status_code, detail=str(e))


@router.get("/series/projects", response_model=list[ProjectOut])
async def list_projects(
    project_type: Optional[str] = None,
    status: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    return await series_service.list_projects(
        session, user.id, project_type=project_type, status=status
    )


@router.get("/series/projects/{project_id}")
async def get_project(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    project = await _owned_project(session, project_id, user)
    episodes = await storyboard_service.list_episodes_with_storyboard(session, project.id)
    return {**ProjectOut.model_validate(project).model_dump(mode="json"), "episodes": episodes}


class ClaimRequest(BaseModel):
    user_id: int


@router.post("/series/projects/{project_id}/claim")
async def claim_project(
    project_id: str,
    body: ClaimRequest,
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """认领历史无主项目（review 0.4）：仅 admin；仅 user_id 为 NULL 的项目可认领。"""
    project = await session.get(SeriesProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if project.user_id is not None:
        raise HTTPException(status_code=409, detail="项目已有归属，无需认领")
    target = await session.get(User, body.user_id)
    if target is None:
        raise HTTPException(status_code=400, detail=f"user {body.user_id} 不存在")
    project.user_id = body.user_id
    await session.commit()
    return {"id": project.id, "user_id": project.user_id, "claimed_by": admin.id}


# ─────────────────────────────────────────
# 分镜
# ─────────────────────────────────────────


@router.get("/series/projects/{project_id}/storyboard")
async def get_storyboard(
    project_id: str,
    episode_id: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    project = await _owned_project(session, project_id, user)
    return await storyboard_service.list_episodes_with_storyboard(
        session, project.id, episode_id=episode_id
    )


class StoryboardSaveRequest(BaseModel):
    payload: dict[str, Any]


@router.put("/series/episodes/{episode_id}/storyboard", response_model=StoryboardRevisionOut)
async def save_storyboard(
    episode_id: str,
    body: StoryboardSaveRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _, project = await _owned_episode_project(session, episode_id, user)
    try:
        return await storyboard_service.save_storyboard(
            session, project, episode_id, body.payload, created_by=str(user.id)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class ImportTextRequest(BaseModel):
    text: str


@router.post(
    "/series/episodes/{episode_id}/storyboard/import-text",
    response_model=StoryboardRevisionOut,
)
async def import_storyboard_text(
    episode_id: str,
    body: ImportTextRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _, project = await _owned_episode_project(session, episode_id, user)
    try:
        return await storyboard_service.import_legacy_text(
            session, project, episode_id, body.text, created_by=str(user.id)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class ShotUpdateRequest(BaseModel):
    duration_ms: Optional[int] = None
    visual_prompt: Optional[str] = None
    camera: Optional[dict[str, Any]] = None
    transition: Optional[dict[str, Any]] = None
    aspect_ratio: Optional[str] = None
    dialogue_tracks: Optional[list[Any]] = None
    narration_tracks: Optional[list[Any]] = None
    subtitle_tracks: Optional[list[Any]] = None
    status: Optional[str] = None


@router.patch("/series/shots/{shot_id}")
async def update_shot(
    shot_id: str,
    body: ShotUpdateRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="没有可更新的字段")
    try:
        shot = await storyboard_service.update_shot(session, shot_id, fields, user.id)
    except PermissionError as e:
        raise _403(e)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "id": shot.id,
        "scene_id": shot.scene_id,
        "shot_no": shot.shot_no,
        "duration_ms": shot.duration_ms,
        "visual_prompt": shot.visual_prompt,
        "status": shot.status,
    }


# ─────────────────────────────────────────
# 资产
# ─────────────────────────────────────────


@router.post("/series/projects/{project_id}/assets", response_model=AssetCardOut)
async def create_asset(
    project_id: str,
    body: AssetCardCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    project = await _owned_project(session, project_id, user)
    return await asset_service.create_asset_card(
        session,
        project.id,
        user.id,
        type=body.type,
        name=body.name,
        description=body.description,
        provider_meta=body.provider_meta,
        tags=body.tags,
        scope=body.scope,
        source=body.source,
    )


@router.get("/series/projects/{project_id}/assets", response_model=list[AssetCardOut])
async def list_assets(
    project_id: str,
    type: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
    include_personal: bool = False,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """项目资产列表；include_personal=true 时合并返回当前用户的个人资产
    （scope=personal，供工作台"项目资产/个人资产"切换）。"""
    project = await _owned_project(session, project_id, user)
    cards = await asset_service.list_assets(session, project_id=project.id, type=type, tag=tag, q=q)
    if include_personal:
        cards += await asset_service.list_assets(
            session, user_id=user.id, scope="personal", type=type, tag=tag, q=q
        )
    return cards


class NewVersionRequest(BaseModel):
    updates: dict[str, Any] = Field(default_factory=dict)


@router.post("/series/assets/{asset_id}/versions", response_model=AssetCardOut)
async def new_asset_version(
    asset_id: str,
    body: NewVersionRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    asset = await asset_service.get_asset_for_user(session, asset_id, user.id)
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权访问")
    try:
        return await asset_service.new_asset_version(session, asset_id, user.id, body.updates)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class BindShotRequest(BaseModel):
    shot_id: str
    role: str = "character"


@router.post("/series/assets/{asset_id}/bind-shot")
async def bind_asset_to_shot(
    asset_id: str,
    body: BindShotRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    asset = await asset_service.get_asset_for_user(session, asset_id, user.id)
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在或无权访问")
    try:
        ref = await asset_service.bind_asset_to_shot(
            session, body.shot_id, asset_id, role=body.role
        )
    except PermissionError as e:
        raise _403(e)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"id": ref.id, "shot_id": ref.shot_id, "asset_id": ref.asset_id, "role": ref.role}


# ─────────────────────────────────────────
# 审批 / 生成 Gate
# ─────────────────────────────────────────


class ApprovalRequest(BaseModel):
    target_type: str  # storyboard | asset
    target_id: str
    target_revision: int
    action: str  # approve | reject
    comment: str = ""
    project_id: str


@router.post("/series/approvals", response_model=ApprovalOut)
async def submit_approval(
    body: ApprovalRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    project = await _owned_project(session, body.project_id, user)
    fn = {"approve": approval_service.approve, "reject": approval_service.reject}.get(body.action)
    if fn is None:
        raise HTTPException(status_code=400, detail="action 必须是 approve 或 reject")
    try:
        result = await fn(
            session,
            project_id=project.id,
            target_type=body.target_type,
            target_id=body.target_id,
            target_revision=body.target_revision,
            approved_by=user.id,
            comment=body.comment,
        )
    except PermissionError as e:
        raise _403(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 审批可能放行 waiting_approval 的 Gate 节点：对项目内未终态 run 触发重估
    active_runs = (
        (
            await session.execute(
                select(WorkflowRun).where(
                    WorkflowRun.project_id == project.id,
                    WorkflowRun.status.notin_({"succeeded", "failed", "canceled", "paused"}),
                )
            )
        )
        .scalars()
        .all()
    )
    for run in active_runs:
        await workflow_service.advance_run(session, run.id)
    return result


@router.get("/series/projects/{project_id}/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    project = await _owned_project(session, project_id, user)
    return await approval_service.list_approvals(session, project.id)


@router.get("/series/episodes/{episode_id}/generation-gate")
async def generation_gate(
    episode_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    _, project = await _owned_episode_project(session, episode_id, user)
    ok, reasons = await approval_service.check_generation_gate(session, project.id, episode_id)
    return {"ok": ok, "reasons": reasons}


# ─────────────────────────────────────────
# Artifact 候选与反馈（P2-2）
# ─────────────────────────────────────────


async def _owned_shot_project(session: AsyncSession, shot_id: str, user: User):
    """shot → project 归属校验（不存在或无权 → 404）。"""
    project_id = await asset_service._shot_project_id(session, shot_id)
    if project_id is None:
        raise HTTPException(status_code=404, detail="镜头不存在")
    return await _owned_project(session, project_id, user)


@router.get("/series/shots/{shot_id}/artifacts")
async def list_shot_artifacts(
    shot_id: str,
    operation: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """该 shot 的生成产物候选列表（经 job.shot_id 反查，含 provider/model/url）。"""
    await _owned_shot_project(session, shot_id, user)
    stmt = (
        select(Artifact, GenerationJob)
        .join(GenerationJob, Artifact.job_id == GenerationJob.id)
        .where(GenerationJob.shot_id == shot_id)
        .order_by(Artifact.created_at.desc())
    )
    if operation:
        stmt = stmt.where(GenerationJob.operation == operation)
    rows = (await session.execute(stmt)).all()
    return [
        {
            "artifact_id": a.id,
            "type": a.type,
            "url": asset_service.artifact_url(a),
            "job_id": j.id,
            "operation": j.operation,
            "provider": j.provider,
            "model": j.model,
            "job_status": j.status,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a, j in rows
    ]


class FeedbackRequest(BaseModel):
    action: str  # selected | rejected | rated
    score: Optional[int] = None
    reason: str = ""
    step_id: Optional[str] = None


@router.post("/series/artifacts/{artifact_id}/feedback")
async def submit_feedback(
    artifact_id: str,
    body: FeedbackRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """写反馈；action=selected 且给 step_id 时同步更新 step.selected_artifact_id
    （简单语义：直接覆盖旧选中，历史经 feedback 记录追溯）。"""
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None or artifact.project_id is None:
        raise HTTPException(status_code=404, detail="产物不存在")
    await _owned_project(session, artifact.project_id, user)
    try:
        feedback = await feedback_service.record_feedback(
            session,
            artifact=artifact,
            user_id=user.id,
            action=body.action,
            score=body.score,
            reason=body.reason,
            step_id=body.step_id,
        )
    except PermissionError as e:
        raise _403(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "id": feedback.id,
        "artifact_id": feedback.artifact_id,
        "action": feedback.action,
        "score": feedback.score,
        "step_id": feedback.step_id,
    }


@router.get("/series/projects/{project_id}/feedback")
async def list_project_feedback(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    project = await _owned_project(session, project_id, user)
    rows = await feedback_service.list_feedback(session, project.id)
    return [
        {
            "id": f.id,
            "artifact_id": f.artifact_id,
            "step_id": f.step_id,
            "action": f.action,
            "score": f.score,
            "reason": f.reason,
            "user_id": f.user_id,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in rows
    ]


# ─────────────────────────────────────────
# 个人资产库（P2-3）
# ─────────────────────────────────────────


class CopyToProjectRequest(BaseModel):
    project_id: str


@router.post("/series/assets/{asset_id}/copy-to-project", response_model=AssetCardOut)
async def copy_asset_to_project(
    asset_id: str,
    body: CopyToProjectRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """把个人资产（scope=personal）复制进项目：新行 version=1、source=原资产 id，
    项目内版本演进独立于个人库（锁定版本语义见 service 注释）。"""
    await _owned_project(session, body.project_id, user)
    try:
        return await asset_service.copy_personal_asset_to_project(
            session, asset_id, body.project_id, user.id
        )
    except PermissionError as e:
        raise _403(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ─────────────────────────────────────────
# 音色复刻（R6.4：Cost Gate 安全版）
# ─────────────────────────────────────────


class CloneVoiceRequest(BaseModel):
    reference_audio_artifact_id: str
    voice_name: str = ""
    confirmed_quote_id: str = ""


class CloneVoiceQuote(BaseModel):
    quote_id: str
    provider: str
    price: str  # CNY
    currency: str = "CNY"
    expires_at: str
    reference_artifact_id: str
    voice_asset_id: str


def _clone_quote_token(quote_id: str) -> str:
    import hashlib
    import hmac
    import os

    secret = os.getenv("CLONE_QUOTE_SECRET", os.getenv("JWT_SECRET", "dev-secret-change-me"))
    signature = hmac.new(secret.encode(), quote_id.encode(), hashlib.sha256).hexdigest()
    return f"vcq:{quote_id}:{signature}"


def _parse_clone_quote_token(token: str) -> str:
    import hmac

    parts = token.split(":")
    if len(parts) != 3 or parts[0] != "vcq":
        raise HTTPException(status_code=400, detail="无效的 quote_id 格式")
    quote_id, signature = parts[1], parts[2]
    expected = _clone_quote_token(quote_id).rsplit(":", 1)[1]
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400, detail="quote 签名验证失败，请重新获取")
    return quote_id


@router.get("/series/projects/{project_id}/assets/{asset_id}/clone-voice-quote")
async def get_clone_voice_quote(
    project_id: str,
    asset_id: str,
    reference_audio_artifact_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """返回音色复刻报价（不创建 Job）。用户确认后带 quote_id 调用 clone-voice。"""
    project = await _owned_project(session, project_id, user)
    asset = await session.get(AssetCard, asset_id)
    if asset is None or asset.project_id != project_id:
        raise HTTPException(status_code=404, detail="voice 资产不存在")
    if asset.type != "voice":
        raise HTTPException(status_code=400, detail="仅 voice 类型资产支持音色复刻")

    from decimal import Decimal

    from app.providers.pricing import PRICE_TABLE_VERSION, VOICE_ENROLL_PRICE
    from app.providers.tts_base import QwenTTSProvider

    qwen = QwenTTSProvider()
    if not qwen.is_available():
        raise HTTPException(status_code=503, detail="TTS Provider 不可用，无法报价")

    # R8.5：签名绑定业务字段，防止跨资产重放
    ref_artifact = await session.get(Artifact, reference_audio_artifact_id)
    if ref_artifact is None or ref_artifact.project_id != project_id:
        raise HTTPException(status_code=404, detail="参考音频产物不存在或不属于当前项目")
    if ref_artifact.type != "audio":
        raise HTTPException(status_code=400, detail="参考音频产物类型须为 audio")

    import datetime as _dt
    import uuid as _uuid

    price = VOICE_ENROLL_PRICE.get("qwen_tts", Decimal("2.00"))
    now = _dt.datetime.now(_dt.timezone.utc)
    expires_at = now + _dt.timedelta(minutes=30)
    quote = CloneVoiceQuoteRecord(
        id=str(_uuid.uuid4()),
        nonce=_uuid.uuid4().hex,
        user_id=user.id,
        project_id=project_id,
        asset_id=asset_id,
        asset_version=asset.version,
        reference_artifact_id=ref_artifact.id,
        provider=qwen.name,
        model="qwen-tts-voice-cloning",
        price=price,
        currency="CNY",
        price_table_version=PRICE_TABLE_VERSION,
        expires_at=expires_at,
    )
    session.add(quote)
    await session.commit()
    return {
        "quote_id": _clone_quote_token(quote.id),
        "provider": qwen.name,
        "price": str(price),
        "currency": "CNY",
        "price_table_version": PRICE_TABLE_VERSION,
        "expires_at": expires_at.isoformat(),
        "voice_asset_id": asset_id,
        "voice_asset_version": asset.version,
        "reference_artifact_id": ref_artifact.id,
        "project_id": project_id,
        "user_id": user.id,
        "nonce": quote.nonce,
    }


@router.post("/series/projects/{project_id}/artifacts/upload-audio")
async def upload_project_audio(
    project_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """上传项目级参考音频并创建受保护 Artifact，供 clone_voice 报价使用。"""
    project = await _owned_project(session, project_id, user)
    original_name = Path(file.filename or "reference.wav").name
    extension = Path(original_name).suffix.lower()
    allowed_extensions = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".wma"}
    content_type = (file.content_type or "").lower()
    if extension not in allowed_extensions:
        raise HTTPException(status_code=400, detail="仅支持 wav/mp3/m4a/aac/ogg/flac/wma 音频")
    if content_type and not content_type.startswith("audio/") and content_type != "application/octet-stream":
        raise HTTPException(status_code=400, detail="上传文件不是音频类型")

    import uuid

    audio_dir = asset_service.STORAGE_DIR / "audios"
    audio_dir.mkdir(parents=True, exist_ok=True)
    filename = f"reference_{uuid.uuid4().hex}{extension}"
    output_path = audio_dir / filename
    max_bytes = 25 * 1024 * 1024
    total_bytes = 0
    try:
        with output_path.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise HTTPException(status_code=413, detail="参考音频不能超过 25MB")
                output.write(chunk)
        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="参考音频不能为空")
        artifact = await asset_service.create_artifact(
            session,
            project_id=project.id,
            type="audio",
            storage_key=f"audios/{filename}",
            mime_type=content_type or "audio/wav",
            meta={
                "source": "voice_reference_upload",
                "original_filename": original_name,
                "user_id": user.id,
            },
        )
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    return {
        "artifact_id": artifact.id,
        "type": artifact.type,
        "filename": original_name,
        "size": total_bytes,
        "url": asset_service.artifact_url(artifact),
    }


@router.post("/series/projects/{project_id}/assets/{asset_id}/clone-voice")
async def create_clone_voice_job(
    project_id: str,
    asset_id: str,
    body: CloneVoiceRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """创建 clone_voice Job（R6.4：必须确认报价后携带 quote_id）。"""
    project = await _owned_project(session, project_id, user)
    asset = await session.get(AssetCard, asset_id, with_for_update=True)
    if asset is None or asset.project_id != project_id:
        raise HTTPException(status_code=404, detail="voice 资产不存在")
    if asset.type != "voice":
        raise HTTPException(status_code=400, detail="仅 voice 类型资产支持音色复刻")

    if not body.confirmed_quote_id:
        raise HTTPException(status_code=400, detail="请先获取 clone-voice-quote 并确认费用后携带 confirmed_quote_id")
    import datetime as _dt
    from decimal import Decimal

    from app.services import cost_service

    quote_id = _parse_clone_quote_token(body.confirmed_quote_id)
    quote = await session.get(CloneVoiceQuoteRecord, quote_id, with_for_update=True)
    if quote is None:
        raise HTTPException(status_code=400, detail="quote 不存在或已失效，请重新获取")
    expires_at = quote.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=_dt.timezone.utc)
    now = _dt.datetime.now(_dt.timezone.utc)
    if now > expires_at:
        raise HTTPException(status_code=400, detail="quote 已过期，请重新获取")
    if (
        quote.user_id != user.id
        or quote.project_id != project_id
        or quote.asset_id != asset_id
        or quote.asset_version != asset.version
        or quote.reference_artifact_id != body.reference_audio_artifact_id
    ):
        raise HTTPException(status_code=400, detail="quote 业务字段不匹配，请重新获取")
    if quote.consumed_at is not None:
        if quote.consumed_job_id:
            return {"job_id": quote.consumed_job_id, "status": "exists"}
        raise HTTPException(status_code=409, detail="quote 已消费，请重新获取")

    ref_artifact = await session.get(Artifact, body.reference_audio_artifact_id)
    if ref_artifact is None or ref_artifact.project_id != project_id:
        raise HTTPException(status_code=404, detail="参考音频产物不存在或不属于当前项目")
    if ref_artifact.type != "audio":
        raise HTTPException(status_code=400, detail="参考音频产物类型须为 audio")

    storage_key = ref_artifact.storage_key
    from app.services.asset_service import STORAGE_DIR
    storage_root = STORAGE_DIR.resolve()
    ref_path = (storage_root / storage_key).resolve()
    if not ref_path.is_relative_to(storage_root) or not ref_path.is_file():
        raise HTTPException(status_code=400, detail="参考音频路径非法或文件不存在")

    idem_key = f"clone_voice:{asset_id}:{asset.version}:{ref_artifact.id}"
    existing = await session.scalar(
        select(GenerationJob.id).where(GenerationJob.idempotency_key == idem_key)
    )
    if existing:
        quote.consumed_at = now
        quote.consumed_job_id = existing
        await session.commit()
        return {"job_id": existing, "status": "exists"}

    price = Decimal(str(quote.price))
    quote_snapshot = {
        "quote_id": quote.id,
        "provider": quote.provider,
        "model": quote.model,
        "price": str(price),
        "currency": quote.currency,
        "price_table_version": quote.price_table_version,
        "expires_at": expires_at.isoformat(),
        "reference_artifact_id": quote.reference_artifact_id,
        "asset_id": quote.asset_id,
        "asset_version": quote.asset_version,
    }
    run = WorkflowRun(
        project_id=project_id,
        status="queued",
        trigger_type="api",
        input_snapshot={"operation": "clone_voice", "cost_confirmed": True, "cost_quote": quote_snapshot},
        estimated_cost=Decimal("0"),
        cost_limit=price,
        created_by=user.id,
    )
    session.add(run)
    await session.flush()
    step = WorkflowStep(
        run_id=run.id,
        node_key="asset.voice.clone",
        node_type="asset.voice.clone",
        entity_type="asset",
        entity_id=asset_id,
        status="queued",
        input_snapshot=quote_snapshot,
    )
    session.add(step)
    await session.flush()
    if not await cost_service.reserve_budget(session, run.id, price):
        await session.rollback()
        raise HTTPException(status_code=409, detail="clone_voice 预算预留失败")

    job = GenerationJob(
        project_id=project_id,
        operation="clone_voice",
        provider=quote.provider,
        model=quote.model,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        params={
            "asset_id": asset_id,
            "reference_audio_storage_key": storage_key,
            "reference_audio_artifact_id": ref_artifact.id,
            "voice_name": body.voice_name or asset.name,
            "confirmed_quote_id": body.confirmed_quote_id,
            "quote_snapshot": quote_snapshot,
            "estimated_cost": str(price),
            "currency": quote.currency,
            "price_table_version": quote.price_table_version,
            "step_id": step.id,
            "run_id": run.id,
        },
        idempotency_key=idem_key,
        status="pending",
        step_attempt=0,
        logical_job_key=f"clone_voice:{asset_id}",
    )
    session.add(job)
    await session.flush()
    quote.consumed_at = now
    quote.consumed_job_id = job.id
    session.add(
        UsageEvent(
            run_id=run.id,
            job_id=job.id,
            user_id=user.id,
            project_id=project_id,
            operation="clone_voice.reserve",
            provider=quote.provider,
            model=quote.model,
            input_units=1,
            output_units=0,
            unit_type="request",
            unit_price_snapshot=price,
            estimated_cost=price,
            actual_cost=None,
            cost_source="estimate",
            currency=quote.currency,
            status="reserved",
            event_metadata={"quote_id": quote.id, "price_table_version": quote.price_table_version},
        )
    )
    await session.commit()
    return {"job_id": job.id, "run_id": run.id, "status": "pending"}
