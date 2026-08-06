"""generation_jobs.idempotency_key 128 -> 255

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-03

幂等键格式 ``op:run_id:node_key(:ep_<episode_id>):shot_id:attempt<N>``，
episode 作用域节点的键约 160 字符，超出原 VARCHAR(128) 导致
StringDataRightTruncationError（E2E 实测 500）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "generation_jobs",
        "idempotency_key",
        existing_type=sa.String(length=128),
        type_=sa.String(length=255),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "generation_jobs",
        "idempotency_key",
        existing_type=sa.String(length=255),
        type_=sa.String(length=128),
        existing_nullable=False,
    )
