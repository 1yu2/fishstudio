"""Add node canvases (node-based infinite canvas for short videos).

Each canvas owns a series project (project_type="node_canvas") so generation
jobs, artifacts, cost and access checks reuse the existing machinery. The
graph (nodes / edges / viewport) is stored as JSON with a revision for
optimistic concurrency on autosave.

Revision ID: 0026
Revises: 0025
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "node_canvases",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "project_id", sa.String(length=64), sa.ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("aspect_ratio", sa.String(length=16), nullable=False, server_default="9:16"),
        sa.Column("graph", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_node_canvases_owner_id", "node_canvases", ["owner_id"])
    op.create_index("ix_node_canvases_project_id", "node_canvases", ["project_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_node_canvases_project_id", table_name="node_canvases")
    op.drop_index("ix_node_canvases_owner_id", table_name="node_canvases")
    op.drop_table("node_canvases")
