"""
全局测试配置

提供：
- 测试数据库 Session（使用独立测试数据库）
- Mock LLM 客户端（避免实际 API 调用）
- 测试用户数据工厂
- 测试 Excel fixtures 路径
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 设置测试环境变量（覆盖 .env 配置）
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("META_DB_HOST", "127.0.0.1")
os.environ.setdefault("META_DB_PORT", "5441")
os.environ.setdefault("META_DB_USER", "test_user")
os.environ.setdefault("META_DB_PASSWORD", "test_pass")
os.environ.setdefault("META_DB_NAME", "lgsteel_meta_test")
os.environ.setdefault("BIZ_DB_HOST", "127.0.0.1")
os.environ.setdefault("BIZ_DB_PORT", "5442")
os.environ.setdefault("BIZ_DB_USER", "test_user")
os.environ.setdefault("BIZ_DB_PASSWORD", "test_pass")
os.environ.setdefault("BIZ_DB_NAME", "lgsteel_biz_test")
os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("REDIS_PORT", "6391")
os.environ.setdefault("REDIS_PASSWORD", "")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-32-chars-xxxxxxxxx")
os.environ.setdefault("QIANWEN_API_KEY", "mock-key")
os.environ.setdefault("WENXIN_API_KEY", "mock-key")
os.environ.setdefault("WENXIN_SECRET_KEY", "mock-secret")

from app.config import get_settings  # noqa: E402 (after env setup)
from app.db.meta_session import MetaBase  # noqa: E402
from app.main import app  # noqa: E402

settings = get_settings()

# ---- 测试 Fixtures 路径 ----
FIXTURES_DIR = Path(__file__).parent / "fixtures"
EXCEL_FIXTURES_DIR = FIXTURES_DIR / "excel"
SQL_FIXTURES_DIR = FIXTURES_DIR / "sql"


# ---- Event Loop（pytest-asyncio）----
@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# ---- 测试元数据库（每个测试函数独立 transaction，测试后 rollback）----
@pytest.fixture(scope="session")
async def test_meta_engine():
    engine = create_async_engine(
        settings.meta_db_url,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(MetaBase.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(MetaBase.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def meta_session(test_meta_engine) -> AsyncGenerator[AsyncSession, None]:  # type: ignore[type-arg]
    """每个测试使用独立 transaction，测试后 rollback（保证测试隔离）"""
    async with test_meta_engine.connect() as conn:
        await conn.begin()
        session_factory = async_sessionmaker(
            bind=conn, class_=AsyncSession, expire_on_commit=False
        )
        async with session_factory() as session:
            yield session
        await conn.rollback()


# ---- FastAPI 测试客户端 ----
@pytest_asyncio.fixture(scope="session")
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


# ---- Mock LLM 客户端（防止真实 API 调用）----
@pytest.fixture
def mock_llm_router():
    """Mock LLM Router，返回预设 SQL"""
    mock = AsyncMock()
    mock.complete.return_value = MagicMock(
        text="SELECT COUNT(*) AS total FROM sales_orders",
        model="qianwen-max",
        prompt_tokens=100,
        completion_tokens=20,
    )
    return mock


@pytest.fixture
def mock_embedding_service():
    """Mock Embedding Service，返回固定维度向量"""
    mock = AsyncMock()
    mock.embed_single.return_value = [0.1] * 1536
    mock.embed.return_value = [[0.1] * 1536]
    return mock


# ---- 测试用户 Fixtures ----
@pytest.fixture
def test_admin_user() -> dict:
    return {
        "user_id": "00000000-0000-0000-0000-000000000001",
        "username": "test_admin",
        "display_name": "测试管理员",
        "role": "admin",
    }


@pytest.fixture
def test_analyst_user() -> dict:
    return {
        "user_id": "00000000-0000-0000-0000-000000000002",
        "username": "test_analyst",
        "display_name": "测试分析师",
        "role": "analyst",
    }


@pytest.fixture
def test_finance_user() -> dict:
    return {
        "user_id": "00000000-0000-0000-0000-000000000003",
        "username": "test_finance",
        "display_name": "测试财务用户",
        "role": "finance_user",
    }


@pytest.fixture
def test_data_manager_user() -> dict:
    return {
        "user_id": "00000000-0000-0000-0000-000000000004",
        "username": "test_data_manager",
        "display_name": "测试数据维护员",
        "role": "data_manager",
    }


# ---- 认证 Headers Fixtures（用于集成测试）----
@pytest_asyncio.fixture
async def auth_headers_admin(async_client: AsyncClient) -> dict:
    resp = await async_client.post(
        "/api/v1/auth/login",
        json={"username": "test_admin", "password": "Admin@2026!"},
    )
    if resp.status_code == 501:  # 阶段零 API 未实现，返回 mock token
        return {"Authorization": "Bearer mock-admin-token"}
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def auth_headers_analyst(async_client: AsyncClient) -> dict:
    resp = await async_client.post(
        "/api/v1/auth/login",
        json={"username": "test_analyst", "password": "Test@2026!"},
    )
    if resp.status_code == 501:
        return {"Authorization": "Bearer mock-analyst-token"}
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def auth_headers_finance_user(async_client: AsyncClient) -> dict:
    resp = await async_client.post(
        "/api/v1/auth/login",
        json={"username": "test_finance", "password": "Test@2026!"},
    )
    if resp.status_code == 501:
        return {"Authorization": "Bearer mock-finance-token"}
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def auth_headers_data_manager(async_client: AsyncClient) -> dict:
    resp = await async_client.post(
        "/api/v1/auth/login",
        json={"username": "test_data_manager", "password": "Test@2026!"},
    )
    if resp.status_code == 501:
        return {"Authorization": "Bearer mock-data-manager-token"}
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}
