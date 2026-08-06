"""ORM 模型：User / Canvas / Setting / AuditLog，以及系列剧（series）领域模型。"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Canvas(Base):
    __tablename__ = "canvases"

    # id 沿用前端给的字符串 id，避免破坏现有数据
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at_ts: Mapped[float] = mapped_column(nullable=False, default=0.0)
    images: Mapped[Optional[list[Any]]] = mapped_column(JSONB, nullable=True)
    data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    messages: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    owner_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Setting(Base):
    __tablename__ = "settings_kv"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    before: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    after: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


def _uuid_str() -> str:
    return str(uuid.uuid4())


class SeriesProject(Base):
    __tablename__ = "series_projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    # MVP 一个 Canvas 最多绑一个 Series Project；Canvas 删除后置空
    canvas_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("canvases.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_type: Mapped[str] = mapped_column(String(32), nullable=False, default="short_drama")
    workflow_template_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    active_skill_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    # 默认画幅 / 目标平台 / provider 锁定等
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("project_id", "episode_no", name="uq_episodes_project_no"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    episode_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    synopsis: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Scene(Base):
    __tablename__ = "scenes"
    __table_args__ = (UniqueConstraint("episode_id", "scene_no", name="uq_scenes_episode_no"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    episode_id: Mapped[str] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scene_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    location: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    time_of_day: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class StoryboardRevision(Base):
    __tablename__ = "storyboard_revisions"
    __table_args__ = (UniqueConstraint("episode_id", "revision", name="uq_storyboard_episode_revision"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    episode_id: Mapped[str] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Shot(Base):
    __tablename__ = "shots"
    # storyboard_revision_id 可空；NULL 在 PG 中不参与唯一冲突，符合"未绑修订的 shot 不互斥"语义
    __table_args__ = (
        UniqueConstraint("scene_id", "storyboard_revision_id", "shot_no", name="uq_shots_scene_rev_no"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    scene_id: Mapped[str] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    storyboard_revision_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("storyboard_revisions.id", ondelete="SET NULL"), nullable=True
    )
    shot_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=5000)
    visual_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    camera: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    transition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    aspect_ratio: Mapped[str] = mapped_column(String(16), nullable=False, default="9:16")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dialogue_tracks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    narration_tracks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    subtitle_tracks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    # 复审 0.8：拆分图片/视频选择槽，避免候选选择互相覆盖
    selected_image_artifact_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    selected_video_artifact_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )


class AssetCard(Base):
    __tablename__ = "asset_cards"
    __table_args__ = (
        UniqueConstraint("project_id", "type", "name", "version", name="uq_asset_cards_project_type_name_version"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    # scope=personal 的个人资产可为空（P2-3：个人资产库独立于项目）
    project_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # owner_user_id 语义：scope=personal 时归属用户；项目资产也可记录创建者
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # character | style | scene | voice
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    provider_meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # personal | project（个人资产库 / 项目资产）
    scope: Mapped[str] = mapped_column(String(32), nullable=False, default="project")
    tags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    source: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # 第三方素材授权信息等
    license_meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    project_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    job_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # image | audio | video | subtitle | model | document
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # metadata 是 SQLAlchemy 保留字，字段名用 artifact_metadata
    artifact_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AssetArtifactRef(Base):
    __tablename__ = "asset_artifact_refs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("asset_cards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="reference")


class ShotAssetRef(Base):
    __tablename__ = "shot_asset_refs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    shot_id: Mapped[str] = mapped_column(
        ForeignKey("shots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("asset_cards.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="character")


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # storyboard | asset
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # approved | rejected | superseded
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="approved")
    approved_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GenerationJob(Base):
    __tablename__ = "generation_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shot_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("shots.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 归属的 workflow run/step（P1-10：不再只靠 params JSON，pause/lease 可直接 JOIN）
    workflow_run_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    workflow_step_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("workflow_steps.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # generate_image | generate_video | tts | render_episode | clone_voice
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # 格式 op:run_id:node_key(:ep_<episode_id>):shot_id:attempt<N>，最长可超 128，预留 255
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    external_task_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # pending | leased | running | succeeded | failed | canceled
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    # 重试代际追踪（聚合逻辑 Wave B 接管，先落字段）：
    # step_attempt 创建时从 step.attempt 拷贝；logical_job_key 标记同一逻辑任务的多代重试；
    # superseded_by 在 job 级 retry 时回填新 job.id
    step_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    logical_job_key: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", index=True
    )
    superseded_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    job_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("series_projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    input_units: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    output_units: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    unit_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    unit_price_snapshot: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)
    estimated_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)
    actual_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)
    # 复审 0.13：cost_source 区分费用来源（estimate | provider_response | reconciled_bill）
    cost_source: Mapped[str] = mapped_column(String(32), nullable=False, default="estimate")
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="CNY")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="success")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    provider_request_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # metadata 是 SQLAlchemy 保留字，字段名用 event_metadata
    event_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CloneVoiceQuoteRecord(Base):
    __tablename__ = "clone_voice_quotes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    nonce: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("asset_cards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="CNY")
    price_table_version: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_job_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ─────────────────────────────────────────
# Workflow 领域（文档 5.2）
# ─────────────────────────────────────────


class WorkflowTemplate(Base):
    __tablename__ = "workflow_templates"
    __table_args__ = (UniqueConstraint("key", "version", name="uq_workflow_templates_key_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    project_type: Mapped[str] = mapped_column(String(32), nullable=False, default="short_drama")
    # 节点 DAG 描述（nodes/edges/params 槽位）
    graph_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    default_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SkillManifest(Base):
    __tablename__ = "skill_manifests"
    __table_args__ = (UniqueConstraint("key", "version", name="uq_skill_manifests_key_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="0.1.0")
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    required_capabilities: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    compatible_engine_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # builtin | user | registry
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="builtin")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    template_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("workflow_templates.id", ondelete="SET NULL"), nullable=True
    )
    template_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    planner_model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    # draft | ready | waiting_approval | queued | running | succeeded | failed | canceled | paused
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    # agent | manual | api
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    estimated_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)
    cost_limit: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (UniqueConstraint("run_id", "node_key", name="uq_workflow_steps_run_node"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_key: Mapped[str] = mapped_column(String(128), nullable=False)
    node_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # 关联实体（episode/scene/shot/asset 等），用字符串避免跨域 FK 环
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # draft | ready | waiting_approval | queued | running | succeeded | failed | canceled | skipped
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # 用户最终选中的 artifact；不加 FK 避免 artifacts 与 steps 之间的依赖环
    selected_artifact_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WorkflowStepDependency(Base):
    __tablename__ = "workflow_step_dependencies"
    __table_args__ = (
        UniqueConstraint("upstream_step_id", "downstream_step_id", name="uq_step_deps_up_down"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    upstream_step_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_steps.id", ondelete="CASCADE"), nullable=False
    )
    downstream_step_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_steps.id", ondelete="CASCADE"), nullable=False
    )
    dependency_type: Mapped[str] = mapped_column(String(32), nullable=False, default="finish_to_start")


class ArtifactFeedback(Base):
    __tablename__ = "artifact_feedback"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("workflow_steps.id", ondelete="SET NULL"), nullable=True
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # selected | rejected | rated
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
