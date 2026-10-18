"""Job 展开（plan 层）：把 creates_jobs 的 WorkflowStep 展开为 GenerationJob 落库。

本模块只负责"规划并落库"（status=pending），不执行——Worker/Provider 层后续任务实现。
幂等：idempotency_key 由 run/step/shot/attempt 决定，重复展开直接复用已有 Job。
"""

import logging
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Artifact,
    AssetCard,
    Episode,
    GenerationJob,
    Scene,
    Shot,
    StoryboardRevision,
    WorkflowRun,
    WorkflowStep,
)
from app.providers.pricing import PRICE_TABLE_VERSION

logger = logging.getLogger(__name__)

JOB_TERMINAL_STATUSES = {"succeeded", "failed", "canceled"}


def make_job_idempotency_key(operation: str, run_id: str, node_key: str, shot_id: str, attempt: int) -> str:
    """稳定的幂等键（纯函数，供测试锚定格式）。"""
    return f"{operation}:{run_id}:{node_key}:{shot_id}:attempt{attempt}"


def make_logical_job_key(operation: str, run_id: str, node_key: str, unit_id: str) -> str:
    """同一逻辑任务的多代重试共享的 key（不含 attempt，纯函数，供测试锚定格式）。

    unit_id：shot_id，或 tts 的 track 标识（{shot_id}:{track_type}{index}），
    或 render 的 episode_id。
    """
    return f"{operation}:{run_id}:{node_key}:{unit_id}"


