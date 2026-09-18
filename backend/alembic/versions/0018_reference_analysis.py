"""reference analysis and selective shot references

Revision ID: 0018
Revises: 0017
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reference_analyses",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_id", sa.String(64), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", sa.String(64), sa.ForeignKey("reference_analyses.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="ready"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("segments", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("model_invocation_id", sa.String(64), sa.ForeignKey("model_invocations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("model_snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("usage_snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("estimated_cost", sa.Numeric(12, 6), nullable=True),
        sa.Column("source_uri", sa.Text(), nullable=False, server_default=""),
        sa.Column("authorization", sa.String(32), nullable=False, server_default="owned"),
        sa.Column("usage_scope", sa.String(64), nullable=False, server_default="director_language_only"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("artifact_id", "revision", name="uq_reference_analysis_artifact_revision"),
    )
    op.create_table(
        "shot_references",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shot_id", sa.String(64), sa.ForeignKey("shots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("analysis_id", sa.String(64), sa.ForeignKey("reference_analyses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("analysis_revision", sa.Integer(), nullable=False),
        sa.Column("segment_key", sa.String(64), nullable=False),
        sa.Column("reference_type", sa.String(32), nullable=False),
        sa.Column("segment_snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1"),
        sa.Column("replace_subject", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("shot_id", "analysis_id", "segment_key", "reference_type", name="uq_shot_reference_slot"),
    )
    for table, columns in {"reference_analyses": ("project_id", "artifact_id", "status"), "shot_references": ("project_id", "shot_id", "analysis_id")}.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    op.drop_table("shot_references")
    op.drop_table("reference_analyses")
