"""Add quality tier to purpose model routes.

Routes become keyed by (purpose, tier) so one purpose can bind different
deployments for draft / preview / final quality. Existing routes become
tier=final, which is what every caller resolved before this migration.

Revision ID: 0023
Revises: 0022
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "model_routes",
        sa.Column("tier", sa.String(length=16), nullable=False, server_default="final"),
    )
    op.drop_constraint("model_routes_pkey", "model_routes", type_="primary")
    op.create_primary_key("model_routes_pkey", "model_routes", ["purpose", "tier"])


def downgrade() -> None:
    # 降级只能保留每个用途的 final 档；draft/preview 绑定会丢失。
    op.execute("DELETE FROM model_routes WHERE tier <> 'final'")
    op.drop_constraint("model_routes_pkey", "model_routes", type_="primary")
    op.create_primary_key("model_routes_pkey", "model_routes", ["purpose"])
    op.drop_column("model_routes", "tier")
