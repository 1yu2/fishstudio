"""series + workflow domain: 18 张表

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-03

对应 app/db/models.py 中的系列剧（series）领域模型（schema 加固版）+
Workflow 领域模型（workflow_templates / skill_manifests / workflow_runs /
workflow_steps / workflow_step_dependencies / artifact_feedback）。

建表顺序按 FK 依赖拓扑排序；downgrade 反序全 drop。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # 1. workflow_templates（仅依赖 users）
    op.create_table(
        "workflow_templates",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("project_type", sa.String(length=32), nullable=False, server_default="short_drama"),
        sa.Column("graph_schema", _jsonb(), nullable=False, server_default="{}"),
        sa.Column("default_policy", _jsonb(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", "version", name="uq_workflow_templates_key_version"),
    )
    op.create_index("ix_workflow_templates_key", "workflow_templates", ["key"])

    # 2. skill_manifests
    op.create_table(
        "skill_manifests",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("version", sa.String(length=32), nullable=False, server_default="0.1.0"),
        sa.Column("input_schema", _jsonb(), nullable=False, server_default="{}"),
        sa.Column("output_schema", _jsonb(), nullable=False, server_default="{}"),
        sa.Column("required_capabilities", _jsonb(), nullable=False, server_default="[]"),
        sa.Column("compatible_engine_version", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="builtin"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", "version", name="uq_skill_manifests_key_version"),
    )

    # 3. series_projects（依赖 canvases / users）
    op.create_table(
        "series_projects",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("canvas_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("project_type", sa.String(length=32), nullable=False, server_default="short_drama"),
        sa.Column("workflow_template_id", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("active_skill_ids", _jsonb(), nullable=False, server_default="[]"),
        sa.Column("settings", _jsonb(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["canvas_id"], ["canvases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canvas_id", name="uq_series_projects_canvas_id"),
    )
    op.create_index("ix_series_projects_user_id", "series_projects", ["user_id"])

    # 4. episodes
    op.create_table(
        "episodes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("episode_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("synopsis", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["series_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "episode_no", name="uq_episodes_project_no"),
    )
    op.create_index("ix_episodes_project_id", "episodes", ["project_id"])

    # 5. scenes
    op.create_table(
        "scenes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("episode_id", sa.String(length=64), nullable=False),
        sa.Column("scene_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("location", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("time_of_day", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("episode_id", "scene_no", name="uq_scenes_episode_no"),
    )
    op.create_index("ix_scenes_episode_id", "scenes", ["episode_id"])

    # 6. storyboard_revisions
    op.create_table(
        "storyboard_revisions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("episode_id", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload", _jsonb(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("episode_id", "revision", name="uq_storyboard_episode_revision"),
    )
    op.create_index("ix_storyboard_revisions_episode_id", "storyboard_revisions", ["episode_id"])

    # 7. shots
    op.create_table(
        "shots",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("scene_id", sa.String(length=64), nullable=False),
        sa.Column("storyboard_revision_id", sa.String(length=64), nullable=True),
        sa.Column("shot_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="5000"),
        sa.Column("visual_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("camera", _jsonb(), nullable=False, server_default="{}"),
        sa.Column("transition", _jsonb(), nullable=False, server_default="{}"),
        sa.Column("aspect_ratio", sa.String(length=16), nullable=False, server_default="9:16"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dialogue_tracks", _jsonb(), nullable=False, server_default="[]"),
        sa.Column("narration_tracks", _jsonb(), nullable=False, server_default="[]"),
        sa.Column("subtitle_tracks", _jsonb(), nullable=False, server_default="[]"),
        sa.ForeignKeyConstraint(["scene_id"], ["scenes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["storyboard_revision_id"], ["storyboard_revisions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scene_id", "storyboard_revision_id", "shot_no", name="uq_shots_scene_rev_no"),
    )
    op.create_index("ix_shots_scene_id", "shots", ["scene_id"])

    # 8. asset_cards
    op.create_table(
        "asset_cards",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("provider_meta", _jsonb(), nullable=False, server_default="{}"),
        sa.Column("scope", sa.String(length=32), nullable=False, server_default="project"),
        sa.Column("tags", _jsonb(), nullable=False, server_default="[]"),
        sa.Column("source", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("license_meta", _jsonb(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["series_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "type", "name", "version", name="uq_asset_cards_project_type_name_version"),
    )
    op.create_index("ix_asset_cards_project_id", "asset_cards", ["project_id"])

    # 9. workflow_runs（依赖 series_projects / workflow_templates / users）
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("template_id", sa.String(length=64), nullable=True),
        sa.Column("template_version", sa.Integer(), nullable=True),
        sa.Column("planner_model", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("trigger_type", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("input_snapshot", _jsonb(), nullable=False, server_default="{}"),
        sa.Column("estimated_cost", sa.Numeric(12, 6), nullable=True),
        sa.Column("cost_limit", sa.Numeric(12, 6), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["series_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["workflow_templates.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_runs_project_id", "workflow_runs", ["project_id"])

    # 10. workflow_steps（selected_artifact_id 不加 FK，避免与 artifacts 成环）
    op.create_table(
        "workflow_steps",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("node_key", sa.String(length=128), nullable=False),
        sa.Column("node_type", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("entity_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("input_snapshot", _jsonb(), nullable=False, server_default="{}"),
        sa.Column("output_snapshot", _jsonb(), nullable=False, server_default="{}"),
        sa.Column("selected_artifact_id", sa.String(length=64), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "node_key", name="uq_workflow_steps_run_node"),
    )
    op.create_index("ix_workflow_steps_run_id", "workflow_steps", ["run_id"])

    # 11. workflow_step_dependencies
    op.create_table(
        "workflow_step_dependencies",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("upstream_step_id", sa.String(length=64), nullable=False),
        sa.Column("downstream_step_id", sa.String(length=64), nullable=False),
        sa.Column("dependency_type", sa.String(length=32), nullable=False, server_default="finish_to_start"),
        sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["upstream_step_id"], ["workflow_steps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["downstream_step_id"], ["workflow_steps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("upstream_step_id", "downstream_step_id", name="uq_step_deps_up_down"),
    )
    op.create_index("ix_workflow_step_dependencies_run_id", "workflow_step_dependencies", ["run_id"])

    # 12. generation_jobs
    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("shot_id", sa.String(length=64), nullable=True),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("params", _jsonb(), nullable=False, server_default="{}"),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("external_task_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("error_code", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", _jsonb(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["series_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shot_id"], ["shots.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_generation_jobs_project_id", "generation_jobs", ["project_id"])
    op.create_index("ix_generation_jobs_shot_id", "generation_jobs", ["shot_id"])
    op.create_index("ix_generation_jobs_idempotency_key", "generation_jobs", ["idempotency_key"], unique=True)
    op.create_index("ix_generation_jobs_status", "generation_jobs", ["status"])

    # 13. artifacts（依赖 series_projects / generation_jobs）
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=True),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("mime_type", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("artifact_metadata", _jsonb(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["series_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["generation_jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifacts_project_id", "artifacts", ["project_id"])
    op.create_index("ix_artifacts_job_id", "artifacts", ["job_id"])

    # 14. asset_artifact_refs
    op.create_table(
        "asset_artifact_refs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("artifact_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="reference"),
        sa.ForeignKeyConstraint(["asset_id"], ["asset_cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_asset_artifact_refs_asset_id", "asset_artifact_refs", ["asset_id"])

    # 15. shot_asset_refs
    op.create_table(
        "shot_asset_refs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("shot_id", sa.String(length=64), nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="character"),
        sa.ForeignKeyConstraint(["shot_id"], ["shots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["asset_cards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shot_asset_refs_shot_id", "shot_asset_refs", ["shot_id"])

    # 16. approvals
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("target_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="approved"),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["series_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_approvals_project_id", "approvals", ["project_id"])

    # 17. usage_events
    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("run_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.String(length=64), nullable=True),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("input_units", sa.Float(), nullable=False, server_default="0"),
        sa.Column("output_units", sa.Float(), nullable=False, server_default="0"),
        sa.Column("unit_type", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("unit_price_snapshot", sa.Numeric(12, 6), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(12, 6), nullable=True),
        sa.Column("actual_cost", sa.Numeric(12, 6), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="CNY"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="success"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("provider_request_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("error_code", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("event_metadata", _jsonb(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["generation_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["series_projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_events_job_id", "usage_events", ["job_id"])
    op.create_index("ix_usage_events_user_id", "usage_events", ["user_id"])
    op.create_index("ix_usage_events_project_id", "usage_events", ["project_id"])

    # 18. artifact_feedback
    op.create_table(
        "artifact_feedback",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("step_id", sa.String(length=64), nullable=True),
        sa.Column("artifact_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["series_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["step_id"], ["workflow_steps.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifact_feedback_project_id", "artifact_feedback", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_artifact_feedback_project_id", table_name="artifact_feedback")
    op.drop_table("artifact_feedback")
    op.drop_index("ix_usage_events_project_id", table_name="usage_events")
    op.drop_index("ix_usage_events_user_id", table_name="usage_events")
    op.drop_index("ix_usage_events_job_id", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_index("ix_approvals_project_id", table_name="approvals")
    op.drop_table("approvals")
    op.drop_index("ix_shot_asset_refs_shot_id", table_name="shot_asset_refs")
    op.drop_table("shot_asset_refs")
    op.drop_index("ix_asset_artifact_refs_asset_id", table_name="asset_artifact_refs")
    op.drop_table("asset_artifact_refs")
    op.drop_index("ix_artifacts_job_id", table_name="artifacts")
    op.drop_index("ix_artifacts_project_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_generation_jobs_status", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_idempotency_key", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_shot_id", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_project_id", table_name="generation_jobs")
    op.drop_table("generation_jobs")
    op.drop_index("ix_workflow_step_dependencies_run_id", table_name="workflow_step_dependencies")
    op.drop_table("workflow_step_dependencies")
    op.drop_index("ix_workflow_steps_run_id", table_name="workflow_steps")
    op.drop_table("workflow_steps")
    op.drop_index("ix_workflow_runs_project_id", table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_index("ix_asset_cards_project_id", table_name="asset_cards")
    op.drop_table("asset_cards")
    op.drop_index("ix_shots_scene_id", table_name="shots")
    op.drop_table("shots")
    op.drop_index("ix_storyboard_revisions_episode_id", table_name="storyboard_revisions")
    op.drop_table("storyboard_revisions")
    op.drop_index("ix_scenes_episode_id", table_name="scenes")
    op.drop_table("scenes")
    op.drop_index("ix_episodes_project_id", table_name="episodes")
    op.drop_table("episodes")
    op.drop_index("ix_series_projects_user_id", table_name="series_projects")
    op.drop_table("series_projects")
    op.drop_table("skill_manifests")
    op.drop_index("ix_workflow_templates_key", table_name="workflow_templates")
    op.drop_table("workflow_templates")
