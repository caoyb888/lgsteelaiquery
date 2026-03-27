"""
单元测试：app/security/sql_validator.py

覆盖率目标：100%
"""
from __future__ import annotations

import pytest

from app.security.sql_validator import SQLValidator
from app.utils.exceptions import SQLSafetyViolationError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def validator() -> SQLValidator:
    return SQLValidator()


@pytest.fixture()
def allowed_tables() -> set[str]:
    return {
        "sales_a3f2b1c0",
        "finance_8d7e6f5a",
        "production_b2c3d4e5",
        "procurement_c3d4e5f6",
    }


# ---------------------------------------------------------------------------
# Layer 1 — 正则黑名单
# ---------------------------------------------------------------------------

class TestLayer1RegexBlacklist:
    """每条 FORBIDDEN_PATTERNS 至少一个命中测试用例"""

    def test_drop(self, validator: SQLValidator, allowed_tables: set[str]) -> None:
        with pytest.raises(SQLSafetyViolationError):
            validator.validate("DROP TABLE users", allowed_tables)

    def test_truncate(self, validator: SQLValidator, allowed_tables: set[str]) -> None:
        with pytest.raises(SQLSafetyViolationError):
            validator.validate("TRUNCATE TABLE sales_a3f2b1c0", allowed_tables)

    def test_delete(self, validator: SQLValidator, allowed_tables: set[str]) -> None:
        with pytest.raises(SQLSafetyViolationError):
            validator.validate("DELETE FROM finance_8d7e6f5a", allowed_tables)

    def test_update(self, validator: SQLValidator, allowed_tables: set[str]) -> None:
        with pytest.raises(SQLSafetyViolationError):
            validator.validate(
                "UPDATE sales_a3f2b1c0 SET revenue = 0", allowed_tables
            )

    def test_insert(self, validator: SQLValidator, allowed_tables: set[str]) -> None:
        with pytest.raises(SQLSafetyViolationError):
            validator.validate(
                "INSERT INTO sales_a3f2b1c0 VALUES (1, 'test')", allowed_tables
            )

    def test_create(self, validator: SQLValidator, allowed_tables: set[str]) -> None:
        with pytest.raises(SQLSafetyViolationError):
            validator.validate("CREATE TABLE evil AS SELECT 1", allowed_tables)

    def test_alter(self, validator: SQLValidator, allowed_tables: set[str]) -> None:
        with pytest.raises(SQLSafetyViolationError):
            validator.validate(
                "ALTER TABLE users ADD COLUMN x INT", allowed_tables
            )

    def test_exec(self, validator: SQLValidator, allowed_tables: set[str]) -> None:
        with pytest.raises(SQLSafetyViolationError):
            validator.validate("EXEC xp_cmdshell('rm -rf /')", allowed_tables)

    def test_execute(self, validator: SQLValidator, allowed_tables: set[str]) -> None:
        with pytest.raises(SQLSafetyViolationError):
            validator.validate("EXECUTE sp_some_proc", allowed_tables)

    def test_multi_statement_semicolon(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        with pytest.raises(SQLSafetyViolationError):
            validator.validate("SELECT 1; DROP TABLE users", allowed_tables)

    def test_line_comment(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        with pytest.raises(SQLSafetyViolationError):
            validator.validate("SELECT 1 -- comment", allowed_tables)

    def test_block_comment(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        with pytest.raises(SQLSafetyViolationError):
            validator.validate("SELECT /* evil */ 1", allowed_tables)

    def test_into_outfile(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        with pytest.raises(SQLSafetyViolationError):
            validator.validate(
                "SELECT * INTO OUTFILE '/etc/passwd'", allowed_tables
            )

    def test_load_file(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        with pytest.raises(SQLSafetyViolationError):
            validator.validate(
                "SELECT LOAD_FILE('/etc/passwd')", allowed_tables
            )

    def test_case_insensitive_drop(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        """黑名单检查大小写不敏感"""
        with pytest.raises(SQLSafetyViolationError):
            validator.validate("drop table users", allowed_tables)


# ---------------------------------------------------------------------------
# Layer 2 — AST 校验
# ---------------------------------------------------------------------------

class TestLayer2ASTValidation:
    def test_non_select_update_blocked(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        """UPDATE 语句（如能绕过 Layer1）应被 AST 层拦截——此处用自定义绕过方式测试"""
        # 直接调用 _layer2_ast 来验证非 SELECT 语句被拒绝
        import sqlglot
        stmt = sqlglot.parse_one("UPDATE t SET x=1", dialect="postgres")
        with pytest.raises(SQLSafetyViolationError, match="必须为 SELECT"):
            validator._layer2_ast("UPDATE t SET x=1")  # noqa: SLF001

    def test_syntax_error_blocked(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        """无法解析的 SQL 应被拦截"""
        with pytest.raises(SQLSafetyViolationError):
            validator._layer2_ast("THIS IS NOT SQL AT ALL !!!")  # noqa: SLF001

    def test_cte_with_select_passes_layer2(
        self, validator: SQLValidator
    ) -> None:
        """CTE（WITH ... SELECT）应通过 Layer2"""
        sql = "WITH cte AS (SELECT 1) SELECT * FROM cte"
        stmt = validator._layer2_ast(sql)  # noqa: SLF001
        assert stmt is not None

    def test_simple_select_passes_layer2(self, validator: SQLValidator) -> None:
        sql = "SELECT 1"
        stmt = validator._layer2_ast(sql)  # noqa: SLF001
        assert stmt is not None


# ---------------------------------------------------------------------------
# Layer 3 — 表名白名单
# ---------------------------------------------------------------------------

class TestLayer3TableWhitelist:
    def test_allowed_table_passes(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        """白名单内的表通过"""
        validator.validate(
            "SELECT * FROM sales_a3f2b1c0", allowed_tables
        )

    def test_forbidden_table_blocked(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        """不在白名单中的表被拦截"""
        with pytest.raises(SQLSafetyViolationError, match="未授权的表访问"):
            validator.validate(
                "SELECT * FROM secret_salary_table", allowed_tables
            )

    def test_information_schema_blocked(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        """information_schema 必须被拦截"""
        with pytest.raises(SQLSafetyViolationError):
            validator.validate(
                "SELECT * FROM information_schema.tables", allowed_tables
            )

    def test_pg_catalog_blocked(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        """pg_catalog 必须被拦截"""
        with pytest.raises(SQLSafetyViolationError):
            validator.validate(
                "SELECT * FROM pg_catalog.pg_tables", allowed_tables
            )


# ---------------------------------------------------------------------------
# 合法 SELECT 语句（端到端 validate 通过）
# ---------------------------------------------------------------------------

class TestValidSelectStatements:
    def test_simple_select(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        validator.validate("SELECT * FROM sales_a3f2b1c0", allowed_tables)

    def test_select_with_where_and_order(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        sql = (
            "SELECT * FROM sales_a3f2b1c0 "
            "WHERE report_month >= '2026-01-01' "
            "ORDER BY revenue DESC LIMIT 10"
        )
        validator.validate(sql, allowed_tables)

    def test_select_with_group_by(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        sql = (
            "SELECT product_line, SUM(revenue) AS total_revenue "
            "FROM sales_a3f2b1c0 GROUP BY product_line"
        )
        validator.validate(sql, allowed_tables)

    def test_select_with_join(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        sql = (
            "SELECT a.product_name, b.cost "
            "FROM sales_a3f2b1c0 a "
            "JOIN finance_8d7e6f5a b ON a.product_code = b.product_code"
        )
        validator.validate(sql, allowed_tables)

    def test_select_with_cte(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        sql = (
            "WITH cte AS (SELECT * FROM sales_a3f2b1c0) "
            "SELECT * FROM cte"
        )
        validator.validate(sql, allowed_tables)

    def test_select_with_subquery(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        sql = (
            "SELECT * FROM ("
            "  SELECT product_line, SUM(revenue) AS rev "
            "  FROM sales_a3f2b1c0 GROUP BY product_line"
            ") sub WHERE rev > 0"
        )
        validator.validate(sql, allowed_tables)

    def test_select_count(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        validator.validate(
            "SELECT COUNT(*) AS total FROM production_b2c3d4e5", allowed_tables
        )

    def test_select_aggregate_multi_column(
        self, validator: SQLValidator, allowed_tables: set[str]
    ) -> None:
        sql = (
            "SELECT report_month, "
            "SUM(revenue) AS monthly_revenue, "
            "SUM(cost) AS monthly_cost, "
            "SUM(revenue) - SUM(cost) AS gross_profit "
            "FROM finance_8d7e6f5a "
            "GROUP BY report_month ORDER BY report_month DESC"
        )
        validator.validate(sql, allowed_tables)


# ---------------------------------------------------------------------------
# extract_tables()
# ---------------------------------------------------------------------------

class TestExtractTables:
    def test_single_table(self, validator: SQLValidator) -> None:
        result = validator.extract_tables("SELECT * FROM sales_a3f2b1c0")
        assert "sales_a3f2b1c0" in result

    def test_join_tables(self, validator: SQLValidator) -> None:
        sql = (
            "SELECT a.x, b.y FROM sales_a3f2b1c0 a "
            "JOIN finance_8d7e6f5a b ON a.id = b.id"
        )
        result = validator.extract_tables(sql)
        assert "sales_a3f2b1c0" in result
        assert "finance_8d7e6f5a" in result

    def test_cte_tables(self, validator: SQLValidator) -> None:
        sql = (
            "WITH cte AS (SELECT * FROM sales_a3f2b1c0) "
            "SELECT * FROM cte"
        )
        result = validator.extract_tables(sql)
        assert "sales_a3f2b1c0" in result

    def test_syntax_error_returns_empty_set(
        self, validator: SQLValidator
    ) -> None:
        result = validator.extract_tables("NOT VALID SQL !!!")
        assert result == set()

    def test_empty_sql_returns_empty_set(self, validator: SQLValidator) -> None:
        # sqlglot.parse_one("") returns None → 触发 line 73 分支
        result = validator.extract_tables("")
        assert result == set()


# ---------------------------------------------------------------------------
# _layer2_ast 边界情况
# ---------------------------------------------------------------------------

class TestLayer2EdgeCases:
    def test_empty_sql_raises_in_layer2(self, validator: SQLValidator) -> None:
        """空 SQL 无法解析，应抛 SQLSafetyViolationError"""
        with pytest.raises(SQLSafetyViolationError, match="语法错误或无法解析"):
            validator._layer2_ast("")  # noqa: SLF001

    def test_cte_select_returns_select_node(self, validator: SQLValidator) -> None:
        """
        sqlglot 25+ 中 CTE 解析为顶层 Select 节点（CTE 定义存放于 with_ 属性）；
        _layer2_ast 应识别为合法 SELECT 并返回。
        """
        import sqlglot.expressions as exp

        sql = "WITH cte AS (SELECT 1) SELECT * FROM cte"
        stmt = validator._layer2_ast(sql)  # noqa: SLF001
        # sqlglot 25+ CTE → 顶层为 Select
        assert isinstance(stmt, exp.Select)
