"""Add unified model deployments.

Revision ID: 0021
Revises: 0020
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_deployments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("driver", sa.String(length=64), nullable=False),
        sa.Column("modality", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False, server_default=""),
        sa.Column("credential_ref", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("pricing", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_model_deployments_slug", "model_deployments", ["slug"])
    op.create_index("ix_model_deployments_driver", "model_deployments", ["driver"])
    op.create_index("ix_model_deployments_modality", "model_deployments", ["modality"])
    op.create_index("ix_model_deployments_enabled", "model_deployments", ["enabled"])
    op.create_index("ix_model_deployments_config_hash", "model_deployments", ["config_hash"])


def downgrade() -> None:
    op.drop_index("ix_model_deployments_config_hash", table_name="model_deployments")
    op.drop_index("ix_model_deployments_enabled", table_name="model_deployments")
    op.drop_index("ix_model_deployments_modality", table_name="model_deployments")
    op.drop_index("ix_model_deployments_driver", table_name="model_deployments")
    op.drop_index("ix_model_deployments_slug", table_name="model_deployments")
    op.drop_table("model_deployments")
