"""
SQL 安全校验器

三层校验机制：
  Layer 1 - 正则黑名单：拦截危险关键字和注入模式
  Layer 2 - sqlglot AST 校验：确保顶层语句为 SELECT
  Layer 3 - 表名白名单：只允许访问授权表，拦截系统表
"""
from __future__ import annotations

import re

import sqlglot
import sqlglot.expressions as exp
from loguru import logger

from app.utils.exceptions import SQLSafetyViolationError

# 正则黑名单模式（来自 CLAUDE.md §6.2）
FORBIDDEN_PATTERNS: list[str] = [
    r"\bDROP\b",
    r"\bTRUNCATE\b",
    r"\bDELETE\b",
    r"\bUPDATE\b",
    r"\bINSERT\b",
    r"\bCREATE\b",
    r"\bALTER\b",
    r"\bEXEC(?:UTE)?\b",
    r";\s*\w+",           # 多语句：分号后跟单词
    r"--",                # 行注释
    r"/\*.*?\*/",         # 块注释（dotall）
    r"\bINTO\s+OUTFILE\b",
    r"\bLOAD_FILE\b",
]

# 预编译黑名单正则（忽略大小写，DOTALL 用于块注释）
_COMPILED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE | re.DOTALL) for p in FORBIDDEN_PATTERNS
]

# 禁止访问的系统 schema
_FORBIDDEN_SCHEMAS: frozenset[str] = frozenset(
    {"information_schema", "pg_catalog", "pg_toast", "pg_temp"}
)


class SQLValidator:
    """三层 SQL 安全校验器"""

    def validate(self, sql: str, allowed_tables: set[str]) -> None:
        """
        三层校验，任意一层失败抛 SQLSafetyViolationError。

        Args:
            sql: 待校验的 SQL 字符串
            allowed_tables: 允许访问的表名集合（小写）
        """
        self._layer1_regex(sql)
        statement = self._layer2_ast(sql)
        self._layer3_whitelist(statement, allowed_tables)

    def extract_tables(self, sql: str) -> set[str]:
        """
        提取 SQL 中涉及的所有表名（用于审计）。

        解析失败时返回空集合。
        """
        try:
            statement = sqlglot.parse_one(sql, dialect="postgres")
        except sqlglot.errors.ParseError:
            return set()
        return self._collect_table_names(statement)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _layer1_regex(self, sql: str) -> None:
        """Layer 1：正则黑名单扫描"""
        for pattern in _COMPILED_PATTERNS:
            if pattern.search(sql):
                logger.warning(
                    "SQL 正则黑名单命中",
                    pattern=pattern.pattern,
                    sql_snippet=sql[:200],
                )
                raise SQLSafetyViolationError(
                    f"SQL 包含禁止的模式：{pattern.pattern}"
                )

    def _layer2_ast(self, sql: str) -> exp.Expression:
        """
        Layer 2：sqlglot AST 校验。

        顶层语句必须是 SELECT（包含带 CTE 的 SELECT）。
        解析失败（包括空 SQL）视为非法。

        注意：sqlglot 25+ 中 CTE 查询解析为顶层 Select 节点，
        CTE 定义存放在 Select.args["with"]，不再是独立的 With 节点。
        """
        try:
            statement = sqlglot.parse_one(sql, dialect="postgres")
        except sqlglot.errors.ParseError as exc:
            logger.warning("SQL 解析失败", error=str(exc), sql_snippet=sql[:200])
            raise SQLSafetyViolationError(f"SQL 语法错误或无法解析：{exc}") from exc

        if not isinstance(statement, exp.Select):
            logger.warning(
                "SQL 顶层语句非 SELECT",
                stmt_type=type(statement).__name__,
                sql_snippet=sql[:200],
            )
            raise SQLSafetyViolationError(
                f"SQL 顶层语句必须为 SELECT，实际类型：{type(statement).__name__}"
            )

        return statement

    def _layer3_whitelist(
        self, statement: exp.Expression, allowed_tables: set[str]
    ) -> None:
        """Layer 3：表名白名单校验，同时拦截系统表"""
        table_names = self._collect_table_names(statement)
        allowed_lower = {t.lower() for t in allowed_tables}

        # 优先检查系统 schema（保证检测路径在白名单检查之前执行）
        for table_name in sorted(table_names):
            name_lower = table_name.lower()
            if name_lower in _FORBIDDEN_SCHEMAS:
                logger.warning(
                    "SQL 访问禁止的系统 schema",
                    table=table_name,
                )
                raise SQLSafetyViolationError(
                    f"禁止访问系统表：{table_name}"
                )

        # 再检查表名白名单
        for table_name in table_names:
            name_lower = table_name.lower()
            if name_lower not in allowed_lower:
                logger.warning(
                    "SQL 引用了不在白名单中的表",
                    table=table_name,
                    allowed=allowed_lower,
                )
                raise SQLSafetyViolationError(
                    f"未授权的表访问：{table_name}"
                )

    def _collect_table_names(self, statement: exp.Expression) -> set[str]:
        """
        从 AST 中递归提取所有真实表名（schema 前缀单独提取）。

        CTE 别名（WITH cte AS (...)）作为局部定义，不计入结果，
        避免将 CTE 名称误判为未授权表。
        """
        # 收集 CTE 别名（局部定义名，不是真实表）
        cte_aliases: set[str] = set()
        for node in statement.walk():
            if isinstance(node, exp.CTE):
                alias = node.alias
                if alias:
                    cte_aliases.add(alias.lower())

        result: set[str] = set()
        for node in statement.walk():
            if isinstance(node, exp.Table):
                db = node.args.get("db")
                name = node.name
                if db:
                    # 将 schema 本身记录为表名（用于系统 schema 检测）
                    schema_name = (
                        db.name if isinstance(db, exp.Identifier) else str(db)
                    )
                    result.add(schema_name)
                if name and name.lower() not in cte_aliases:
                    result.add(name)
        return result
