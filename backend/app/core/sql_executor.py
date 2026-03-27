"""
SQL 执行器

只读 SQL 执行器，强制注入 LIMIT，支持 Redis 缓存和行级权限注入。
所有数据库 IO 使用 async/await；查询超时通过 asyncio.wait_for 实现。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from sqlalchemy import text

from app.config import Settings, get_settings
from app.security.row_filter import RowLevelFilter
from app.utils.exceptions import QueryTimeoutError, SQLExecutionError


@dataclass
class QueryResult:
    """SQL 查询结果"""

    rows: list[dict[str, Any]]      # 行数据（每行 {col: value}）
    columns: list[str]              # 列名列表
    total_rows: int                 # 实际行数（截断前）
    truncated: bool                 # 是否被 LIMIT 截断
    execution_ms: int               # 执行耗时毫秒
    from_cache: bool                # 是否来自缓存


def _make_cache_key(sql: str, user_role: str) -> str:
    """生成 Redis 缓存 key：query_cache:{md5(sql+user_role)[:16]}"""
    raw = sql + user_role
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]
    return f"query_cache:{digest}"


class SQLExecutor:
    """
    只读 SQL 执行器，强制注入 LIMIT，支持 Redis 缓存和行级权限注入。

    使用方式：
        executor = SQLExecutor(biz_session_factory, row_filter, redis_client)
        result = await executor.execute(sql, user_role, allowed_tables, user_id)
    """

    def __init__(
        self,
        biz_session_factory: Any,
        row_filter: RowLevelFilter,
        redis_client: Any,
        settings: Settings | None = None,
    ) -> None:
        self._biz_session_factory = biz_session_factory
        self._row_filter = row_filter
        self._redis = redis_client
        self._settings = settings or get_settings()

    async def execute(
        self,
        sql: str,
        user_role: str,
        allowed_tables: set[str],
        user_id: str | None = None,
    ) -> QueryResult:
        """
        执行只读 SQL 查询。

        流程：
          1. 检查 Redis 缓存（key = query_cache:{md5(sql+user_role)[:16]}）
          2. 调用 row_filter.inject_permission() 校验行级权限
          3. 注入 LIMIT：末尾添加 LIMIT {query_max_rows+1}（用于判断截断）
          4. asyncio.wait_for 超时控制
          5. 执行 SQL，将结果映射为 list[dict]
          6. 若结果超过 query_max_rows，设 truncated=True，截取前 N 行
          7. 写入 Redis 缓存（TTL = query_result_cache_ttl）
          8. 返回 QueryResult

        Raises:
            DataPermissionError: 行级权限校验失败（由 row_filter 抛出）
            QueryTimeoutError: 查询超时
            SQLExecutionError: SQL 执行期间发生数据库错误
        """
        cache_key = _make_cache_key(sql, user_role)

        # 1. 检查 Redis 缓存
        cached = await self._redis.get(cache_key)
        if cached is not None:
            logger.info(
                "命中查询缓存，直接返回",
                cache_key=cache_key,
                user_role=user_role,
                user_id=user_id,
            )
            data: dict[str, Any] = json.loads(cached)
            return QueryResult(
                rows=data["rows"],
                columns=data["columns"],
                total_rows=data["total_rows"],
                truncated=data["truncated"],
                execution_ms=data["execution_ms"],
                from_cache=True,
            )

        # 2. 行级权限校验（抛 DataPermissionError 时直接上抛）
        validated_sql = await self._row_filter.inject_permission(
            sql=sql,
            user_role=user_role,
            allowed_tables=allowed_tables,
        )

        # 3. 注入 LIMIT（在 SQL 末尾；多取一行用于截断检测）
        max_rows = self._settings.query_max_rows
        fetch_limit = max_rows + 1
        # 去除末尾分号和空白，避免 LIMIT 追加后变为双语句
        trimmed_sql = validated_sql.rstrip().rstrip(";")
        limited_sql = f"{trimmed_sql} LIMIT {fetch_limit}"

        # 4 & 5. 超时控制 + 执行 SQL
        start_ms = time.monotonic()
        try:
            raw_rows = await asyncio.wait_for(
                self._run_query(limited_sql),
                timeout=float(self._settings.query_timeout_seconds),
            )
        except asyncio.TimeoutError:
            elapsed = int((time.monotonic() - start_ms) * 1000)
            logger.warning(
                "SQL 查询超时",
                elapsed_ms=elapsed,
                timeout_s=self._settings.query_timeout_seconds,
                user_role=user_role,
                user_id=user_id,
            )
            raise QueryTimeoutError()

        execution_ms = int((time.monotonic() - start_ms) * 1000)

        # 6. 截断判断
        truncated = len(raw_rows) > max_rows
        if truncated:
            raw_rows = raw_rows[:max_rows]

        total_rows = len(raw_rows)
        columns: list[str] = list(raw_rows[0].keys()) if raw_rows else []

        logger.info(
            "SQL 执行成功",
            total_rows=total_rows,
            truncated=truncated,
            execution_ms=execution_ms,
            user_role=user_role,
            user_id=user_id,
        )

        result = QueryResult(
            rows=raw_rows,
            columns=columns,
            total_rows=total_rows,
            truncated=truncated,
            execution_ms=execution_ms,
            from_cache=False,
        )

        # 7. 写入 Redis 缓存
        cache_payload = json.dumps(
            {
                "rows": result.rows,
                "columns": result.columns,
                "total_rows": result.total_rows,
                "truncated": result.truncated,
                "execution_ms": result.execution_ms,
            },
            ensure_ascii=False,
            default=str,  # 兜底：日期等不可序列化类型转为字符串
        )
        await self._redis.set(
            cache_key,
            cache_payload,
            ex=self._settings.query_result_cache_ttl,
        )
        logger.debug(
            "查询结果已写入缓存",
            cache_key=cache_key,
            ttl=self._settings.query_result_cache_ttl,
        )

        return result

    async def _run_query(self, sql: str) -> list[dict[str, Any]]:
        """在业务数据库 session 中执行 SQL，返回行列表。"""
        try:
            async with self._biz_session_factory() as session:
                result = await session.execute(text(sql))
                # MappingResult → list[dict]
                rows = [dict(row._mapping) for row in result.fetchall()]
                return rows
        except Exception as exc:
            logger.error("SQL 执行失败", error=str(exc))
            raise SQLExecutionError(f"SQL 执行失败：{exc}") from exc
