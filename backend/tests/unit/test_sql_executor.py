"""
单元测试：app/core/sql_executor.py

覆盖场景：
- 缓存命中直接返回，不执行 SQL
- SQL 执行成功，正确映射 rows 和 columns
- 超过 query_max_rows 时 truncated=True
- 查询超时抛 QueryTimeoutError
- row_filter 权限校验失败抛 DataPermissionError
- 写入 Redis 缓存（验证 set 被调用，key 格式正确）
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.core.sql_executor import QueryResult, SQLExecutor, _make_cache_key
from app.utils.exceptions import DataPermissionError, QueryTimeoutError, SQLExecutionError


# ---------------------------------------------------------------------------
# 测试辅助：最小化 Settings
# ---------------------------------------------------------------------------

def _make_settings(**overrides: Any) -> Settings:
    base = dict(
        query_max_rows=5,
        query_timeout_seconds=30,
        query_result_cache_ttl=300,
        meta_db_password="x",
        biz_db_password="x",
        redis_password="x",
    )
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# 测试辅助：构造 mock session
# ---------------------------------------------------------------------------

def _make_mock_biz_session_factory(rows: list[dict[str, Any]]) -> Any:
    """返回一个 async context manager 工厂，execute 时返回指定行集。"""

    class FakeRow:
        def __init__(self, data: dict[str, Any]) -> None:
            self._mapping = data

    class FakeResult:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self._rows = [FakeRow(r) for r in rows]

        def fetchall(self) -> list[FakeRow]:
            return self._rows

    mock_session = AsyncMock()
    mock_session.execute.return_value = FakeResult(rows)

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=mock_ctx)
    return factory


def _make_mock_row_filter(raise_permission_error: bool = False) -> Any:
    """返回 mock RowLevelFilter。"""
    rf = AsyncMock()
    if raise_permission_error:
        rf.inject_permission.side_effect = DataPermissionError("无权限")
    else:
        rf.inject_permission.side_effect = lambda sql, **_: asyncio.coroutine(
            lambda: sql
        )()
    return rf


def _passthrough_row_filter() -> Any:
    """原样返回 sql 的 row_filter。"""
    rf = AsyncMock()

    async def _pass(sql: str, user_role: str, allowed_tables: set[str]) -> str:
        return sql

    rf.inject_permission.side_effect = _pass
    return rf


def _make_mock_redis(cached_value: str | None = None) -> Any:
    redis = AsyncMock()
    redis.get.return_value = cached_value
    redis.set.return_value = True
    return redis


# ---------------------------------------------------------------------------
# 辅助：_make_cache_key 测试
# ---------------------------------------------------------------------------

class TestMakeCacheKey:
    def test_format(self) -> None:
        key = _make_cache_key("SELECT 1", "admin")
        assert key.startswith("query_cache:")
        suffix = key[len("query_cache:"):]
        assert len(suffix) == 16

    def test_deterministic(self) -> None:
        assert _make_cache_key("SELECT 1", "admin") == _make_cache_key("SELECT 1", "admin")

    def test_different_role_yields_different_key(self) -> None:
        assert _make_cache_key("SELECT 1", "admin") != _make_cache_key("SELECT 1", "viewer")


# ---------------------------------------------------------------------------
# 主测试类
# ---------------------------------------------------------------------------

class TestSQLExecutorCacheHit:
    """缓存命中时直接返回，不执行 SQL。"""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_without_db_call(self) -> None:
        cached_data = {
            "rows": [{"a": 1}],
            "columns": ["a"],
            "total_rows": 1,
            "truncated": False,
            "execution_ms": 10,
        }
        redis = _make_mock_redis(cached_value=json.dumps(cached_data))
        factory = _make_mock_biz_session_factory([])
        rf = _passthrough_row_filter()

        executor = SQLExecutor(factory, rf, redis, _make_settings())
        result = await executor.execute("SELECT a FROM t", "admin", {"t"})

        assert result.from_cache is True
        assert result.rows == [{"a": 1}]
        assert result.columns == ["a"]
        assert result.total_rows == 1
        assert result.truncated is False
        # session 工厂不应被调用
        factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_hit_execution_ms_from_cache(self) -> None:
        cached_data = {
            "rows": [],
            "columns": [],
            "total_rows": 0,
            "truncated": False,
            "execution_ms": 42,
        }
        redis = _make_mock_redis(cached_value=json.dumps(cached_data))
        factory = _make_mock_biz_session_factory([])
        rf = _passthrough_row_filter()

        executor = SQLExecutor(factory, rf, redis, _make_settings())
        result = await executor.execute("SELECT 1", "admin", set())
        assert result.execution_ms == 42


class TestSQLExecutorNoCacheHit:
    """无缓存时正常执行 SQL。"""

    @pytest.mark.asyncio
    async def test_rows_and_columns_mapped_correctly(self) -> None:
        db_rows = [{"name": "张三", "amount": 100}, {"name": "李四", "amount": 200}]
        factory = _make_mock_biz_session_factory(db_rows)
        redis = _make_mock_redis()
        rf = _passthrough_row_filter()

        executor = SQLExecutor(factory, rf, redis, _make_settings(query_max_rows=10))
        result = await executor.execute("SELECT name, amount FROM t", "admin", {"t"})

        assert result.from_cache is False
        assert result.rows == db_rows
        assert result.columns == ["name", "amount"]
        assert result.total_rows == 2
        assert result.truncated is False

    @pytest.mark.asyncio
    async def test_execution_ms_is_non_negative(self) -> None:
        factory = _make_mock_biz_session_factory([{"v": 1}])
        redis = _make_mock_redis()
        rf = _passthrough_row_filter()

        executor = SQLExecutor(factory, rf, redis, _make_settings())
        result = await executor.execute("SELECT v FROM t", "admin", {"t"})
        assert result.execution_ms >= 0

    @pytest.mark.asyncio
    async def test_empty_result_set(self) -> None:
        factory = _make_mock_biz_session_factory([])
        redis = _make_mock_redis()
        rf = _passthrough_row_filter()

        executor = SQLExecutor(factory, rf, redis, _make_settings())
        result = await executor.execute("SELECT 1 WHERE false", "admin", set())

        assert result.rows == []
        assert result.columns == []
        assert result.total_rows == 0
        assert result.truncated is False


class TestSQLExecutorTruncation:
    """超过 query_max_rows 时 truncated=True。"""

    @pytest.mark.asyncio
    async def test_truncated_when_exceeds_max_rows(self) -> None:
        # max_rows=3，DB 返回 4 行（模拟 LIMIT max+1 后多一行）
        db_rows = [{"id": i} for i in range(4)]
        factory = _make_mock_biz_session_factory(db_rows)
        redis = _make_mock_redis()
        rf = _passthrough_row_filter()

        executor = SQLExecutor(factory, rf, redis, _make_settings(query_max_rows=3))
        result = await executor.execute("SELECT id FROM t", "admin", {"t"})

        assert result.truncated is True
        assert result.total_rows == 3
        assert len(result.rows) == 3

    @pytest.mark.asyncio
    async def test_not_truncated_when_exactly_max_rows(self) -> None:
        db_rows = [{"id": i} for i in range(3)]
        factory = _make_mock_biz_session_factory(db_rows)
        redis = _make_mock_redis()
        rf = _passthrough_row_filter()

        executor = SQLExecutor(factory, rf, redis, _make_settings(query_max_rows=3))
        result = await executor.execute("SELECT id FROM t", "admin", {"t"})

        assert result.truncated is False
        assert result.total_rows == 3

    @pytest.mark.asyncio
    async def test_truncated_rows_are_first_n(self) -> None:
        db_rows = [{"id": i} for i in range(6)]  # max=5, DB returns 6
        factory = _make_mock_biz_session_factory(db_rows)
        redis = _make_mock_redis()
        rf = _passthrough_row_filter()

        executor = SQLExecutor(factory, rf, redis, _make_settings(query_max_rows=5))
        result = await executor.execute("SELECT id FROM t", "admin", {"t"})

        assert result.rows == [{"id": i} for i in range(5)]


class TestSQLExecutorTimeout:
    """查询超时抛 QueryTimeoutError。"""

    @pytest.mark.asyncio
    async def test_timeout_raises_query_timeout_error(self) -> None:
        async def _slow_query(sql: str) -> list[dict[str, Any]]:
            await asyncio.sleep(10)  # 模拟慢查询
            return []

        factory = _make_mock_biz_session_factory([])
        redis = _make_mock_redis()
        rf = _passthrough_row_filter()

        executor = SQLExecutor(factory, rf, redis, _make_settings(query_timeout_seconds=1))
        # 替换 _run_query 为慢查询
        executor._run_query = _slow_query  # type: ignore[method-assign]

        with pytest.raises(QueryTimeoutError):
            await executor.execute("SELECT 1", "admin", set())

    @pytest.mark.asyncio
    async def test_timeout_zero_raises_immediately(self) -> None:
        async def _slow_query(sql: str) -> list[dict[str, Any]]:
            await asyncio.sleep(5)
            return []

        factory = _make_mock_biz_session_factory([])
        redis = _make_mock_redis()
        rf = _passthrough_row_filter()

        executor = SQLExecutor(factory, rf, redis, _make_settings(query_timeout_seconds=0))
        executor._run_query = _slow_query  # type: ignore[method-assign]

        with pytest.raises(QueryTimeoutError):
            await executor.execute("SELECT 1", "admin", set())


class TestSQLExecutorPermission:
    """row_filter 权限校验失败时抛 DataPermissionError。"""

    @pytest.mark.asyncio
    async def test_permission_denied_raises_data_permission_error(self) -> None:
        factory = _make_mock_biz_session_factory([])
        redis = _make_mock_redis()
        rf = AsyncMock()
        rf.inject_permission.side_effect = DataPermissionError("无权限")

        executor = SQLExecutor(factory, rf, redis, _make_settings())

        with pytest.raises(DataPermissionError):
            await executor.execute(
                "SELECT * FROM finance_table", "viewer", {"finance_table"}
            )

    @pytest.mark.asyncio
    async def test_permission_denied_does_not_execute_sql(self) -> None:
        factory = _make_mock_biz_session_factory([])
        redis = _make_mock_redis()
        rf = AsyncMock()
        rf.inject_permission.side_effect = DataPermissionError("无权限")

        executor = SQLExecutor(factory, rf, redis, _make_settings())

        with pytest.raises(DataPermissionError):
            await executor.execute("SELECT 1", "viewer", {"secret_table"})

        factory.assert_not_called()


class TestSQLExecutorCacheWrite:
    """执行成功后写入 Redis 缓存，验证 set 被调用且 key 格式正确。"""

    @pytest.mark.asyncio
    async def test_redis_set_called_after_success(self) -> None:
        factory = _make_mock_biz_session_factory([{"v": 1}])
        redis = _make_mock_redis()
        rf = _passthrough_row_filter()

        settings = _make_settings(query_result_cache_ttl=300)
        executor = SQLExecutor(factory, rf, redis, settings)
        await executor.execute("SELECT v FROM t", "admin", {"t"})

        redis.set.assert_awaited_once()
        call_args = redis.set.call_args
        key = call_args.args[0] if call_args.args else call_args.kwargs.get("name") or call_args.args[0]
        # key 应以 query_cache: 开头
        assert key.startswith("query_cache:")
        # ex 参数应为 TTL
        assert call_args.kwargs.get("ex") == 300 or (
            len(call_args.args) >= 3 and call_args.args[2] == 300
        )

    @pytest.mark.asyncio
    async def test_cache_key_matches_expected_format(self) -> None:
        sql = "SELECT v FROM t"
        user_role = "admin"
        expected_key = _make_cache_key(sql, user_role)

        factory = _make_mock_biz_session_factory([{"v": 1}])
        redis = _make_mock_redis()
        rf = _passthrough_row_filter()

        executor = SQLExecutor(factory, rf, redis, _make_settings())
        await executor.execute(sql, user_role, {"t"})

        set_key = redis.set.call_args.args[0]
        assert set_key == expected_key

    @pytest.mark.asyncio
    async def test_redis_set_not_called_on_cache_hit(self) -> None:
        cached_data = json.dumps(
            {"rows": [], "columns": [], "total_rows": 0, "truncated": False, "execution_ms": 1}
        )
        redis = _make_mock_redis(cached_value=cached_data)
        factory = _make_mock_biz_session_factory([])
        rf = _passthrough_row_filter()

        executor = SQLExecutor(factory, rf, redis, _make_settings())
        await executor.execute("SELECT 1", "admin", set())

        redis.set.assert_not_awaited()


class TestSQLExecutorLimitInjection:
    """验证 LIMIT 被正确注入到 SQL 末尾。"""

    @pytest.mark.asyncio
    async def test_limit_injected_into_sql(self) -> None:
        """验证传递给 DB 的 SQL 包含 LIMIT 子句。"""
        captured_sql: list[str] = []

        class FakeRow:
            _mapping = {"x": 1}

        class FakeResult:
            def fetchall(self) -> list[FakeRow]:
                return [FakeRow()]

        mock_session = AsyncMock()

        async def capture_execute(stmt: Any) -> FakeResult:
            captured_sql.append(str(stmt))
            return FakeResult()

        mock_session.execute.side_effect = capture_execute

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        factory = MagicMock(return_value=mock_ctx)

        redis = _make_mock_redis()
        rf = _passthrough_row_filter()

        executor = SQLExecutor(factory, rf, redis, _make_settings(query_max_rows=5))
        await executor.execute("SELECT x FROM t", "admin", {"t"})

        assert len(captured_sql) == 1
        assert "LIMIT 6" in captured_sql[0]

    @pytest.mark.asyncio
    async def test_trailing_semicolon_removed_before_limit(self) -> None:
        captured_sql: list[str] = []

        class FakeRow:
            _mapping = {"x": 1}

        class FakeResult:
            def fetchall(self) -> list[FakeRow]:
                return [FakeRow()]

        mock_session = AsyncMock()

        async def capture_execute(stmt: Any) -> FakeResult:
            captured_sql.append(str(stmt))
            return FakeResult()

        mock_session.execute.side_effect = capture_execute
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        factory = MagicMock(return_value=mock_ctx)

        redis = _make_mock_redis()
        rf = _passthrough_row_filter()
        executor = SQLExecutor(factory, rf, redis, _make_settings(query_max_rows=5))

        await executor.execute("SELECT x FROM t;", "admin", {"t"})
        assert ";  LIMIT" not in captured_sql[0]
        assert "LIMIT 6" in captured_sql[0]
