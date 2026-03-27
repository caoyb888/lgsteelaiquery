"""
认证工具模块

JWT 令牌创建 / 验证 + 密码哈希工具。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from jose import jwt
from passlib.context import CryptContext

from app.config import get_settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """使用 bcrypt 哈希密码"""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """验证明文密码与哈希是否匹配"""
    return _pwd_context.verify(plain, hashed)


def create_access_token(user_id: uuid.UUID, username: str) -> tuple[str, int]:
    """
    生成 JWT Access Token。

    Returns:
        (token_string, expires_in_seconds)
    """
    settings = get_settings()
    expire_seconds = settings.jwt_expire_minutes * 60
    now = datetime.now(tz=timezone.utc)
    expire = now + timedelta(seconds=expire_seconds)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    token = jwt.encode(payload, settings.app_secret_key, algorithm=settings.jwt_algorithm)
    return token, expire_seconds
