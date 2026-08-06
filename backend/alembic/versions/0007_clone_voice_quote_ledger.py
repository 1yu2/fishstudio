"""clone_voice 持久化报价账本

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "clone_voice_quotes",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("asset_version", sa.Integer(), nullable=False),
        sa.Column("reference_artifact_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("price", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="CNY"),
        sa.Column("price_table_version", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_job_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["asset_cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["consumed_job_id"], ["generation_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["series_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reference_artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("consumed_job_id"),
        sa.UniqueConstraint("nonce"),
    )
    op.create_index("ix_clone_voice_quotes_asset_id", "clone_voice_quotes", ["asset_id"])
    op.create_index("ix_clone_voice_quotes_expires_at", "clone_voice_quotes", ["expires_at"])
    op.create_index("ix_clone_voice_quotes_nonce", "clone_voice_quotes", ["nonce"], unique=True)
    op.create_index("ix_clone_voice_quotes_project_id", "clone_voice_quotes", ["project_id"])
    op.create_index("ix_clone_voice_quotes_user_id", "clone_voice_quotes", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_clone_voice_quotes_user_id", table_name="clone_voice_quotes")
    op.drop_index("ix_clone_voice_quotes_project_id", table_name="clone_voice_quotes")
    op.drop_index("ix_clone_voice_quotes_nonce", table_name="clone_voice_quotes")
    op.drop_index("ix_clone_voice_quotes_expires_at", table_name="clone_voice_quotes")
    op.drop_index("ix_clone_voice_quotes_asset_id", table_name="clone_voice_quotes")
    op.drop_table("clone_voice_quotes")
