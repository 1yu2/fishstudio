"""director plan revisions and observer reviews

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "director_plan_revisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("episode_id", sa.String(64), sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("based_on_script_revision_id", sa.String(64), sa.ForeignKey("script_revisions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("series_bible_revision_id", sa.String(64), sa.ForeignKey("series_bible_revisions.id", ondelete="SET NULL")),
        sa.Column("style_bible_revision_id", sa.String(64), sa.ForeignKey("style_bible_revisions.id", ondelete="SET NULL")),
        sa.Column("asset_version_snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("platform_constraints", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="agent"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("episode_id", "revision", name="uq_director_plan_episode_revision"),
    )
    op.create_index("ix_director_plan_revisions_episode_id", "director_plan_revisions", ["episode_id"])
    op.create_index("ix_director_plan_revisions_based_on_script_revision_id", "director_plan_revisions", ["based_on_script_revision_id"])
    op.create_table(
        "observer_reviews",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("episode_id", sa.String(64), sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("review_type", sa.String(32), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("target_revision", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("blocking", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deterministic_findings", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("specialist_findings", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("llm_findings", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("review_scope", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("model_invocation_id", sa.String(64), sa.ForeignKey("model_invocations.id", ondelete="SET NULL")),
        sa.Column("created_by", sa.String(64), nullable=False, server_default="observer"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for column in ("project_id", "episode_id", "target_id"):
        op.create_index(f"ix_observer_reviews_{column}", "observer_reviews", [column])
    op.add_column("shots", sa.Column("director_plan_revision_id", sa.String(64), nullable=True))
    op.add_column("shots", sa.Column("director_intent_key", sa.String(128), nullable=False, server_default=""))
    op.create_foreign_key("fk_shots_director_plan_revision_id", "shots", "director_plan_revisions", ["director_plan_revision_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_shots_director_plan_revision_id", "shots", ["director_plan_revision_id"])


def downgrade() -> None:
    op.drop_index("ix_shots_director_plan_revision_id", table_name="shots")
    op.drop_constraint("fk_shots_director_plan_revision_id", "shots", type_="foreignkey")
    op.drop_column("shots", "director_intent_key")
    op.drop_column("shots", "director_plan_revision_id")
    op.drop_table("observer_reviews")
    op.drop_table("director_plan_revisions")
