"""认证路由：登录、当前用户、登出。"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.schemas import LoginRequest, TokenResponse, UserResponse
from app.auth.security import create_access_token, verify_password
from app.db.database import get_session
from app.db.models import User

router = APIRouter()


@router.post("/auth/login", response_model=TokenResponse)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(User).where(User.username == payload.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token = create_access_token(subject=user.username, extra={"is_admin": user.is_admin})
    return TokenResponse(
        access_token=token,
        user=UserResponse(id=user.id, username=user.username, is_admin=user.is_admin),
    )


@router.get("/auth/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return UserResponse(id=user.id, username=user.username, is_admin=user.is_admin)


@router.post("/auth/logout")
async def logout(user: User = Depends(get_current_user)):
    """无服务端 token 黑名单，前端清除 localStorage 即可。返回 200 表示已确认。"""
    return {"ok": True}