async def _current_revision_shots(session: AsyncSession, episode_id: str) -> list[Shot]:
    """取该集最新 revision 物化出的 shots（按场景/镜头序）。"""
    stmt = (
        select(StoryboardRevision)
        .where(StoryboardRevision.episode_id == episode_id)
        .order_by(StoryboardRevision.revision.desc())
        .limit(1)
    )
    revision = (await session.execute(stmt)).scalars().first()
    if revision is None:
        return []
    stmt = (
        select(Shot)
        .join(Scene, Shot.scene_id == Scene.id)
        .where(Shot.storyboard_revision_id == revision.id)
        .order_by(Scene.sort_order, Shot.sort_order)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _get_or_create_job(
    session: AsyncSession, *, idempotency_key: str, **fields: Any
) -> tuple[GenerationJob, bool]:
    """按 idempotency_key 查，存在则复用（返回 created=False）。

    复用时回填 workflow_run_id/workflow_step_id（P1-10 前的存量 Job 只有 params JSON）。
    """
    stmt = select(GenerationJob).where(GenerationJob.idempotency_key == idempotency_key)
    existing = (await session.execute(stmt)).scalars().first()
    if existing is not None:
        for key in ("workflow_run_id", "workflow_step_id"):
            if fields.get(key) and not getattr(existing, key):
                setattr(existing, key, fields[key])
                await session.flush()
        return existing, False
    job = GenerationJob(idempotency_key=idempotency_key, status="pending", **fields)
    session.add(job)
    await session.flush()
    from app.services import job_event_service

    await job_event_service.emit(session, job, "job.queued")
    await job_event_service.notify_ready(job.id)
    return job, True


def _run_policy(run: WorkflowRun) -> dict[str, Any]:
    return (run.input_snapshot or {}).get("default_policy") or {}


async def _reserve_budget_or_fail(
    session: AsyncSession, run: WorkflowRun, step: WorkflowStep, estimated: Decimal
) -> bool:
    """原子预算预留（P1-5）：行锁内检查并累加 run.estimated_cost；超限则
    step 标 failed(cost_limit_exceeded) 且不累加。返回是否可继续展开。"""
    from app.services import cost_service  # 延迟导入避免环（cost_service 依赖本模块）

    if not await cost_service.reserve_budget(session, run.id, estimated):
        step.status = "failed"
        step.error_code = "cost_limit_exceeded"
        step.error = (
            f"planned {run.estimated_cost or 0} + estimated {estimated} > cost_limit {run.cost_limit}"
        )
        return False
    return True


async def _cost_quote_still_valid(session: AsyncSession, run: WorkflowRun) -> bool:
    """展开 job 前的防御性 quote 复核（复审 0.6，防 advance 之外的直接调用路径）。

    只在 run 已确认费用（cost_confirmed=True）时校验：此时确认绑定的 quote
    必须仍有效（未过期/版本一致/input_hash 一致）；未确认时由 advance_run 的
    费用 Gate 负责拦截，planner 不重复拦截（避免阻塞测试/内部直接展开路径）。
    """
    snapshot = run.input_snapshot or {}
    if snapshot.get("cost_confirmed") is not True:
        return True
    from app.services import cost_service  # 延迟导入避免环

    quote = snapshot.get("cost_quote") or {}
    ok, _reason = await cost_service.validate_cost_quote(session, run, quote.get("quote_id", ""))
    return ok


async def _selected_shot_artifact(
    session: AsyncSession, shot_id: str, artifact_type: str
) -> Optional[Artifact]:
    """Shot 选择槽对应的 artifact，校验后返回；无效返回 None。

    复审 0.8：图片用 selected_image_artifact_id，视频用 selected_video_artifact_id，
    不再共用一个槽导致互相覆盖。
    """
    shot = await session.get(Shot, shot_id)
    if shot is None:
        return None
    artifact_id = None
    if artifact_type == "image":
        artifact_id = shot.selected_image_artifact_id
    elif artifact_type == "video":
        artifact_id = shot.selected_video_artifact_id
    if not artifact_id:
        return None
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None or artifact.type != artifact_type or not artifact.job_id:
        return None
    job = await session.get(GenerationJob, artifact.job_id)
    if job is None or job.shot_id != shot_id:
        return None
    return artifact


async def find_first_frame_storage_key(session: AsyncSession, shot_id: str) -> Optional[str]:
    """该 shot 的首帧图 storage_key：用户选定产物优先，回退最新 succeeded 图片产物。

    1. Shot.selected_artifact_id 对应的 image artifact（须属于该 shot 的 job 产物，
       feedback selected 写入，P2-2 消费侧）；
    2. 回退：最新 succeeded generate_image Job 产出的图片 Artifact（P1-2）。
    """
    selected = await _selected_shot_artifact(session, shot_id, "image")
    if selected is not None:
        return selected.storage_key
    stmt = (
        select(Artifact.storage_key)
        .join(GenerationJob, Artifact.job_id == GenerationJob.id)
        .where(
            GenerationJob.shot_id == shot_id,
            GenerationJob.operation == "generate_image",
            GenerationJob.status == "succeeded",
            Artifact.type == "image",
        )
        .order_by(Artifact.created_at.desc())
        .limit(1)
    )
    return await session.scalar(stmt)


def _confirmed_shot_plans(run: WorkflowRun, node_key: str) -> list[dict]:
    """R8.3/R9.7：从已确认 CostQuote 中提取 step 对应的 Shot Route Plans。仅 cost_confirmed 时有效。"""
    snapshot = run.input_snapshot or {}
    if snapshot.get("cost_confirmed") is not True:
        return []
    quote = snapshot.get("cost_quote") or {}
    for item in quote.get("items", []):
        if item.get("step_node_key") == node_key:
            return item.get("route_plans", [])
    return []


def build_video_job_params(
    *,
    shot_id: str,
    step_id: str,
    run_id: str,
    prompt: str,
    duration_ms: int,
    aspect_ratio: str,
    policy: dict[str, Any],
    first_frame: Optional[str],
    routing: Optional[dict[str, Any]] = None,
    compiled: Optional[Any] = None,
) -> dict[str, Any]:
    """视频 Job params 纯函数（P1-2）：有首帧 → first_frame 模式；无 → 降级 text 并记原因。

    缺少首帧时不得创建 first_frame 视频 Job（review P1-2），降级为文生视频并在
    params.fallback_reason 留痕。

    R1.4：routing 快照（provider/model/resolution/price_snapshot）锁定到 Job params，
    Worker 后续按快照执行，不再动态路由。
    """
    params: dict[str, Any] = {
        "shot_id": shot_id,
        "step_id": step_id,
        "run_id": run_id,
        "prompt": prompt,
        "duration_ms": duration_ms,
        "resolution": policy.get("resolution", "768P"),
        "aspect_ratio": aspect_ratio,
        "provider_policy": policy.get("provider_policy", "balanced"),
    }
    if routing:
        params["routing_snapshot"] = routing
        params["resolution"] = routing.get("resolution", params["resolution"])
    if compiled:
        params.update(
            {
                "prompt": compiled.prompt,
                "prompt_version": compiled.prompt_version,
                "prompt_input_hash": compiled.input_hash,
                "reference_slots": compiled.reference_slots,
                "reference_images": list(compiled.reference_images),
                "reference_videos": list(compiled.reference_videos),
                "reference_audios": list(compiled.reference_audios),
            }
        )
        first_frame = compiled.first_frame or first_frame
        if compiled.last_frame:
            params["last_frame"] = compiled.last_frame
        if compiled.source_video:
            params["source_video"] = compiled.source_video
    if params.get("source_video"):
        params["mode"] = "regenerate"
    elif first_frame and params.get("last_frame"):
        params["mode"] = "first_last_frame"
        params["first_frame"] = first_frame
    elif first_frame:
        params["mode"] = "first_frame"
        params["first_frame"] = first_frame  # storage_key，provider adapter 转 data URL
    elif params.get("reference_images") or params.get("reference_videos") or params.get("reference_audios"):
        params["mode"] = "reference"
    else:
        params["mode"] = "text"
        params["fallback_reason"] = "first_frame_missing"
    return params


async def load_shot_video_inputs(session: AsyncSession, shot: Shot) -> tuple[Optional[str], Any]:
    """读取镜头的首帧与编译后的参考输入；报价与规划共用，保证两者按同一请求形态路由。"""
    from app.services.prompt_compiler import compile_shot_input

    first_frame = await find_first_frame_storage_key(session, shot.id)
    compiled = await compile_shot_input(session, shot)
    return first_frame, compiled


async def resolve_shot_video_plan(
    session: AsyncSession,
    shot: Shot,
    policy: dict[str, Any],
    *,
    first_frame: Optional[str],
    compiled: Any,
) -> dict[str, Any]:
    """按镜头的真实请求形态（文生 / 首帧 / 首尾帧 / 参考）选择视频 Deployment 并报价。

    policy.video_tier 选择质量档位（draft / preview / final，默认 final）。
    """
    from app.model_runtime.video import resolve_video_plan

    shape = build_video_job_params(
        shot_id=shot.id,
        step_id="",
        run_id="",
        prompt=shot.visual_prompt or "",
        duration_ms=shot.duration_ms,
        aspect_ratio=shot.aspect_ratio,
        policy=policy,
        first_frame=first_frame,
        compiled=compiled,
    )
    return await resolve_video_plan(
        session,
        duration=int(max(1, shot.duration_ms) / 1000),
        resolution=policy.get("resolution", "768P"),
        tier=policy.get("video_tier", "final"),
        mode=shape["mode"],
        aspect_ratio=shot.aspect_ratio or "9:16",
        reference_count=len(shape.get("reference_images") or []),
        explicit_deployment=policy.get("explicit_deployment") or policy.get("explicit_provider", ""),
        provider_policy=policy.get("provider_policy", "balanced"),
    )


async def plan_shot_video_jobs(
    session: AsyncSession, *, run: WorkflowRun, step: WorkflowStep, episode_id: str
) -> list[GenerationJob]:
    """按当前 revision 的 shots 展开 generate_video Job（每 shot 一个）。

    首帧策略（P1-2）：查到首帧图 → mode=first_frame 且 params.first_frame 写
    storage_key（provider adapter 负责转 data URL）；查不到 → 不创建 first_frame
    Job，降级 mode="text" 并在 params.fallback_reason 记录原因。
    """
    shots = await _current_revision_shots(session, episode_id)
    policy = _run_policy(run)
    from app.model_runtime.video import VideoRoutingError

    # 每个镜头只读取一次首帧与参考输入，路由与 Job 参数使用同一份。
    inputs = {shot.id: await load_shot_video_inputs(session, shot) for shot in shots}

    # R8.3：优先消费已确认 Quote 中的路由计划，不再独立解析
    confirmed_plans = _confirmed_shot_plans(run, step.node_key)

    estimated = Decimal("0")
    shot_plans: dict[str, dict] = {}
    if confirmed_plans:
        for plan in confirmed_plans:
            shot_plans[plan.get("shot_id")] = plan
            cost = plan.get("estimated_total_cost", "0")
            estimated += Decimal(cost) if cost else Decimal("0")
    else:
        for s in shots:
            first_frame, compiled = inputs[s.id]
            try:
                routing = await resolve_shot_video_plan(
                    session, s, policy, first_frame=first_frame, compiled=compiled
                )
            except VideoRoutingError as error:
                step.status = "failed"
                step.error_code = "no_video_deployment"
                step.error = str(error)
                await session.flush()
                return []
            shot_plans[s.id] = routing
            estimated += Decimal(str(routing["estimated_total_cost"]))
    # R9.7：已确认 Quote 的 route plans 必须覆盖所有 shot，不一致时报失效并重新报价
    if confirmed_plans and len(shot_plans) != len(shots):
        step.status = "failed"
        step.error_code = "cost_quote_invalid"
        step.error = "已确认报价的 Route Plan 与当前分镜不一致，请重新确认费用"
        await session.flush()
        return []
    if not await _cost_quote_still_valid(session, run):
        step.status = "failed"
        step.error_code = "cost_quote_invalid"
        step.error = "费用报价已失效（分镜/参数变化或过期），请重新确认费用"
        await session.flush()
        return []
    if not await _reserve_budget_or_fail(session, run, step, estimated):
        await session.flush()
        return []

    jobs: list[GenerationJob] = []
    fallback_count = 0
    for shot in shots:
        # R8.3：优先使用已确认 Quote plan，不再独立解析
        plan = shot_plans.get(shot.id)
        if plan is None:
            step.status = "failed"
            step.error_code = "cost_quote_invalid"
            step.error = f"已确认报价缺少镜头 {shot.id} 的 Route Plan，请重新确认费用"
            await session.flush()
            return []
        first_frame, compiled = inputs[shot.id]
        if not first_frame and not compiled.first_frame:
            fallback_count += 1
        key = make_job_idempotency_key("video", run.id, step.node_key, shot.id, step.attempt)
        params = build_video_job_params(
            shot_id=shot.id,
            step_id=step.id,
            run_id=run.id,
            prompt=shot.visual_prompt,
            duration_ms=shot.duration_ms,
            aspect_ratio=shot.aspect_ratio,
            policy=policy,
            first_frame=first_frame,
            routing={
                "provider": plan["provider"],
                "model": plan["model"],
                "resolution": plan["resolution"],
                "protocol": plan.get("protocol", ""),
                "billable_duration": plan["billable_duration"],
                "estimated_total_cost": plan.get("estimated_total_cost"),
                "price_snapshot": plan.get("price_snapshot", {}),
                "price_table_version": plan.get("price_table_version", PRICE_TABLE_VERSION),
                "deployment_name": plan.get("deployment_name", ""),
                "deployment_revision": plan.get("deployment_revision"),
                "deployment_config_hash": plan.get("deployment_config_hash", ""),
                "catalog_revision": plan.get("catalog_revision", ""),
                "route": plan.get("route", {}),
            },
            compiled=compiled,
        )
        from app.model_runtime.media import get_media_runtime
        from app.providers.base import VideoGenerationRequest

        runtime = get_media_runtime()
        runtime.video_router()
        # 优先按锁定的 Deployment slug 查找；旧快照只有 Provider 名。
        deployment = runtime.get("video", plan.get("deployment_name") or plan["provider"])
        request_data = {k: v for k, v in params.items() if k in VideoGenerationRequest.model_fields}
        request_data["duration"] = max(1, shot.duration_ms // 1000)
        request = VideoGenerationRequest(**request_data)
        if deployment is None or not deployment.provider.supports(request, model=plan["model"]):
            step.status = "failed"
            step.error_code = "reference_capability_unsupported"
            step.error = f"{plan['provider']}/{plan['model']} 不支持当前 Reference Slots"
            await session.flush()
            return []
        job, _ = await _get_or_create_job(
            session,
            idempotency_key=key,
            project_id=run.project_id,
            shot_id=shot.id,
            workflow_run_id=run.id,
            workflow_step_id=step.id,
            operation="generate_video",
            provider=plan["provider"],
            model=plan["model"],
            step_attempt=step.attempt,
            logical_job_key=make_logical_job_key("generate_video", run.id, step.node_key, shot.id),
            params=params,
        )
        jobs.append(job)
    if fallback_count:
        # step 级留痕：多少 shot 因缺首帧降级为文生视频
        step.output_snapshot = {
            **(step.output_snapshot or {}),
            "first_frame_fallback_count": fallback_count,
        }
    await session.flush()
    return jobs


async def plan_shot_image_jobs(
    session: AsyncSession, *, run: WorkflowRun, step: WorkflowStep, episode_id: str
) -> list[GenerationJob]:
    """按当前 revision 的 shots 展开 generate_image Job（首帧图，每 shot 一个）。"""
    shots = await _current_revision_shots(session, episode_id)
    policy = _run_policy(run)
    candidate_count = int(policy.get("candidate_count", 1))
    from app.providers.pricing import estimate_image_cost

    unit_price = estimate_image_cost("volcano_image", "seedream-4.5", count=1)
    estimated = (unit_price or Decimal("0.20")) * candidate_count * len(shots)
    if not await _cost_quote_still_valid(session, run):
        step.status = "failed"
        step.error_code = "cost_quote_invalid"
        step.error = "费用报价已失效（分镜/参数变化或过期），请重新确认费用"
        await session.flush()
        return []
    if not await _reserve_budget_or_fail(session, run, step, estimated):
        await session.flush()
        return []

    jobs: list[GenerationJob] = []
    for shot in shots:
        from app.services.prompt_compiler import compile_shot_input

        compiled = await compile_shot_input(session, shot)
        key = make_job_idempotency_key("image", run.id, step.node_key, shot.id, step.attempt)
        job, _ = await _get_or_create_job(
            session,
            idempotency_key=key,
            project_id=run.project_id,
            shot_id=shot.id,
            workflow_run_id=run.id,
            workflow_step_id=step.id,
            operation="generate_image",
            step_attempt=step.attempt,
            logical_job_key=make_logical_job_key("generate_image", run.id, step.node_key, shot.id),
            params={
                "shot_id": shot.id,
                "step_id": step.id,
                "run_id": run.id,
                "prompt": compiled.prompt,
                "prompt_version": compiled.prompt_version,
                "prompt_input_hash": compiled.input_hash,
                "reference_slots": compiled.reference_slots,
                "source_image": (compiled.reference_images[0] if compiled.reference_images else None),
                "resolution": policy.get("resolution", "768P"),
                "aspect_ratio": shot.aspect_ratio,
                "candidate_count": candidate_count,
                "provider_policy": policy.get("provider_policy", "balanced"),
            },
        )
        jobs.append(job)
    await session.flush()
    return jobs


async def _find_voice_asset(
    session: AsyncSession, project_id: str, speaker_asset_id: Optional[str]
) -> Optional[AssetCard]:
    """由 track.speaker_asset_id 找对应 voice 资产（type=voice approved）。

    约定：voice 资产的 provider_meta.speaker_asset_id 关联其角色资产；
    找不到返回 None（provider 用默认音色，见 QwenTTSProvider 注释）。
    """
    if not speaker_asset_id:
        return None
    stmt = select(AssetCard).where(
        AssetCard.project_id == project_id,
        AssetCard.type == "voice",
        AssetCard.status == "approved",
    )
    for card in (await session.execute(stmt)).scalars().all():
        if (card.provider_meta or {}).get("speaker_asset_id") == speaker_asset_id:
            return card
    return None


async def plan_shot_audio_jobs(
    session: AsyncSession, *, run: WorkflowRun, step: WorkflowStep, episode_id: str
) -> list[GenerationJob]:
    """按当前 revision shots 的 dialogue_tracks/narration_tracks 展开 tts Job（每条有 text 的 track 一个）。

    TTS 单价未入价格表，用 cost_service 的占位单价做预算预留（复审 0.6：
    TTS 也计费，统一受费用 Gate + reserve_budget 控制；pricing 接管后改走价格表）。
    幂等键含 run/step/shot/track_type/track_index/attempt。
    """
    shots = await _current_revision_shots(session, episode_id)
    if not await _cost_quote_still_valid(session, run):
        step.status = "failed"
        step.error_code = "cost_quote_invalid"
        step.error = "费用报价已失效（分镜/参数变化或过期），请重新确认费用"
        await session.flush()
        return []
    from app.services import cost_service  # 延迟导入避免环

    tts_seconds = 0
    for shot in shots:
        for tracks in (shot.dialogue_tracks or [], shot.narration_tracks or []):
            for track in tracks or []:
                text = (track or {}).get("text") or ""
                if text.strip():
                    tts_seconds += cost_service._estimate_tts_seconds(text)
    estimated = cost_service.TTS_PRICE_PER_SECOND * tts_seconds
    if not await _reserve_budget_or_fail(session, run, step, estimated):
        await session.flush()
        return []

    jobs: list[GenerationJob] = []
    for shot in shots:
        for track_type, tracks in (
            ("dialogue", shot.dialogue_tracks or []),
            ("narration", shot.narration_tracks or []),
        ):
            for index, track in enumerate(tracks):
                text = (track or {}).get("text") or ""
                if not text.strip():
                    continue
                voice_asset = await _find_voice_asset(session, run.project_id, track.get("speaker_asset_id"))
                # voice 资产 provider_meta.voice_id（clone_voice 回写的音色标识）传入
                # tts job；没有则用 provider 默认音色（见 QwenTTSProvider 注释）
                voice_id = (voice_asset.provider_meta or {}).get("voice_id") if voice_asset else None
                voice_model = (voice_asset.provider_meta or {}).get("voice_model") if voice_asset else None
                key = make_job_idempotency_key(
                    "tts",
                    run.id,
                    step.node_key,
                    f"{shot.id}:{track_type}{index}",
                    step.attempt,
                )
                job, _ = await _get_or_create_job(
                    session,
                    idempotency_key=key,
                    project_id=run.project_id,
                    shot_id=shot.id,
                    workflow_run_id=run.id,
                    workflow_step_id=step.id,
                    operation="tts",
                    step_attempt=step.attempt,
                    logical_job_key=make_logical_job_key(
                        "tts", run.id, step.node_key, f"{shot.id}:{track_type}{index}"
                    ),
                    params={
                        "shot_id": shot.id,
                        "step_id": step.id,
                        "run_id": run.id,
                        "track_type": track_type,
                        "track_index": index,
                        "text": text,
                        "lang": track.get("lang", "zh"),
                        "voice_asset_id": voice_asset.id if voice_asset else None,
                        "voice_id": voice_id,
                        "voice_model": voice_model,
                    },
                )
                jobs.append(job)
    await session.flush()
    return jobs


async def plan_episode_render_job(
    session: AsyncSession, *, run: WorkflowRun, step: WorkflowStep, episode_id: str
) -> GenerationJob:
    """整集渲染单 Job（render_episode，本地合成不计费）。

    render_mode（P1-11）：默认 final——用户显式合成就是要成片，任何镜头缺视频
    产物即失败；preview 由 API/工具经 step.input_snapshot.render_mode 显式传入。
    """
    from app.services import timeline_service

    render_mode = (step.input_snapshot or {}).get("render_mode", "final")
    timeline = await timeline_service.get_render_revision(session, episode_id)
    if timeline is None:
        raise ValueError("episode render requires an approved TimelineRevision")
    key = make_job_idempotency_key("render", run.id, step.node_key, episode_id, step.attempt)
    job, _ = await _get_or_create_job(
        session,
        idempotency_key=key,
        project_id=run.project_id,
        workflow_run_id=run.id,
        workflow_step_id=step.id,
        operation="render_episode",
        step_attempt=step.attempt,
        logical_job_key=make_logical_job_key("render_episode", run.id, step.node_key, episode_id),
        params={
            "episode_id": episode_id,
            "step_id": step.id,
            "run_id": run.id,
            "resolution": _run_policy(run).get("resolution", "768P"),
            "render_mode": render_mode,
            "timeline_revision_id": timeline.id,
            "timeline_content_hash": timeline.content_hash,
            "export_profile": (step.input_snapshot or {}).get("export_profile", render_mode),
        },
    )
    await session.flush()
    return job


async def retry_failed_shot_jobs(session: AsyncSession, episode_id: str, run_id: str) -> int:
    """只重置该 episode 下 failed 的 shot Job 为 pending，返回重置数量。

    语义选择（简单版）：attempt 不归零、idempotency_key 不变 —— attempt 是
    worker 的累计执行次数，重试只是重新排队；max_attempts 仍由 worker 把关。
    """
    episode = await session.get(Episode, episode_id)
    if episode is None:
        raise ValueError(f"episode {episode_id} not found")
    stmt = (
        select(GenerationJob)
        .join(Shot, GenerationJob.shot_id == Shot.id)
        .join(Scene, Shot.scene_id == Scene.id)
        .where(
            Scene.episode_id == episode_id,
            GenerationJob.status == "failed",
            GenerationJob.params["run_id"].as_string() == run_id,
        )
    )
    jobs = (await session.execute(stmt)).scalars().all()
    for job in jobs:
        job.status = "pending"
        job.error = ""
        job.error_code = ""
        job.progress = 0
    await session.flush()
    return len(jobs)
