"""
FastAPI 依赖注入

提供：
- 数据库 Session 注入
- 当前用户提取（JWT 验证）
- Redis 客户端注入
- 当前用户完整信息（含角色）
"""
from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.biz_session import get_biz_session
from app.db.meta_session import get_meta_session
from app.utils.exceptions import AuthenticationError

settings = get_settings()

# ---- 数据库 Session 依赖 ----

MetaSessionDep = Annotated[AsyncSession, Depends(get_meta_session)]
BizSessionDep = Annotated[AsyncSession, Depends(get_biz_session)]

# ---- JWT Bearer 认证 ----

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> uuid.UUID:
    """从 JWT Token 中提取用户 ID，校验签名与过期"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证 Token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload: dict[str, Any] = jwt.decode(
            credentials.credentials,
            settings.app_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        user_id_str: str | None = payload.get("sub")
        if user_id_str is None:
            raise AuthenticationError("Token 中缺少用户信息")
        return uuid.UUID(user_id_str)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


CurrentUserIdDep = Annotated[uuid.UUID, Depends(get_current_user_id)]


# ---- Redis 客户端依赖 ----

_redis_client: Redis | None = None


async def get_redis() -> Redis:
    """返回全局 Redis 客户端（懒初始化）"""
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=False,
        )
    return _redis_client


RedisDep = Annotated[Redis, Depends(get_redis)]


# ---- 当前用户角色 ----

async def get_current_user_role(
    user_id: CurrentUserIdDep,
    session: MetaSessionDep,
) -> str:
    """从元数据库查询当前用户的角色"""
    from app.db.models.user import User  # 避免循环导入

    result = await session.execute(
        select(User.role).where(User.id == user_id, User.is_active.is_(True))
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被禁用",
        )
    return row


CurrentUserRoleDep = Annotated[str, Depends(get_current_user_role)]
