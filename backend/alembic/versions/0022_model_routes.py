"""Add simple purpose model routes.

Revision ID: 0022
Revises: 0021
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_routes",
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("modality", sa.String(length=32), nullable=False),
        sa.Column("primary_deployment", sa.String(length=128), nullable=False),
        sa.Column("fallback_deployments", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("purpose"),
    )
    op.create_index("ix_model_routes_modality", "model_routes", ["modality"])
    op.create_index("ix_model_routes_primary_deployment", "model_routes", ["primary_deployment"])


def downgrade() -> None:
    op.drop_index("ix_model_routes_primary_deployment", table_name="model_routes")
    op.drop_index("ix_model_routes_modality", table_name="model_routes")
    op.drop_table("model_routes")
