"""工作台总览 API：当前用户所有画布项目的进度、在跑任务与待处理事项。"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.database import get_session
from app.db.models import User
from app.services import workspace_overview_service

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/workspace/overview")
async def workspace_overview(
    session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)
):
    return await workspace_overview_service.overview(session, user)
