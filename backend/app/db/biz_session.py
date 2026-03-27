"""
业务数据库 AsyncSession 工厂

存储：由 Excel 导入的业务数据（动态表，表名格式：{domain}_{uuid_short}）。
权限：只读账号，严禁 DDL 操作（DDL 仅允许 DataLoader 在特权连接下执行）。
禁止：在此 Session 上执行元数据操作（使用 meta_session）。
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

settings = get_settings()

# 业务数据库引擎（只读查询，连接池相对小）
_biz_engine = create_async_engine(
    settings.biz_db_url,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800,
    echo=settings.debug,
    # 超时控制（server-side statement_timeout 在执行层注入）
)

# Session 工厂
_BizSessionFactory = async_sessionmaker(
    _biz_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


def get_biz_session_factory() -> async_sessionmaker[AsyncSession]:
    """返回业务数据库 Session 工厂（供 SQLExecutor 等非 Depends 场景使用）"""
    return _BizSessionFactory


async def get_biz_session() -> AsyncGenerator[AsyncSession, Any]:
    """FastAPI Depends 注入用（只读查询）"""
    async with _BizSessionFactory() as session:
        try:
            yield session
            # 业务库只读，无需 commit
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_biz_session_rw() -> AsyncGenerator[AsyncSession, Any]:
    """
    业务库读写 Session，仅供 DataLoader（Excel 入库任务）使用。
    严禁在查询路径中使用此 Session。
    """
    async with _BizSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
