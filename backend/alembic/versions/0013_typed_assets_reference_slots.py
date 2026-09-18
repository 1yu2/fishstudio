"""typed asset profiles and shot reference slots

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("asset_cards", sa.Column("profile_schema_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("asset_cards", sa.Column("profile", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("shot_asset_refs", sa.Column("asset_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("shot_asset_refs", sa.Column("reference_type", sa.String(32), nullable=False, server_default="character"))
    op.add_column("shot_asset_refs", sa.Column("artifact_id", sa.String(64), nullable=True))
    op.add_column("shot_asset_refs", sa.Column("subject", sa.String(255), nullable=False, server_default=""))
    op.add_column("shot_asset_refs", sa.Column("weight", sa.Float(), nullable=False, server_default="1"))
    op.add_column("shot_asset_refs", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("shot_asset_refs", sa.Column("usage", sa.String(64), nullable=False, server_default="identity"))
    op.create_foreign_key("fk_shot_asset_refs_artifact_id", "shot_asset_refs", "artifacts", ["artifact_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("fk_shot_asset_refs_artifact_id", "shot_asset_refs", type_="foreignkey")
    for column in ("usage", "sort_order", "weight", "subject", "artifact_id", "reference_type", "asset_version"):
        op.drop_column("shot_asset_refs", column)
    op.drop_column("asset_cards", "profile")
    op.drop_column("asset_cards", "profile_schema_version")
