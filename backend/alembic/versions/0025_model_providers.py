"""Add model providers (vendor accounts) above deployments.

A provider holds one account: provider type (API protocol), base URL,
credential env reference, models endpoint, capability tags and an enabled switch. Deployments (models) point at their provider; existing deployments
are assigned to providers at startup by the bootstrap service.

Revision ID: 0025
Revises: 0024
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_providers",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("provider_type", sa.String(length=64), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False, server_default=""),
        sa.Column("credential_ref", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("models_endpoint", sa.String(length=255), nullable=False, server_default="/models"),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_model_providers_slug", "model_providers", ["slug"], unique=True)
    op.add_column("model_deployments", sa.Column("provider_id", sa.String(length=64), nullable=True))
    op.create_index("ix_model_deployments_provider_id", "model_deployments", ["provider_id"])
    op.create_foreign_key(
        "fk_model_deployments_provider_id",
        "model_deployments",
        "model_providers",
        ["provider_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_model_deployments_provider_id", "model_deployments", type_="foreignkey")
    op.drop_index("ix_model_deployments_provider_id", table_name="model_deployments")
    op.drop_column("model_deployments", "provider_id")
    op.drop_index("ix_model_providers_slug", table_name="model_providers")
    op.drop_table("model_providers")
