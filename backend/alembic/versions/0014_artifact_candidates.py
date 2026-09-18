"""candidate QA lifecycle and regeneration evidence

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifact_candidates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shot_id", sa.String(64), sa.ForeignKey("shots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_id", sa.String(64), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("candidate_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("qa_summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("qa_findings", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("rejection_reason", sa.String(128), nullable=False, server_default=""),
        sa.Column("evaluated_by", sa.String(128), nullable=False, server_default="deterministic"),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("selected_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("shot_id", "artifact_id", name="uq_artifact_candidates_shot_artifact"),
    )
    op.create_index("ix_artifact_candidates_project_id", "artifact_candidates", ["project_id"])
    op.create_index("ix_artifact_candidates_shot_id", "artifact_candidates", ["shot_id"])
    op.create_index("ix_artifact_candidates_artifact_id", "artifact_candidates", ["artifact_id"])
    op.create_index("ix_artifact_candidates_job_id", "artifact_candidates", ["job_id"])
    op.create_index("ix_artifact_candidates_status", "artifact_candidates", ["status"])
    op.create_table(
        "regeneration_requests",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shot_id", sa.String(64), sa.ForeignKey("shots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_id", sa.String(64), sa.ForeignKey("artifact_candidates.id", ondelete="SET NULL"), nullable=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("generation_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_artifact_id", sa.String(64), sa.ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("result_artifact_id", sa.String(64), sa.ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("instruction", sa.Text(), nullable=False, server_default=""),
        sa.Column("preserve_fields", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("replace_fields", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for column in ("project_id", "shot_id", "candidate_id", "job_id", "status"):
        op.create_index(f"ix_regeneration_requests_{column}", "regeneration_requests", [column])


def downgrade() -> None:
    op.drop_table("regeneration_requests")
    op.drop_table("artifact_candidates")
