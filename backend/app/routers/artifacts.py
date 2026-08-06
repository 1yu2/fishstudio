"""Artifact 受保护下载：/api/artifacts/{artifact_id}/download（review P0-3）。

- project_id 非空：仅项目 owner（或 admin）可下载。
- project_id 为 NULL：artifact 本身没有 user 字段，无法判定创建者，
  过渡期内仅 admin 可下载。
- 一律 404 响应无权/不存在，不泄露 artifact 存在性。
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import Artifact, SeriesProject, User
from app.services.asset_service import STORAGE_DIR
from app.services.ownership import check_project_access

logger = logging.getLogger(__name__)

router = APIRouter()

_STORAGE_ROOT = STORAGE_DIR.resolve()


def _resolve_storage_path(storage_key: str) -> Path:
    """把 storage_key 解析为 storage 根下的绝对路径，防目录穿越。"""
    path = (_STORAGE_ROOT / storage_key).resolve()
    if not path.is_relative_to(_STORAGE_ROOT):
        raise HTTPException(status_code=404, detail="artifact not found")
    return path


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")

    if artifact.project_id is not None:
        project = await session.get(SeriesProject, artifact.project_id)
        # 无权访问与不存在统一 404，不泄露存在性（含历史无主项目，仅 admin 可过）
        if project is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        try:
            check_project_access(project, user)
        except PermissionError:
            raise HTTPException(status_code=404, detail="artifact not found") from None
    elif not user.is_admin:
        # project_id 为 NULL 的 artifact 没有创建者字段可校验，过渡期仅 admin 可下
        raise HTTPException(status_code=404, detail="artifact not found")

    path = _resolve_storage_path(artifact.storage_key)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="artifact file not found")

    return FileResponse(
        str(path),
        media_type=artifact.mime_type or None,
        filename=path.name,
    )
