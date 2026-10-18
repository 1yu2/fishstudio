"""个人资产库路由（P2-3）：scope=personal、project_id=NULL 的独立资产 API。

与项目下 /api/series/projects/{id}/assets 的区别：这里创建/操作的资产不属于任何
项目，按当前登录用户隔离。权限约定：personal 资产仅 owner 本人可见/操作——
admin 不默认放行（个人资产无"历史无主认领"场景，保持最严隔离，见
asset_service.get_personal_asset_for_user）。

错误映射约定同 series 路由：PermissionError → 403；不存在/无权 → 404；参数错误 → 400。
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import User
from app.schemas.series import AssetCardOut
from app.services import asset_service, series_service

router = APIRouter(dependencies=[Depends(get_current_user)])


def _403(e: PermissionError) -> HTTPException:
    return HTTPException(status_code=403, detail=str(e) or "无权访问")


class PersonalAssetCreate(BaseModel):
    """创建个人资产请求体：scope 固定 personal，project_id 固定 NULL。"""

    type: str
    name: str
    description: str = ""
    provider_meta: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    profile_schema_version: int = 1
    profile: dict[str, Any] = Field(default_factory=dict)


@router.post("/assets/personal", response_model=AssetCardOut)
async def create_personal_asset(
    body: PersonalAssetCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """创建个人资产（scope=personal、project_id=NULL、user_id=当前用户）。"""
    try:
        kwargs = {}
        if body.profile:
            kwargs = {"profile_schema_version": body.profile_schema_version, "profile": body.profile}
        return await asset_service.create_asset_card(
            session,
            None,
            user.id,
            type=body.type,
            name=body.name,
            description=body.description,
            provider_meta=body.provider_meta,
            tags=body.tags,
            scope="personal",
            **kwargs,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/assets/personal", response_model=list[AssetCardOut])
async def list_personal_assets(
    type: Optional[str] = None,
    tag: Optional[str] = None,
    q: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """我的个人资产列表（强制 user 隔离）。"""
    return await asset_service.list_assets(
        session, user_id=user.id, scope="personal", type=type, tag=tag, q=q
    )


class PersonalNewVersionRequest(BaseModel):
    updates: dict[str, Any] = Field(default_factory=dict)


@router.post("/assets/personal/{asset_id}/versions", response_model=AssetCardOut)
async def new_personal_asset_version(
    asset_id: str,
    body: PersonalNewVersionRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """个人资产新版本（复用 new_asset_version：version+1 新行，旧行 retired）。

    无权与不存在统一 404，不泄露存在性。
    """
    asset = await asset_service.get_personal_asset_for_user(session, asset_id, user.id)
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在")
    try:
        return await asset_service.new_asset_version(session, asset_id, user.id, body.updates)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class PersonalCopyToProjectRequest(BaseModel):
    project_id: str


@router.post("/assets/personal/{asset_id}/copy-to-project", response_model=AssetCardOut)
async def copy_personal_asset_to_project(
    asset_id: str,
    body: PersonalCopyToProjectRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """把我的个人资产复制进目标项目（新行 version=1、source=原资产 id）。

    目标项目必须归当前用户（admin 走项目通用的 admin 语义）；源资产仅 owner
    可复制（service 内 PermissionError）。
    """
    try:
        project = await series_service.get_project(session, body.project_id, user.id, is_admin=user.is_admin)
    except PermissionError as e:
        raise _403(e)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    try:
        return await asset_service.copy_personal_asset_to_project(session, asset_id, body.project_id, user.id)
    except PermissionError as e:
        raise _403(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
