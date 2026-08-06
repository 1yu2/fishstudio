"""重试代际字段 + Shot 级选择

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-04

- GenerationJob 增加 step_attempt（创建时从 step.attempt 拷贝）/
  logical_job_key（同一逻辑任务多代重试共享，+索引）/superseded_by
  （job 级 retry 时旧 job 回填新 job.id）——聚合逻辑 Wave B 接管，先落字段。
- Shot 增加 selected_artifact_id（FK artifacts.id SET NULL）：
  前端按 shot 选择产物时写 shot 级选择，episode 级 render 选择仍在
  WorkflowStep.selected_artifact_id。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "generation_jobs",
        sa.Column("step_attempt", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "generation_jobs",
        sa.Column("logical_job_key", sa.String(length=255), nullable=False, server_default=""),
    )
    op.create_index(
        "ix_generation_jobs_logical_job_key", "generation_jobs", ["logical_job_key"]
    )
    op.add_column(
        "generation_jobs",
        sa.Column("superseded_by", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "shots",
        sa.Column("selected_artifact_id", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_shots_selected_artifact_id",
        "shots",
        "artifacts",
        ["selected_artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_shots_selected_artifact_id", "shots", type_="foreignkey")
    op.drop_column("shots", "selected_artifact_id")
    op.drop_column("generation_jobs", "superseded_by")
    op.drop_index("ix_generation_jobs_logical_job_key", table_name="generation_jobs")
    op.drop_column("generation_jobs", "logical_job_key")
    op.drop_column("generation_jobs", "step_attempt")
