"""
认证 API

POST /api/v1/auth/login    — 用户登录，返回 JWT Token
POST /api/v1/auth/logout   — 注销（无状态，客户端清除 Token）
POST /api/v1/auth/refresh  — Token 刷新（需要有效旧 Token）
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.meta_session import get_meta_session
from app.db.models.user import User
from app.dependencies import CurrentUserIdDep, MetaSessionDep
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.common import ApiResponse
from app.security.auth import create_access_token, verify_password

router = APIRouter()


async def _get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    return result.scalar_one_or_none()


@router.post("/login", response_model=ApiResponse[TokenResponse])
async def login(
    request: LoginRequest,
    session: MetaSessionDep,
) -> ApiResponse[TokenResponse]:
    """
    用户登录。

    1. 按 username 查询活跃用户
    2. 验证密码哈希
    3. 颁发 JWT Access Token
    """
    result = await session.execute(
        select(User).where(User.username == request.username, User.is_active.is_(True))
    )
    user: User | None = result.scalar_one_or_none()

    if user is None or not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token, expires_in = create_access_token(user.id, user.username)
    return ApiResponse.ok(
        data=TokenResponse(
            access_token=token,
            token_type="bearer",
            expires_in=expires_in,
            user_id=str(user.id),
            username=user.username,
            display_name=user.display_name,
            role=user.role,
        )
    )


@router.post("/logout")
async def logout() -> ApiResponse[None]:
    """注销（无状态 JWT，客户端清除 Token 即可）"""
    return ApiResponse.ok(data=None, message="已注销")


@router.post("/refresh", response_model=ApiResponse[TokenResponse])
async def refresh_token(
    current_user_id: CurrentUserIdDep,
    session: MetaSessionDep,
) -> ApiResponse[TokenResponse]:
    """
    刷新 Token：验证旧 Token 有效后颁发新 Token。
    旧 Token 本身由 CurrentUserIdDep 校验签名与过期。
    """
    user = await _get_user_by_id(session, current_user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被禁用",
        )

    token, expires_in = create_access_token(user.id, user.username)
    return ApiResponse.ok(
        data=TokenResponse(
            access_token=token,
            token_type="bearer",
            expires_in=expires_in,
            user_id=str(user.id),
            username=user.username,
            display_name=user.display_name,
            role=user.role,
        )
    )
