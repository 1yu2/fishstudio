"""immutable regeneration quotes and cost audit

Revision ID: 0015
Revises: 0014
"""
import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("regeneration_requests", sa.Column("quote_id", sa.String(64), nullable=True))
    op.execute("UPDATE regeneration_requests SET quote_id = md5(id || clock_timestamp()::text)")
    op.alter_column("regeneration_requests", "quote_id", nullable=False)
    op.add_column("regeneration_requests", sa.Column("quoted_cost", sa.Numeric(12, 6), nullable=True))
    op.add_column("regeneration_requests", sa.Column("actual_cost", sa.Numeric(12, 6), nullable=True))
    op.add_column("regeneration_requests", sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"))
    op.add_column("regeneration_requests", sa.Column("price_table_version", sa.String(64), nullable=False, server_default=""))
    op.add_column("regeneration_requests", sa.Column("quote_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_regeneration_requests_quote_id", "regeneration_requests", ["quote_id"])


def downgrade() -> None:
    op.drop_constraint("uq_regeneration_requests_quote_id", "regeneration_requests", type_="unique")
    for column in ("quote_expires_at", "price_table_version", "currency", "actual_cost", "quoted_cost", "quote_id"):
        op.drop_column("regeneration_requests", column)
