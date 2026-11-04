"""Add canvas script revisions (structured script versions per node canvas).

Revision ID: 0028
Revises: 0027
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "canvas_script_revisions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "canvas_id", sa.String(length=64), sa.ForeignKey("node_canvases.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="user"),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("edit_seq", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_save_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("canvas_id", "revision", name="uq_canvas_script_revision"),
    )
    op.create_index("ix_canvas_script_revisions_canvas_id", "canvas_script_revisions", ["canvas_id"])


def downgrade() -> None:
    op.drop_index("ix_canvas_script_revisions_canvas_id", table_name="canvas_script_revisions")
    op.drop_table("canvas_script_revisions")
