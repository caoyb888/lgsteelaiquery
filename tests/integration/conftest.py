"""
集成测试 conftest

提供集成测试专用的 fixtures：真实数据库连接、已填充的业务数据表、
端到端 HTTP 客户端等。集成测试依赖 docker-compose.dev.yml 中的服务已启动。
"""
from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

# 跳过标记：没有 INTEGRATION_TEST 环境变量时跳过整个目录
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:  # noqa: ARG001
    if not os.getenv("INTEGRATION_TEST"):
        skip_mark = pytest.mark.skip(reason="Integration tests skipped (set INTEGRATION_TEST=1 to enable)")
        for item in items:
            if "integration" in str(item.fspath):
                item.add_marker(skip_mark)


# ---------------------------------------------------------------------------
# 业务数据库连接（集成测试专用）
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def biz_db_url() -> str:
    """从环境变量读取业务数据库连接 URL（开发实例）。"""
    host = os.getenv("BIZ_DB_HOST", "127.0.0.1")
    port = os.getenv("BIZ_DB_PORT", "5442")
    name = os.getenv("BIZ_DB_NAME", "lgsteel_biz")
    user = os.getenv("BIZ_DB_USER", "biz_user")
    password = os.getenv("BIZ_DB_PASSWORD", "biz_password")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}"


@pytest_asyncio.fixture(scope="session")
async def biz_engine(biz_db_url: str):  # type: ignore[no-untyped-def]
    engine = create_async_engine(biz_db_url, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def biz_session(biz_engine) -> AsyncGenerator[AsyncSession, None]:  # type: ignore[no-untyped-def]
    """每个集成测试独立事务，测试后回滚。"""
    async with biz_engine.connect() as conn:
        await conn.begin()
        session = AsyncSession(bind=conn)  # type: ignore[call-arg]
        try:
            yield session
        finally:
            await session.close()
            await conn.rollback()


# ---------------------------------------------------------------------------
# 已填充的销售业务表（session 级别，仅创建一次）
# ---------------------------------------------------------------------------

SALES_TABLE_NAME = f"sales_{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture(scope="session")
async def seeded_sales_table(biz_engine) -> AsyncGenerator[str, None]:  # type: ignore[no-untyped-def]
    """
    在业务库中创建并填充一张测试销售表，session 结束后删除。
    返回表名，供各测试查询使用。
    """
    create_ddl = f"""
    CREATE TABLE IF NOT EXISTS {SALES_TABLE_NAME} (
        id          SERIAL PRIMARY KEY,
        report_month VARCHAR(7)  NOT NULL,  -- '2026-01'
        product_line VARCHAR(50) NOT NULL,
        product_name VARCHAR(100) NOT NULL,
        revenue      NUMERIC(14,2) NOT NULL,
        volume       INTEGER NOT NULL,
        unit_price   NUMERIC(10,2) NOT NULL,
        customer     VARCHAR(100) NOT NULL
    );
    """
    insert_dml = f"""
    INSERT INTO {SALES_TABLE_NAME}
        (report_month, product_line, product_name, revenue, volume, unit_price, customer)
    VALUES
        ('2026-01', '板材', '热轧卷板', 1250.50, 8500, 1471.18, '客户A'),
        ('2026-01', '型钢', 'H型钢',   860.00,  6200, 1387.10, '客户B'),
        ('2026-02', '板材', '热轧卷板', 1380.20, 9200, 1500.22, '客户A'),
        ('2026-02', '型钢', 'H型钢',   920.00,  6800, 1352.94, '客户C'),
        ('2026-03', '板材', '热轧卷板', 1320.00, 8900, 1483.15, '客户B'),
        ('2026-03', '型钢', 'H型钢',   900.00,  6500, 1384.62, '客户A');
    """
    async with biz_engine.begin() as conn:
        await conn.execute(text(create_ddl))
        await conn.execute(text(insert_dml))

    yield SALES_TABLE_NAME

    async with biz_engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {SALES_TABLE_NAME};"))


@pytest_asyncio.fixture(scope="session")
async def seeded_finance_table(biz_engine) -> AsyncGenerator[str, None]:  # type: ignore[no-untyped-def]
    """在业务库中创建并填充测试财务表。"""
    table_name = f"finance_{uuid.uuid4().hex[:8]}"
    create_ddl = f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        id           SERIAL PRIMARY KEY,
        report_month VARCHAR(7)    NOT NULL,
        product_name VARCHAR(100)  NOT NULL,
        revenue      NUMERIC(14,2) NOT NULL,
        cost         NUMERIC(14,2) NOT NULL,
        gross_profit NUMERIC(14,2) GENERATED ALWAYS AS (revenue - cost) STORED
    );
    """
    insert_dml = f"""
    INSERT INTO {table_name} (report_month, product_name, revenue, cost)
    VALUES
        ('2026-01', '热轧卷板', 1250.50, 1050.00),
        ('2026-01', 'H型钢',   860.00,  720.00),
        ('2026-02', '热轧卷板', 1380.20, 1150.00),
        ('2026-02', 'H型钢',   920.00,  780.00),
        ('2026-03', '热轧卷板', 1320.00, 1100.00),
        ('2026-03', 'H型钢',   900.00,  760.00);
    """
    async with biz_engine.begin() as conn:
        await conn.execute(text(create_ddl))
        await conn.execute(text(insert_dml))

    yield table_name

    async with biz_engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {table_name};"))
