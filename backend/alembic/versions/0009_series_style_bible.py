"""Series Bible 与 Style Bible 版本链

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_bible_table(name: str, unique_name: str) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "structured_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="user"),
        sa.Column("based_on_revision", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["series_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "revision", name=unique_name),
    )
    op.create_index(f"ix_{name}_project_id", name, ["project_id"])


def upgrade() -> None:
    _create_bible_table("series_bible_revisions", "uq_series_bible_project_revision")
    _create_bible_table("style_bible_revisions", "uq_style_bible_project_revision")
    for table in ("episodes", "script_revisions"):
        for kind in ("series", "style"):
            prefix = "" if table == "episodes" else "based_on_"
            column = f"{prefix}{kind}_bible_revision_id"
            target = f"{kind}_bible_revisions"
            op.add_column(table, sa.Column(column, sa.String(length=64), nullable=True))
            op.create_foreign_key(
                f"fk_{table}_{column}", table, target, [column], ["id"], ondelete="SET NULL"
            )
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table in ("script_revisions", "episodes"):
        for kind in ("style", "series"):
            prefix = "" if table == "episodes" else "based_on_"
            column = f"{prefix}{kind}_bible_revision_id"
            op.drop_index(f"ix_{table}_{column}", table_name=table)
            op.drop_constraint(f"fk_{table}_{column}", table, type_="foreignkey")
            op.drop_column(table, column)
    for name in ("style_bible_revisions", "series_bible_revisions"):
        op.drop_index(f"ix_{name}_project_id", table_name=name)
        op.drop_table(name)
