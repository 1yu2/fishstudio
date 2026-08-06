"""拆分 Shot 选择槽 + UsageEvent cost_source

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-05

复审 0.8 / R0.5：拆分图片/视频选择槽，避免候选互相覆盖。
- shots 表：新增 selected_image_artifact_id 和 selected_video_artifact_id，
  迁移旧 selected_artifact_id 后删除旧列和旧外键。
- usage_events 表：新增 cost_source 列。
- 降级为有损操作：双选择合并回单选择，优先视频。

此迁移为确定性 DDL，不使用 information_schema 分支，确保在线/离线一致。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 新增拆分选择列
    op.add_column(
        "shots",
        sa.Column("selected_image_artifact_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "shots",
        sa.Column("selected_video_artifact_id", sa.String(length=64), nullable=True),
    )

    # 2. 建外键
    op.create_foreign_key(
        "fk_shots_selected_image_artifact_id",
        "shots",
        "artifacts",
        ["selected_image_artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_shots_selected_video_artifact_id",
        "shots",
        "artifacts",
        ["selected_video_artifact_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3. 按 Artifact 类型回填旧选择到新列
    op.execute(
        sa.text(
            """
            UPDATE shots
            SET selected_image_artifact_id = artifacts.id
            FROM artifacts
            WHERE shots.selected_artifact_id = artifacts.id
              AND artifacts.type = 'image'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE shots
            SET selected_video_artifact_id = artifacts.id
            FROM artifacts
            WHERE shots.selected_artifact_id = artifacts.id
              AND artifacts.type = 'video'
            """
        )
    )

    # 4. 删除旧外键和旧列
    op.drop_constraint("fk_shots_selected_artifact_id", "shots", type_="foreignkey")
    op.drop_column("shots", "selected_artifact_id")

    # 5. 新增 cost_source 列
    op.add_column(
        "usage_events",
        sa.Column(
            "cost_source",
            sa.String(length=32),
            nullable=False,
            server_default="estimate",
        ),
    )


def downgrade() -> None:
    # 降级为有损操作：双选择合并回单选择（优先 video）

    # 1. 恢复旧选择列和外键
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

    # 2. 回填：优先 video，其次 image
    op.execute(
        sa.text(
            """
            UPDATE shots
            SET selected_artifact_id = COALESCE(
                selected_video_artifact_id,
                selected_image_artifact_id
            )
            """
        )
    )

    # 3. 删除拆分列
    op.drop_constraint("fk_shots_selected_video_artifact_id", "shots", type_="foreignkey")
    op.drop_constraint("fk_shots_selected_image_artifact_id", "shots", type_="foreignkey")
    op.drop_column("shots", "selected_video_artifact_id")
    op.drop_column("shots", "selected_image_artifact_id")

    # 4. 删除 cost_source 列
    op.drop_column("usage_events", "cost_source")
