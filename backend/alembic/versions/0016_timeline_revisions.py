"""immutable timeline revisions and edit decision lists

Revision ID: 0016
Revises: 0015
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "timeline_revisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("episode_id", sa.String(64), sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", sa.String(64), sa.ForeignKey("timeline_revisions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("storyboard_revision_id", sa.String(64), sa.ForeignKey("storyboard_revisions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("spec", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("episode_id", "revision", name="uq_timeline_revisions_episode_revision"),
    )
    for column in ("project_id", "episode_id", "status", "content_hash"):
        op.create_index(f"ix_timeline_revisions_{column}", "timeline_revisions", [column])


def downgrade() -> None:
    op.drop_table("timeline_revisions")
