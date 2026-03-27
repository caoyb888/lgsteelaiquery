"""
单元测试：app/security/rbac.py

覆盖率目标：100%
"""
from __future__ import annotations

import pytest

from app.security.rbac import DOMAIN_ROLE_MAP, ROLES, RBACChecker
from app.utils.exceptions import AuthorizationError, DataPermissionError

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def checker() -> RBACChecker:
    return RBACChecker()


# ---------------------------------------------------------------------------
# 辅助：所有域 / 所有角色
# ---------------------------------------------------------------------------

ALL_DOMAINS: list[str] = list(DOMAIN_ROLE_MAP.keys())
ALL_ROLES: list[str] = list(ROLES)


# ---------------------------------------------------------------------------
# check_domain_access — 权限矩阵测试
# ---------------------------------------------------------------------------

class TestCheckDomainAccess:
    """对每个角色 × 每个域的预期结果进行验证"""

    # ---- admin ----
    @pytest.mark.parametrize("domain", ALL_DOMAINS)
    def test_admin_can_access_all_domains(
        self, checker: RBACChecker, domain: str
    ) -> None:
        checker.check_domain_access("admin", domain)  # 不应抛出

    # ---- data_manager ----
    @pytest.mark.parametrize("domain", ALL_DOMAINS)
    def test_data_manager_can_access_all_domains(
        self, checker: RBACChecker, domain: str
    ) -> None:
        checker.check_domain_access("data_manager", domain)

    # ---- analyst ----
    @pytest.mark.parametrize("domain", ALL_DOMAINS)
    def test_analyst_can_access_all_domains(
        self, checker: RBACChecker, domain: str
    ) -> None:
        checker.check_domain_access("analyst", domain)

    # ---- finance_user ----
    def test_finance_user_can_access_finance(self, checker: RBACChecker) -> None:
        checker.check_domain_access("finance_user", "finance")

    @pytest.mark.parametrize("domain", ["sales", "production", "procurement"])
    def test_finance_user_cannot_access_other_domains(
        self, checker: RBACChecker, domain: str
    ) -> None:
        with pytest.raises(DataPermissionError):
            checker.check_domain_access("finance_user", domain)

    # ---- sales_user ----
    def test_sales_user_can_access_sales(self, checker: RBACChecker) -> None:
        checker.check_domain_access("sales_user", "sales")

    @pytest.mark.parametrize("domain", ["finance", "production", "procurement"])
    def test_sales_user_cannot_access_other_domains(
        self, checker: RBACChecker, domain: str
    ) -> None:
        with pytest.raises(DataPermissionError):
            checker.check_domain_access("sales_user", domain)

    # ---- production_user ----
    def test_production_user_can_access_production(
        self, checker: RBACChecker
    ) -> None:
        checker.check_domain_access("production_user", "production")

    @pytest.mark.parametrize("domain", ["finance", "sales", "procurement"])
    def test_production_user_cannot_access_other_domains(
        self, checker: RBACChecker, domain: str
    ) -> None:
        with pytest.raises(DataPermissionError):
            checker.check_domain_access("production_user", domain)

    # ---- procurement_user ----
    def test_procurement_user_can_access_procurement(
        self, checker: RBACChecker
    ) -> None:
        checker.check_domain_access("procurement_user", "procurement")

    @pytest.mark.parametrize("domain", ["finance", "sales", "production"])
    def test_procurement_user_cannot_access_other_domains(
        self, checker: RBACChecker, domain: str
    ) -> None:
        with pytest.raises(DataPermissionError):
            checker.check_domain_access("procurement_user", domain)

    # ---- 未知域 ----
    def test_unknown_domain_raises_data_permission_error(
        self, checker: RBACChecker
    ) -> None:
        with pytest.raises(DataPermissionError, match="未知的数据域"):
            checker.check_domain_access("admin", "unknown_domain")

    # ---- 未知角色 ----
    def test_unknown_role_raises_authorization_error(
        self, checker: RBACChecker
    ) -> None:
        with pytest.raises(AuthorizationError, match="未知角色"):
            checker.check_domain_access("hacker_role", "finance")


# ---------------------------------------------------------------------------
# get_allowed_domains
# ---------------------------------------------------------------------------

class TestGetAllowedDomains:
    def test_admin_gets_all_domains(self, checker: RBACChecker) -> None:
        result = checker.get_allowed_domains("admin")
        assert result == set(DOMAIN_ROLE_MAP.keys())

    def test_data_manager_gets_all_domains(self, checker: RBACChecker) -> None:
        result = checker.get_allowed_domains("data_manager")
        assert result == set(DOMAIN_ROLE_MAP.keys())

    def test_analyst_gets_all_domains(self, checker: RBACChecker) -> None:
        result = checker.get_allowed_domains("analyst")
        assert result == set(DOMAIN_ROLE_MAP.keys())

    def test_finance_user_gets_only_finance(self, checker: RBACChecker) -> None:
        result = checker.get_allowed_domains("finance_user")
        assert result == {"finance"}

    def test_sales_user_gets_only_sales(self, checker: RBACChecker) -> None:
        result = checker.get_allowed_domains("sales_user")
        assert result == {"sales"}

    def test_production_user_gets_only_production(
        self, checker: RBACChecker
    ) -> None:
        result = checker.get_allowed_domains("production_user")
        assert result == {"production"}

    def test_procurement_user_gets_only_procurement(
        self, checker: RBACChecker
    ) -> None:
        result = checker.get_allowed_domains("procurement_user")
        assert result == {"procurement"}

    def test_unknown_role_raises_authorization_error(
        self, checker: RBACChecker
    ) -> None:
        with pytest.raises(AuthorizationError):
            checker.get_allowed_domains("ghost")


# ---------------------------------------------------------------------------
# check_can_upload
# ---------------------------------------------------------------------------

class TestCheckCanUpload:
    @pytest.mark.parametrize("role", ["admin", "data_manager"])
    def test_upload_allowed_roles(
        self, checker: RBACChecker, role: str
    ) -> None:
        checker.check_can_upload(role)  # 不应抛出

    @pytest.mark.parametrize(
        "role",
        ["analyst", "finance_user", "sales_user", "production_user", "procurement_user"],
    )
    def test_upload_forbidden_roles(
        self, checker: RBACChecker, role: str
    ) -> None:
        with pytest.raises(AuthorizationError):
            checker.check_can_upload(role)

    def test_upload_unknown_role(self, checker: RBACChecker) -> None:
        with pytest.raises(AuthorizationError):
            checker.check_can_upload("unknown_role")


# ---------------------------------------------------------------------------
# check_can_manage_users
# ---------------------------------------------------------------------------

class TestCheckCanManageUsers:
    def test_admin_can_manage_users(self, checker: RBACChecker) -> None:
        checker.check_can_manage_users("admin")  # 不应抛出

    @pytest.mark.parametrize(
        "role",
        [
            "data_manager",
            "analyst",
            "finance_user",
            "sales_user",
            "production_user",
            "procurement_user",
        ],
    )
    def test_non_admin_cannot_manage_users(
        self, checker: RBACChecker, role: str
    ) -> None:
        with pytest.raises(AuthorizationError):
            checker.check_can_manage_users(role)

    def test_manage_users_unknown_role(self, checker: RBACChecker) -> None:
        with pytest.raises(AuthorizationError):
            checker.check_can_manage_users("unknown_role")
