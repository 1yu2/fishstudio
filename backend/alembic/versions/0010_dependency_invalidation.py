"""DependencyInvalidationService 审计事件

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "invalidation_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=True),
        sa.Column(
            "changed_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("reason", sa.Text(), nullable=False, server_default="content_changed"),
        sa.Column(
            "affected_entities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["series_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_invalidation_events_project_id", "invalidation_events", ["project_id"])
    op.create_index("ix_invalidation_events_source_id", "invalidation_events", ["source_id"])
    op.create_index(
        "ix_invalidation_events_idempotency_key",
        "invalidation_events",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_invalidation_events_idempotency_key", table_name="invalidation_events")
    op.drop_index("ix_invalidation_events_source_id", table_name="invalidation_events")
    op.drop_index("ix_invalidation_events_project_id", table_name="invalidation_events")
    op.drop_table("invalidation_events")
