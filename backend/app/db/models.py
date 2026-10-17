"""ORM 模型：User / Canvas / Setting / AuditLog，以及系列剧（series）领域模型。"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
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


class ModelDeployment(Base):
    """管理员配置的模型服务实例；密钥只保存引用，不保存明文。"""

    __tablename__ = "model_deployments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    driver: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    modality: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    credential_ref: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    pricing: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ModelRoute(Base):
    """业务用途到主 Deployment / 有序备用 Deployment 的最小绑定。"""

    __tablename__ = "model_routes"

    purpose: Mapped[str] = mapped_column(String(64), primary_key=True)
    modality: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    primary_deployment: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    fallback_deployments: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
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


class SeriesBibleRevision(Base):
    """不可变的整季故事设定版本。"""

    __tablename__ = "series_bible_revisions"
    __table_args__ = (
        UniqueConstraint("project_id", "revision", name="uq_series_bible_project_revision"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    structured_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    based_on_revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StyleBibleRevision(Base):
    """不可变的整季视觉风格版本。"""

    __tablename__ = "style_bible_revisions"
    __table_args__ = (
        UniqueConstraint("project_id", "revision", name="uq_style_bible_project_revision"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    structured_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    based_on_revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (UniqueConstraint("project_id", "episode_no", name="uq_episodes_project_no"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    series_bible_revision_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("series_bible_revisions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    style_bible_revision_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("style_bible_revisions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    episode_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    synopsis: Mapped[str] = mapped_column(Text, nullable=False, default="")
    objective: Mapped[str] = mapped_column(Text, nullable=False, default="")
    hook: Mapped[str] = mapped_column(Text, nullable=False, default="")
    conflict: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payoff: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cliffhanger: Mapped[str] = mapped_column(Text, nullable=False, default="")
    target_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=90000)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ScriptRevision(Base):
    """不可变的单集剧本版本；修改剧本必须创建新 revision。"""

    __tablename__ = "script_revisions"
    __table_args__ = (UniqueConstraint("episode_id", "revision", name="uq_script_episode_revision"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    episode_id: Mapped[str] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    based_on_series_bible_revision_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("series_bible_revisions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    based_on_style_bible_revision_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("style_bible_revisions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    structured_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # agent | user | import
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    based_on_revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DirectorPlanRevision(Base):
    """不可变导演计划；每个意图可由 Shot 精确引用。"""

    __tablename__ = "director_plan_revisions"
    __table_args__ = (UniqueConstraint("episode_id", "revision", name="uq_director_plan_episode_revision"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    episode_id: Mapped[str] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    based_on_script_revision_id: Mapped[str] = mapped_column(
        ForeignKey("script_revisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    series_bible_revision_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("series_bible_revisions.id", ondelete="SET NULL"), nullable=True
    )
    style_bible_revision_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("style_bible_revisions.id", ondelete="SET NULL"), nullable=True
    )
    asset_version_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    platform_constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="agent")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
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
    based_on_script_revision_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("script_revisions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ObserverReview(Base):
    """Observer 结构化复审；只提供建议和阻断标记，不产生审批。"""

    __tablename__ = "observer_reviews"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    episode_id: Mapped[str] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    review_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deterministic_findings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    specialist_findings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    llm_findings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    review_scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model_invocation_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("model_invocations.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="observer")
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
    director_plan_revision_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("director_plan_revisions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    director_intent_key: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    shot_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=5000)
    shot_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    narrative_purpose: Mapped[str] = mapped_column(Text, nullable=False, default="")
    composition: Mapped[str] = mapped_column(Text, nullable=False, default="")
    action_start: Mapped[str] = mapped_column(Text, nullable=False, default="")
    action_end: Mapped[str] = mapped_column(Text, nullable=False, default="")
    emotion: Mapped[str] = mapped_column(Text, nullable=False, default="")
    first_frame_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_frame_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    generation_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    continuity_constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    risk_tags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    locked_asset_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    qa_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
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
    profile_schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
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
    storage_backend: Mapped[str] = mapped_column(String(32), nullable=False, default="local", index=True)
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


class ReferenceAnalysis(Base):
    """参考视频的不可变拉片版本；人工修订创建新 revision。"""

    __tablename__ = "reference_analyses"
    __table_args__ = (
        UniqueConstraint("artifact_id", "revision", name="uq_reference_analysis_artifact_revision"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_revision_id: Mapped[Optional[str]] = mapped_column(ForeignKey("reference_analyses.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready", index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    segments: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    model_invocation_id: Mapped[Optional[str]] = mapped_column(ForeignKey("model_invocations.id", ondelete="SET NULL"), nullable=True)
    model_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    usage_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    estimated_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False, default="")
    authorization: Mapped[str] = mapped_column(String(32), nullable=False, default="owned")
    usage_scope: Mapped[str] = mapped_column(String(64), nullable=False, default="director_language_only")
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ShotReference(Base):
    """Shot 对拉片片段的选择性引用，不携带参考片主体身份。"""

    __tablename__ = "shot_references"
    __table_args__ = (
        UniqueConstraint("shot_id", "analysis_id", "segment_key", "reference_type", name="uq_shot_reference_slot"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    shot_id: Mapped[str] = mapped_column(ForeignKey("shots.id", ondelete="CASCADE"), nullable=False, index=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("reference_analyses.id", ondelete="CASCADE"), nullable=False, index=True)
    analysis_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    segment_key: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(32), nullable=False)
    segment_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1)
    replace_subject: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ArtifactCandidate(Base):
    """Shot 级候选生命周期与可审计 QA 证据。Artifact 本身保持不可变。"""

    __tablename__ = "artifact_candidates"
    __table_args__ = (
        UniqueConstraint("shot_id", "artifact_id", name="uq_artifact_candidates_shot_artifact"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shot_id: Mapped[str] = mapped_column(
        ForeignKey("shots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    candidate_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # pending | evaluating | approved | rejected | superseded
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    qa_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    qa_findings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    rejection_reason: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    evaluated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="deterministic")
    evaluated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    selected_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    selected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RegenerationRequest(Base):
    """局部重拍请求；约束和源/结果产物独立于 Job 保留。"""

    __tablename__ = "regeneration_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shot_id: Mapped[str] = mapped_column(
        ForeignKey("shots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("artifact_candidates.id", ondelete="SET NULL"), nullable=True, index=True
    )
    job_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_artifact_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    result_artifact_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    instruction: Mapped[str] = mapped_column(Text, nullable=False, default="")
    preserve_fields: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    replace_fields: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    quote_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", unique=True)
    quoted_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)
    actual_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 6), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="CNY")
    price_table_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    quote_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TimelineRevision(Base):
    """不可变剪辑决策列表；编辑通过创建下一 revision 完成。"""

    __tablename__ = "timeline_revisions"
    __table_args__ = (
        UniqueConstraint("episode_id", "revision", name="uq_timeline_revisions_episode_revision"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    episode_id: Mapped[str] = mapped_column(
        ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_revision_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("timeline_revisions.id", ondelete="SET NULL"), nullable=True
    )
    storyboard_revision_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("storyboard_revisions.id", ondelete="SET NULL"), nullable=True
    )
    # draft | approved | rejected | stale
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class QCReport(Base):
    __tablename__ = "qc_reports"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True)
    timeline_revision_id: Mapped[str] = mapped_column(ForeignKey("timeline_revisions.id", ondelete="CASCADE"), nullable=False)
    render_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False, index=True)
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="final_qc")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    technical_checks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    semantic_checks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    issues: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    repair_targets: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Publication(Base):
    __tablename__ = "publications"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True)
    timeline_revision_id: Mapped[str] = mapped_column(ForeignKey("timeline_revisions.id", ondelete="CASCADE"), nullable=False)
    render_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False)
    cover_artifact_id: Mapped[Optional[str]] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True)
    qc_report_id: Mapped[str] = mapped_column(ForeignKey("qc_reports.id", ondelete="RESTRICT"), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="xiaohongshu")
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    specifications: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    external_post_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class PublishPackage(Base):
    __tablename__ = "publish_packages"
    __table_args__ = (UniqueConstraint("publication_id", "version", name="uq_publish_packages_publication_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    publication_id: Mapped[str] = mapped_column(ForeignKey("publications.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    manifest_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False)
    package_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


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
    asset_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reference_type: Mapped[str] = mapped_column(String(32), nullable=False, default="character")
    artifact_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    subject: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usage: Mapped[str] = mapped_column(String(64), nullable=False, default="identity")


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


class InvalidationEvent(Base):
    """内容变更触发的可审计、幂等失效传播记录。"""

    __tablename__ = "invalidation_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    changed_fields: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="content_changed")
    affected_entities: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ModelInvocation(Base):
    __tablename__ = "model_invocations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("series_projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    workflow_run_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    workflow_step_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("workflow_steps.id", ondelete="SET NULL"), nullable=True, index=True
    )
    job_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    purpose: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    modality: Mapped[str] = mapped_column(String(32), nullable=False, default="chat")
    deployment_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    routing_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created", index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    external_request_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    result_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
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
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    worker_class: Mapped[str] = mapped_column(String(32), nullable=False, default="media", index=True)
    leased_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_lettered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
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


class JobEvent(Base):
    """跨进程任务事件；数据库是真相源，Redis 只负责唤醒与广播。"""

    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    step_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("generation_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class ProviderCircuit(Base):
    __tablename__ = "provider_circuits"
    __table_args__ = (UniqueConstraint("provider", "deployment", name="uq_provider_circuit_deployment"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    deployment: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="closed", index=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    probe_after: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class BenchmarkDataset(Base):
    __tablename__ = "benchmark_datasets"
    __table_args__ = (UniqueConstraint("key", "version", name="uq_benchmark_dataset_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    shot_count: Mapped[int] = mapped_column(Integer, nullable=False)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    expected_constraints: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("benchmark_datasets.id", ondelete="RESTRICT"), nullable=False, index=True)
    project_id: Mapped[Optional[str]] = mapped_column(ForeignKey("series_projects.id", ondelete="SET NULL"), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    deployment: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed", index=True)
    input_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    failure_reasons: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PriceTableEntry(Base):
    __tablename__ = "price_table_entries"
    __table_args__ = (
        UniqueConstraint("version", "provider", "deployment", "model", "unit_type", name="uq_price_table_dimension"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    deployment: Mapped[str] = mapped_column(String(128), nullable=False, default="", index=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    unit_type: Mapped[str] = mapped_column(String(32), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="CNY")
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BudgetAlert(Base):
    __tablename__ = "budget_alerts"
    __table_args__ = (UniqueConstraint("run_id", "threshold", name="uq_budget_alert_threshold"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    utilization: Mapped[float] = mapped_column(Float, nullable=False)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    cost_limit: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PublicationMetric(Base):
    __tablename__ = "publication_metrics"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    publication_id: Mapped[str] = mapped_column(ForeignKey("publications.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    episode_id: Mapped[str] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False, index=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False, index=True)
    content_revisions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    model_invocation_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    recorded_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    job_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    invocation_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("model_invocations.id", ondelete="SET NULL"), nullable=True, index=True
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
