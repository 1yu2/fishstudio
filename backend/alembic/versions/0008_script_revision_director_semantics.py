"""剧本版本链与导演镜头语义

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for name, default in (
        ("objective", ""),
        ("hook", ""),
        ("conflict", ""),
        ("payoff", ""),
        ("cliffhanger", ""),
    ):
        op.add_column(
            "episodes",
            sa.Column(name, sa.Text(), nullable=False, server_default=default),
        )
    op.add_column(
        "episodes",
        sa.Column("target_duration_ms", sa.Integer(), nullable=False, server_default="90000"),
    )

    op.create_table(
        "script_revisions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("episode_id", sa.String(length=64), nullable=False),
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
        sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("episode_id", "revision", name="uq_script_episode_revision"),
    )
    op.create_index("ix_script_revisions_episode_id", "script_revisions", ["episode_id"])

    op.add_column(
        "storyboard_revisions",
        sa.Column("based_on_script_revision_id", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_storyboard_revisions_script_revision",
        "storyboard_revisions",
        "script_revisions",
        ["based_on_script_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_storyboard_revisions_based_on_script_revision_id",
        "storyboard_revisions",
        ["based_on_script_revision_id"],
    )

    text_columns = (
        "narrative_purpose",
        "composition",
        "action_start",
        "action_end",
        "emotion",
        "first_frame_prompt",
        "last_frame_prompt",
    )
    op.add_column(
        "shots", sa.Column("shot_type", sa.String(length=32), nullable=False, server_default="")
    )
    for name in text_columns:
        op.add_column("shots", sa.Column(name, sa.Text(), nullable=False, server_default=""))
    op.add_column(
        "shots",
        sa.Column("generation_mode", sa.String(length=32), nullable=False, server_default="text"),
    )
    op.add_column(
        "shots",
        sa.Column(
            "continuity_constraints",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "shots",
        sa.Column(
            "risk_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "shots",
        sa.Column(
            "locked_asset_versions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "shots", sa.Column("qa_status", sa.String(length=32), nullable=False, server_default="pending")
    )


def downgrade() -> None:
    for name in (
        "qa_status",
        "locked_asset_versions",
        "risk_tags",
        "continuity_constraints",
        "generation_mode",
        "last_frame_prompt",
        "first_frame_prompt",
        "emotion",
        "action_end",
        "action_start",
        "composition",
        "narrative_purpose",
        "shot_type",
    ):
        op.drop_column("shots", name)

    op.drop_index(
        "ix_storyboard_revisions_based_on_script_revision_id",
        table_name="storyboard_revisions",
    )
    op.drop_constraint(
        "fk_storyboard_revisions_script_revision",
        "storyboard_revisions",
        type_="foreignkey",
    )
    op.drop_column("storyboard_revisions", "based_on_script_revision_id")
    op.drop_index("ix_script_revisions_episode_id", table_name="script_revisions")
    op.drop_table("script_revisions")

    for name in (
        "target_duration_ms",
        "cliffhanger",
        "payoff",
        "conflict",
        "hook",
        "objective",
    ):
        op.drop_column("episodes", name)
