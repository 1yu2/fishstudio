"""production job runtime, events, circuits and object storage metadata

Revision ID: 0019
Revises: 0018
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generation_jobs", sa.Column("priority", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("generation_jobs", sa.Column("worker_class", sa.String(32), nullable=False, server_default="media"))
    op.add_column("generation_jobs", sa.Column("leased_by", sa.String(128), nullable=False, server_default=""))
    op.add_column("generation_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("generation_jobs", sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("artifacts", sa.Column("storage_backend", sa.String(32), nullable=False, server_default="local"))
    for column in ("priority", "worker_class"):
        op.create_index(f"ix_generation_jobs_{column}", "generation_jobs", [column])
    op.create_index("ix_artifacts_storage_backend", "artifacts", ["storage_backend"])
    op.create_table(
        "job_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("step_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("generation_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False, server_default=""),
        sa.Column("entity_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default=""),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    for column in ("event", "project_id", "run_id", "step_id", "job_id", "occurred_at"):
        op.create_index(f"ix_job_events_{column}", "job_events", [column])
    op.create_table(
        "provider_circuits",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("deployment", sa.String(128), nullable=False, server_default=""),
        sa.Column("state", sa.String(32), nullable=False, server_default="closed"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("probe_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("provider", "deployment", name="uq_provider_circuit_deployment"),
    )
    for column in ("provider", "state"):
        op.create_index(f"ix_provider_circuits_{column}", "provider_circuits", [column])


def downgrade() -> None:
    op.drop_table("provider_circuits")
    op.drop_table("job_events")
    op.drop_index("ix_artifacts_storage_backend", table_name="artifacts")
    op.drop_column("artifacts", "storage_backend")
    for column in ("worker_class", "priority"):
        op.drop_index(f"ix_generation_jobs_{column}", table_name="generation_jobs")
    for column in ("dead_lettered_at", "heartbeat_at", "leased_by", "worker_class", "priority"):
        op.drop_column("generation_jobs", column)
