"""Add canvas batch runs (server-side ordered execution of a node canvas).

Revision ID: 0027
Revises: 0026
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "canvas_batch_runs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "canvas_id", sa.String(length=64), sa.ForeignKey("node_canvases.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_request_id", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False, server_default="all"),
        sa.Column("root_node_id", sa.String(length=64), nullable=True),
        sa.Column("skip_succeeded", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("node_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("nodes", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("estimated_total", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("graph", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("aspect_ratio", sa.String(length=16), nullable=False, server_default="9:16"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("canvas_id", "client_request_id", name="uq_canvas_batch_client_request"),
    )
    op.create_index("ix_canvas_batch_runs_canvas_id", "canvas_batch_runs", ["canvas_id"])
    op.create_index("ix_canvas_batch_runs_owner_id", "canvas_batch_runs", ["owner_id"])
    op.create_index("ix_canvas_batch_runs_status", "canvas_batch_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_canvas_batch_runs_status", table_name="canvas_batch_runs")
    op.drop_index("ix_canvas_batch_runs_owner_id", table_name="canvas_batch_runs")
    op.drop_index("ix_canvas_batch_runs_canvas_id", table_name="canvas_batch_runs")
    op.drop_table("canvas_batch_runs")
