"""final QC reports, publications and publish packages

Revision ID: 0017
Revises: 0016
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qc_reports",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("episode_id", sa.String(64), sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timeline_revision_id", sa.String(64), sa.ForeignKey("timeline_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("render_artifact_id", sa.String(64), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False, server_default="final_qc"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("technical_checks", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("semantic_checks", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("issues", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("repair_targets", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "publications",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(64), sa.ForeignKey("series_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("episode_id", sa.String(64), sa.ForeignKey("episodes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timeline_revision_id", sa.String(64), sa.ForeignKey("timeline_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("render_artifact_id", sa.String(64), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cover_artifact_id", sa.String(64), sa.ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("qc_report_id", sa.String(64), sa.ForeignKey("qc_reports.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("platform", sa.String(64), nullable=False, server_default="xiaohongshu"),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("specifications", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("external_post_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "publish_packages",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("publication_id", sa.String(64), sa.ForeignKey("publications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("manifest_artifact_id", sa.String(64), sa.ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("package_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(32), nullable=False, server_default="ready"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("publication_id", "version", name="uq_publish_packages_publication_version"),
    )
    for table, columns in {
        "qc_reports": ("project_id", "episode_id", "render_artifact_id", "status"),
        "publications": ("project_id", "episode_id", "status"),
        "publish_packages": ("publication_id", "status"),
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    op.drop_table("publish_packages")
    op.drop_table("publications")
    op.drop_table("qc_reports")
