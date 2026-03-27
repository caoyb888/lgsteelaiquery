"""
tests/unit/test_sql_executor.py

SQLExecutor 单元测试。
全程使用 AsyncMock 模拟 biz_session、RowLevelFilter、Redis；
不依赖真实数据库或网络。
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.core.sql_executor import QueryResult, SQLExecutor, _make_cache_key
from app.utils.exceptions import DataPermissionError, QueryTimeoutError, SQLExecutionError


# ─── Fixtures ───────────────────────────────────────────────────────────────


def _make_mock_session(rows: list[dict]):
    """构造返回固定行列表的 AsyncSession mock。"""
    mock_row_list = []
    for row_dict in rows:
        mock_row = MagicMock()
        mock_row._mapping = row_dict
        mock_row_list.append(mock_row)

    mock_result = MagicMock()
    mock_result.fetchall.return_value = mock_row_list

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    return mock_session


@asynccontextmanager
async def _mock_session_factory_ctx(rows):
    session = _make_mock_session(rows)
    yield session


def _make_session_factory(rows: list[dict]):
    def factory():
        return _mock_session_factory_ctx(rows)
    return factory


def _make_redis(cached_value: bytes | None = None) -> AsyncMock:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=cached_value)
    redis.set = AsyncMock()
    return redis


def _make_row_filter(permitted: bool = True) -> AsyncMock:
    row_filter = AsyncMock()
    if permitted:
        row_filter.inject_permission = AsyncMock(side_effect=lambda sql, **kw: sql)
    else:
        row_filter.inject_permission = AsyncMock(
            side_effect=DataPermissionError("无权访问")
        )
    return row_filter


def _make_settings(**overrides):
    settings = MagicMock()
    settings.query_max_rows = overrides.get("query_max_rows", 10000)
    settings.query_timeout_seconds = overrides.get("query_timeout_seconds", 30)
    settings.query_result_cache_ttl = overrides.get("query_result_cache_ttl", 300)
    return settings


_SENTINEL = object()


def _make_executor(rows=_SENTINEL, cached=None, permitted=True, **settings_overrides):
    if rows is _SENTINEL:
        rows = [{"amount": 100, "month": "2026-01"}]
    redis = _make_redis(cached)
    row_filter = _make_row_filter(permitted)
    settings = _make_settings(**settings_overrides)
    session_factory = _make_session_factory(rows)
    executor = SQLExecutor(
        biz_session_factory=session_factory,
        row_filter=row_filter,
        redis_client=redis,
        settings=settings,
    )
    return executor, redis, row_filter


# ─── _make_cache_key ────────────────────────────────────────────────────────


def test_cache_key_format():
    key = _make_cache_key("SELECT 1", "analyst")
    assert key.startswith("query_cache:")
    # 去掉前缀后是 16 位 hex
    suffix = key[len("query_cache:"):]
    assert len(suffix) == 16
    assert all(c in "0123456789abcdef" for c in suffix)


def test_cache_key_different_sql():
    k1 = _make_cache_key("SELECT 1", "analyst")
    k2 = _make_cache_key("SELECT 2", "analyst")
    assert k1 != k2


def test_cache_key_different_role():
    k1 = _make_cache_key("SELECT 1", "analyst")
    k2 = _make_cache_key("SELECT 1", "admin")
    assert k1 != k2


def test_cache_key_deterministic():
    assert _make_cache_key("SELECT 1", "analyst") == _make_cache_key("SELECT 1", "analyst")


# ─── 缓存命中 ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_hit_returns_from_cache():
    cached_data = {
        "rows": [{"amount": 999}],
        "columns": ["amount"],
        "total_rows": 1,
        "truncated": False,
        "execution_ms": 5,
    }
    cached_bytes = json.dumps(cached_data).encode()
    executor, redis, row_filter = _make_executor(cached=cached_bytes)

    result = await executor.execute(
        sql="SELECT amount FROM sales_orders",
        user_role="analyst",
        allowed_tables={"sales_orders"},
    )

    assert result.from_cache is True
    assert result.rows == [{"amount": 999}]
    assert result.columns == ["amount"]
    assert result.total_rows == 1
    assert result.truncated is False
    # 命中缓存时不应调用 row_filter
    row_filter.inject_permission.assert_not_called()
    # 命中缓存时不应再次写 Redis
    redis.set.assert_not_called()


# ─── 正常执行流程 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_success_basic():
    rows = [{"amount": 100}, {"amount": 200}]
    executor, redis, row_filter = _make_executor(rows=rows)

    result = await executor.execute(
        sql="SELECT amount FROM sales_orders",
        user_role="analyst",
        allowed_tables={"sales_orders"},
    )

    assert result.from_cache is False
    assert len(result.rows) == 2
    assert result.columns == ["amount"]
    assert result.total_rows == 2
    assert result.truncated is False
    assert result.execution_ms >= 0
    row_filter.inject_permission.assert_awaited_once()
    redis.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_writes_cache():
    rows = [{"v": 1}]
    executor, redis, _ = _make_executor(rows=rows)

    await executor.execute(
        sql="SELECT v FROM t",
        user_role="analyst",
        allowed_tables={"t"},
    )

    redis.set.assert_awaited_once()
    call_args = redis.set.call_args
    # 第三个关键字参数应为 ex=300 (cache ttl)
    assert call_args.kwargs.get("ex") == 300
    # 缓存内容应是合法 JSON
    cache_payload_str = call_args.args[1]
    data = json.loads(cache_payload_str)
    assert "rows" in data
    assert "columns" in data
    assert "total_rows" in data


@pytest.mark.asyncio
async def test_execute_empty_result():
    executor, _, _ = _make_executor(rows=[])

    result = await executor.execute(
        sql="SELECT amount FROM sales_orders WHERE 1=0",
        user_role="analyst",
        allowed_tables={"sales_orders"},
    )

    assert result.rows == []
    assert result.columns == []
    assert result.total_rows == 0
    assert result.truncated is False


# ─── LIMIT 注入 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_limit_injected_into_sql():
    """验证执行时 SQL 末尾被追加 LIMIT。"""
    executed_sqls: list[str] = []

    mock_result = MagicMock()
    mock_result.fetchall.return_value = []

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def capturing_factory():
        # Capture the SQL that would be executed
        original_execute = mock_session.execute

        async def capture_execute(stmt):
            executed_sqls.append(str(stmt))
            return await original_execute(stmt)

        mock_session.execute = capture_execute
        yield mock_session

    redis = _make_redis()
    row_filter = _make_row_filter()
    settings = _make_settings(query_max_rows=10000)

    executor = SQLExecutor(
        biz_session_factory=capturing_factory,
        row_filter=row_filter,
        redis_client=redis,
        settings=settings,
    )

    await executor.execute(
        sql="SELECT amount FROM t",
        user_role="analyst",
        allowed_tables={"t"},
    )

    assert len(executed_sqls) == 1
    assert "LIMIT 10001" in executed_sqls[0]


@pytest.mark.asyncio
async def test_limit_removes_trailing_semicolon():
    """末尾分号应被去除，再追加 LIMIT。"""
    executed_sqls: list[str] = []

    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def capturing_factory():
        async def capture(stmt):
            executed_sqls.append(str(stmt))
            return mock_result
        mock_session.execute = capture
        yield mock_session

    redis = _make_redis()
    row_filter = _make_row_filter()
    settings = _make_settings(query_max_rows=100)

    executor = SQLExecutor(
        biz_session_factory=capturing_factory,
        row_filter=row_filter,
        redis_client=redis,
        settings=settings,
    )

    await executor.execute(
        sql="SELECT amount FROM t;",
        user_role="analyst",
        allowed_tables={"t"},
    )

    assert "LIMIT 101" in executed_sqls[0]
    # 不应有双分号
    assert ";;" not in executed_sqls[0]


# ─── 截断逻辑 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_truncation_when_exceeds_max_rows():
    """返回行数 > max_rows 时应截断，truncated=True。"""
    # max_rows=3，返回 4 行 → 截断
    rows = [{"n": i} for i in range(4)]
    executor, _, _ = _make_executor(rows=rows, query_max_rows=3)

    result = await executor.execute(
        sql="SELECT n FROM t",
        user_role="analyst",
        allowed_tables={"t"},
    )

    assert result.truncated is True
    assert result.total_rows == 3
    assert len(result.rows) == 3
    # 应保留前 3 行
    assert result.rows == [{"n": 0}, {"n": 1}, {"n": 2}]


@pytest.mark.asyncio
async def test_no_truncation_when_within_max_rows():
    rows = [{"n": i} for i in range(3)]
    executor, _, _ = _make_executor(rows=rows, query_max_rows=3)

    result = await executor.execute(
        sql="SELECT n FROM t",
        user_role="analyst",
        allowed_tables={"t"},
    )

    assert result.truncated is False
    assert result.total_rows == 3


# ─── 权限拒绝 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_permission_denied_raises():
    executor, _, _ = _make_executor(permitted=False)

    with pytest.raises(DataPermissionError):
        await executor.execute(
            sql="SELECT amount FROM finance_ledger",
            user_role="sales_user",
            allowed_tables=set(),
        )


# ─── 超时 ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_timeout_raises():
    """模拟 asyncio.TimeoutError → 应抛出 QueryTimeoutError。"""
    redis = _make_redis()
    row_filter = _make_row_filter()
    settings = _make_settings(query_timeout_seconds=1)

    async def slow_query(sql: str):
        await asyncio.sleep(10)
        return []

    executor = SQLExecutor(
        biz_session_factory=None,  # 不会用到
        row_filter=row_filter,
        redis_client=redis,
        settings=settings,
    )
    # 直接 patch _run_query
    with patch.object(executor, "_run_query", side_effect=slow_query):
        with pytest.raises(QueryTimeoutError):
            await executor.execute(
                sql="SELECT 1",
                user_role="analyst",
                allowed_tables={"t"},
                user_id="u1",
            )


# ─── SQL 执行异常 ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sql_execution_error_propagates():
    mock_result = MagicMock()
    mock_result.fetchall.side_effect = Exception("DB error")
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def failing_factory():
        yield mock_session

    redis = _make_redis()
    row_filter = _make_row_filter()
    settings = _make_settings()

    executor = SQLExecutor(
        biz_session_factory=failing_factory,
        row_filter=row_filter,
        redis_client=redis,
        settings=settings,
    )

    with pytest.raises(SQLExecutionError):
        await executor.execute(
            sql="SELECT 1",
            user_role="analyst",
            allowed_tables={"t"},
        )


# ─── row_filter 获取正确参数 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_row_filter_receives_correct_args():
    rows = [{"v": 1}]
    executor, _, row_filter = _make_executor(rows=rows)

    await executor.execute(
        sql="SELECT v FROM sales_orders",
        user_role="sales_user",
        allowed_tables={"sales_orders"},
    )

    row_filter.inject_permission.assert_awaited_once_with(
        sql="SELECT v FROM sales_orders",
        user_role="sales_user",
        allowed_tables={"sales_orders"},
    )


# ─── _run_query ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_query_maps_rows_correctly():
    rows = [{"a": 1, "b": "hello"}, {"a": 2, "b": "world"}]
    session_factory = _make_session_factory(rows)
    redis = _make_redis()
    row_filter = _make_row_filter()
    settings = _make_settings()

    executor = SQLExecutor(
        biz_session_factory=session_factory,
        row_filter=row_filter,
        redis_client=redis,
        settings=settings,
    )

    result = await executor._run_query("SELECT a, b FROM t")
    assert result == [{"a": 1, "b": "hello"}, {"a": 2, "b": "world"}]


@pytest.mark.asyncio
async def test_run_query_raises_sql_execution_error_on_db_error():
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=Exception("connection error"))

    @asynccontextmanager
    async def factory():
        yield mock_session

    executor = SQLExecutor(
        biz_session_factory=factory,
        row_filter=AsyncMock(),
        redis_client=AsyncMock(),
        settings=_make_settings(),
    )

    with pytest.raises(SQLExecutionError, match="SQL 执行失败"):
        await executor._run_query("SELECT 1")
