"""
元数据库 AsyncSession 工厂

存储：用户、权限、审计日志、Q&A 示例、对话历史、数据源注册等元数据。
禁止：在此 Session 上执行业务数据查询（使用 biz_session）。
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

# 元数据库引擎
_meta_engine = create_async_engine(
    settings.meta_db_url,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800,
    echo=settings.debug,
)

# Session 工厂
_MetaSessionFactory = async_sessionmaker(
    _meta_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class MetaBase(DeclarativeBase):
    """元数据库 ORM 基类"""
    pass


async def get_meta_session() -> AsyncGenerator[AsyncSession, Any]:
    """FastAPI Depends 注入用"""
    async with _MetaSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
