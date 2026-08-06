"""generation_jobs 增加 workflow_run_id/workflow_step_id；asset_cards.project_id 可空

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-04

- P1-10：GenerationJob 增加 workflow_run_id / workflow_step_id 两列（FK SET NULL + 索引），
  pause/lease 可直接 JOIN workflow_runs，不再只靠 params JSON。
- P2-3：AssetCard.project_id 放宽为可空，支撑独立于项目的个人资产库（scope=personal）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("generation_jobs", sa.Column("workflow_run_id", sa.String(length=64), nullable=True))
    op.add_column("generation_jobs", sa.Column("workflow_step_id", sa.String(length=64), nullable=True))
    op.create_index("ix_generation_jobs_workflow_run_id", "generation_jobs", ["workflow_run_id"])
    op.create_index("ix_generation_jobs_workflow_step_id", "generation_jobs", ["workflow_step_id"])
    op.create_foreign_key(
        "fk_generation_jobs_workflow_run_id",
        "generation_jobs",
        "workflow_runs",
        ["workflow_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_generation_jobs_workflow_step_id",
        "generation_jobs",
        "workflow_steps",
        ["workflow_step_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.alter_column(
        "asset_cards",
        "project_id",
        existing_type=sa.String(length=64),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "asset_cards",
        "project_id",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.drop_constraint("fk_generation_jobs_workflow_step_id", "generation_jobs", type_="foreignkey")
    op.drop_constraint("fk_generation_jobs_workflow_run_id", "generation_jobs", type_="foreignkey")
    op.drop_index("ix_generation_jobs_workflow_step_id", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_workflow_run_id", table_name="generation_jobs")
    op.drop_column("generation_jobs", "workflow_step_id")
    op.drop_column("generation_jobs", "workflow_run_id")
