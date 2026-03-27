"""
单元测试：app/security/row_filter.py

覆盖率目标：100%
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from app.security.rbac import RBACChecker
from app.security.row_filter import RowLevelFilter
from app.utils.exceptions import DataPermissionError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def rbac() -> RBACChecker:
    return RBACChecker()


@pytest.fixture()
def row_filter(rbac: RBACChecker) -> RowLevelFilter:
    return RowLevelFilter(rbac)


# ---------------------------------------------------------------------------
# get_table_domain
# ---------------------------------------------------------------------------

class TestGetTableDomain:
    def test_finance_prefix(self, row_filter: RowLevelFilter) -> None:
        assert row_filter.get_table_domain("finance_8d7e6f5a") == "finance"

    def test_sales_prefix(self, row_filter: RowLevelFilter) -> None:
        assert row_filter.get_table_domain("sales_a3f2b1c0") == "sales"

    def test_production_prefix(self, row_filter: RowLevelFilter) -> None:
        assert row_filter.get_table_domain("production_b2c3d4e5") == "production"

    def test_procurement_prefix(self, row_filter: RowLevelFilter) -> None:
        assert row_filter.get_table_domain("procurement_c3d4e5f6") == "procurement"

    def test_unknown_prefix_returns_none(self, row_filter: RowLevelFilter) -> None:
        assert row_filter.get_table_domain("unknown_table") is None

    def test_empty_string_returns_none(self, row_filter: RowLevelFilter) -> None:
        assert row_filter.get_table_domain("") is None

    def test_prefix_only_returns_none(self, row_filter: RowLevelFilter) -> None:
        """纯前缀字符串（如 "finance_"）仍然能识别域"""
        assert row_filter.get_table_domain("finance_") == "finance"

    def test_case_insensitive(self, row_filter: RowLevelFilter) -> None:
        assert row_filter.get_table_domain("SALES_Table") == "sales"

    def test_cte_alias_returns_none(self, row_filter: RowLevelFilter) -> None:
        """CTE 别名无域前缀，应返回 None"""
        assert row_filter.get_table_domain("cte") is None


# ---------------------------------------------------------------------------
# inject_permission — 有权限的情况
# ---------------------------------------------------------------------------

class TestInjectPermissionAllowed:
    @pytest.mark.asyncio
    async def test_admin_can_access_any_domain(
        self, row_filter: RowLevelFilter
    ) -> None:
        sql = "SELECT * FROM sales_a3f2b1c0"
        result = await row_filter.inject_permission(
            sql=sql,
            user_role="admin",
            allowed_tables={"sales_a3f2b1c0"},
        )
        assert result == sql

    @pytest.mark.asyncio
    async def test_analyst_can_access_all_domains(
        self, row_filter: RowLevelFilter
    ) -> None:
        sql = "SELECT * FROM finance_8d7e6f5a JOIN sales_a3f2b1c0 ON 1=1"
        result = await row_filter.inject_permission(
            sql=sql,
            user_role="analyst",
            allowed_tables={"finance_8d7e6f5a", "sales_a3f2b1c0"},
        )
        assert result == sql

    @pytest.mark.asyncio
    async def test_finance_user_can_access_finance(
        self, row_filter: RowLevelFilter
    ) -> None:
        sql = "SELECT * FROM finance_8d7e6f5a"
        result = await row_filter.inject_permission(
            sql=sql,
            user_role="finance_user",
            allowed_tables={"finance_8d7e6f5a"},
        )
        assert result == sql

    @pytest.mark.asyncio
    async def test_sales_user_can_access_sales(
        self, row_filter: RowLevelFilter
    ) -> None:
        sql = "SELECT * FROM sales_a3f2b1c0"
        result = await row_filter.inject_permission(
            sql=sql,
            user_role="sales_user",
            allowed_tables={"sales_a3f2b1c0"},
        )
        assert result == sql

    @pytest.mark.asyncio
    async def test_production_user_can_access_production(
        self, row_filter: RowLevelFilter
    ) -> None:
        sql = "SELECT * FROM production_b2c3d4e5"
        result = await row_filter.inject_permission(
            sql=sql,
            user_role="production_user",
            allowed_tables={"production_b2c3d4e5"},
        )
        assert result == sql

    @pytest.mark.asyncio
    async def test_procurement_user_can_access_procurement(
        self, row_filter: RowLevelFilter
    ) -> None:
        sql = "SELECT * FROM procurement_c3d4e5f6"
        result = await row_filter.inject_permission(
            sql=sql,
            user_role="procurement_user",
            allowed_tables={"procurement_c3d4e5f6"},
        )
        assert result == sql

    @pytest.mark.asyncio
    async def test_empty_allowed_tables_passes(
        self, row_filter: RowLevelFilter
    ) -> None:
        """空表集合（如纯 CTE 查询）应该通过"""
        sql = "SELECT 1"
        result = await row_filter.inject_permission(
            sql=sql,
            user_role="analyst",
            allowed_tables=set(),
        )
        assert result == sql


# ---------------------------------------------------------------------------
# inject_permission — 无权限（抛 DataPermissionError）
# ---------------------------------------------------------------------------

class TestInjectPermissionDenied:
    @pytest.mark.asyncio
    async def test_finance_user_blocked_from_sales(
        self, row_filter: RowLevelFilter
    ) -> None:
        with pytest.raises(DataPermissionError):
            await row_filter.inject_permission(
                sql="SELECT * FROM sales_a3f2b1c0",
                user_role="finance_user",
                allowed_tables={"sales_a3f2b1c0"},
            )

    @pytest.mark.asyncio
    async def test_sales_user_blocked_from_finance(
        self, row_filter: RowLevelFilter
    ) -> None:
        with pytest.raises(DataPermissionError):
            await row_filter.inject_permission(
                sql="SELECT * FROM finance_8d7e6f5a",
                user_role="sales_user",
                allowed_tables={"finance_8d7e6f5a"},
            )

    @pytest.mark.asyncio
    async def test_production_user_blocked_from_procurement(
        self, row_filter: RowLevelFilter
    ) -> None:
        with pytest.raises(DataPermissionError):
            await row_filter.inject_permission(
                sql="SELECT * FROM procurement_c3d4e5f6",
                user_role="production_user",
                allowed_tables={"procurement_c3d4e5f6"},
            )

    @pytest.mark.asyncio
    async def test_procurement_user_blocked_from_production(
        self, row_filter: RowLevelFilter
    ) -> None:
        with pytest.raises(DataPermissionError):
            await row_filter.inject_permission(
                sql="SELECT * FROM production_b2c3d4e5",
                user_role="procurement_user",
                allowed_tables={"production_b2c3d4e5"},
            )


# ---------------------------------------------------------------------------
# inject_permission — 无法识别域（由白名单兜底，此处放行）
# ---------------------------------------------------------------------------

class TestInjectPermissionUnknownDomain:
    @pytest.mark.asyncio
    async def test_unknown_domain_table_passes_row_filter(
        self, row_filter: RowLevelFilter
    ) -> None:
        """表名无法识别域时，行过滤器放行，由上游白名单兜底"""
        sql = "SELECT * FROM cte_result"
        result = await row_filter.inject_permission(
            sql=sql,
            user_role="analyst",
            allowed_tables={"cte_result"},
        )
        assert result == sql

    @pytest.mark.asyncio
    async def test_mixed_known_and_unknown_domain_with_permission(
        self, row_filter: RowLevelFilter
    ) -> None:
        """已知域有权限 + 未知域表名 → 通过"""
        sql = "SELECT * FROM sales_a3f2b1c0 JOIN dim_time ON 1=1"
        result = await row_filter.inject_permission(
            sql=sql,
            user_role="sales_user",
            allowed_tables={"sales_a3f2b1c0", "dim_time"},
        )
        assert result == sql

    @pytest.mark.asyncio
    async def test_mixed_known_and_unknown_domain_without_permission(
        self, row_filter: RowLevelFilter
    ) -> None:
        """已知域无权限 + 未知域表名 → 拒绝（已知域失败）"""
        with pytest.raises(DataPermissionError):
            await row_filter.inject_permission(
                sql="SELECT * FROM finance_8d7e6f5a JOIN dim_time ON 1=1",
                user_role="sales_user",
                allowed_tables={"finance_8d7e6f5a", "dim_time"},
            )
