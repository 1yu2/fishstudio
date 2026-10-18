"""Add business context and token columns to usage events.

Usage events become the per-node cost trace: each row records which episode,
shot, workflow step, purpose, tier and deployment it belongs to, plus token and
GPU-second usage. No foreign keys on the new id columns so cost history
survives when shots or runs are deleted.

Revision ID: 0024
Revises: 0023
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STRING_COLUMNS = (
    ("episode_id", 64, None),
    ("shot_id", 64, None),
    ("workflow_run_id", 64, None),
    ("workflow_step_id", 64, None),
    ("purpose", 64, ""),
    ("tier", 16, ""),
    ("deployment_name", 128, ""),
)
_INT_COLUMNS = ("input_tokens", "output_tokens", "cached_tokens")
_INDEXED = ("episode_id", "shot_id", "workflow_run_id", "purpose", "deployment_name")


def upgrade() -> None:
    for name, length, default in _STRING_COLUMNS:
        op.add_column(
            "usage_events",
            sa.Column(
                name,
                sa.String(length=length),
                nullable=default is None,
                server_default=default,
            ),
        )
    for name in _INT_COLUMNS:
        op.add_column("usage_events", sa.Column(name, sa.Integer(), nullable=False, server_default="0"))
    op.add_column("usage_events", sa.Column("gpu_seconds", sa.Float(), nullable=False, server_default="0"))
    for name in _INDEXED:
        op.create_index(f"ix_usage_events_{name}", "usage_events", [name])


def downgrade() -> None:
    for name in _INDEXED:
        op.drop_index(f"ix_usage_events_{name}", table_name="usage_events")
    op.drop_column("usage_events", "gpu_seconds")
    for name in reversed(_INT_COLUMNS):
        op.drop_column("usage_events", name)
    for name, _, _ in reversed(_STRING_COLUMNS):
        op.drop_column("usage_events", name)
