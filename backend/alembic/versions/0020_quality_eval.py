"""quality, cost and operations eval

Revision ID: 0020
Revises: 0019
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "benchmark_datasets",
        sa.Column("id", sa.String(64), primary_key=True), sa.Column("key", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("name", sa.String(255), nullable=False, server_default=""),
        sa.Column("shot_count", sa.Integer(), nullable=False), sa.Column("input_payload", postgresql.JSONB(), nullable=False),
        sa.Column("expected_constraints", postgresql.JSONB(), nullable=False), sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("key", "version", name="uq_benchmark_dataset_version"),
    )
    op.create_table(
        "eval_runs", sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("dataset_id", sa.String(64), sa.ForeignKey("benchmark_datasets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("series_projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("provider", sa.String(64), nullable=False), sa.Column("deployment", sa.String(128), nullable=False),
        sa.Column("model", sa.String(128), nullable=False), sa.Column("status", sa.String(32), nullable=False, server_default="completed"),
        sa.Column("input_hash", sa.String(128), nullable=False), sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("failure_reasons", postgresql.JSONB(), nullable=False), sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "price_table_entries", sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("version", sa.String(64), nullable=False), sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("deployment", sa.String(128), nullable=False, server_default=""), sa.Column("model", sa.String(128), nullable=False),
        sa.Column("unit_type", sa.String(32), nullable=False), sa.Column("unit_price", sa.Numeric(12, 6), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False), sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("version", "provider", "deployment", "model", "unit_type", name="uq_price_table_dimension"),
    )
    op.create_table(
        "budget_alerts", sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False), sa.Column("utilization", sa.Float(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(12, 6), nullable=False), sa.Column("cost_limit", sa.Numeric(12, 6), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("run_id", "threshold", name="uq_budget_alert_threshold"),
    )
    op.create_table(
        "publication_metrics", sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("publication_id", sa.String(64), sa.ForeignKey("publications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("episode_id", sa.String(64), sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_id", sa.String(64), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_revisions", postgresql.JSONB(), nullable=False),
        sa.Column("model_invocation_ids", postgresql.JSONB(), nullable=False),
        sa.Column("platform", sa.String(64), nullable=False), sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("recorded_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for table, columns in {
        "benchmark_datasets": ("key", "status"), "eval_runs": ("dataset_id", "project_id", "provider", "deployment", "model", "status"),
        "price_table_entries": ("version", "provider", "deployment", "model"), "budget_alerts": ("run_id", "project_id"),
        "publication_metrics": ("publication_id", "project_id", "episode_id", "artifact_id"),
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in ("publication_metrics", "budget_alerts", "price_table_entries", "eval_runs", "benchmark_datasets"):
        op.drop_table(table)
