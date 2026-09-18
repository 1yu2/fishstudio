"""add model invocation lifecycle ledger

Revision ID: 0011
Revises: 0010
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_invocations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("series_projects.id", ondelete="SET NULL")),
        sa.Column("workflow_run_id", sa.String(64), sa.ForeignKey("workflow_runs.id", ondelete="SET NULL")),
        sa.Column("workflow_step_id", sa.String(64), sa.ForeignKey("workflow_steps.id", ondelete="SET NULL")),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("generation_jobs.id", ondelete="SET NULL")),
        sa.Column("purpose", sa.String(64), nullable=False, server_default=""),
        sa.Column("modality", sa.String(32), nullable=False, server_default="chat"),
        sa.Column("deployment_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("provider", sa.String(64), nullable=False, server_default=""),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("routing_snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(32), nullable=False, server_default="created"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("external_request_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("result_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for column in ("request_id", "project_id", "workflow_run_id", "workflow_step_id", "job_id", "status"):
        op.create_index(f"ix_model_invocations_{column}", "model_invocations", [column])
    op.add_column("usage_events", sa.Column("invocation_id", sa.String(64), nullable=True))
    op.create_foreign_key("fk_usage_events_invocation_id", "usage_events", "model_invocations", ["invocation_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_usage_events_invocation_id", "usage_events", ["invocation_id"])


def downgrade() -> None:
    op.drop_index("ix_usage_events_invocation_id", table_name="usage_events")
    op.drop_constraint("fk_usage_events_invocation_id", "usage_events", type_="foreignkey")
    op.drop_column("usage_events", "invocation_id")
    op.drop_table("model_invocations")
