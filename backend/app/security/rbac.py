"""
基于角色的访问控制（RBAC）

定义角色、数据域映射及权限检查逻辑。
"""
from __future__ import annotations

from loguru import logger

from app.utils.exceptions import AuthorizationError, DataPermissionError

# 所有合法角色
ROLES: list[str] = [
    "admin",
    "data_manager",
    "analyst",
    "finance_user",
    "sales_user",
    "production_user",
    "procurement_user",
]

# 数据域 → 有权访问的角色集合
DOMAIN_ROLE_MAP: dict[str, set[str]] = {
    "finance": {"admin", "data_manager", "analyst", "finance_user"},
    "sales": {"admin", "data_manager", "analyst", "sales_user"},
    "production": {"admin", "data_manager", "analyst", "production_user"},
    "procurement": {"admin", "data_manager", "analyst", "procurement_user"},
}

# 有数据上传/管理权限的角色
_UPLOAD_ROLES: frozenset[str] = frozenset({"admin", "data_manager"})

# 有用户管理权限的角色
_USER_MGMT_ROLES: frozenset[str] = frozenset({"admin"})


class RBACChecker:
    """基于角色的权限校验器"""

    def check_domain_access(self, role: str, domain: str) -> None:
        """
        检查 role 是否有权访问 domain。

        Args:
            role: 用户角色
            domain: 数据域名称

        Raises:
            AuthorizationError: 未知角色
            DataPermissionError: 角色无权访问该域
        """
        self._assert_known_role(role)

        allowed_roles = DOMAIN_ROLE_MAP.get(domain)
        if allowed_roles is None:
            # 未知域，拒绝访问
            logger.warning("未知数据域访问请求", role=role, domain=domain)
            raise DataPermissionError(f"未知的数据域：{domain}")

        if role not in allowed_roles:
            logger.warning(
                "角色无权访问数据域",
                role=role,
                domain=domain,
                allowed_roles=allowed_roles,
            )
            raise DataPermissionError(
                f"角色 {role!r} 无权访问数据域 {domain!r}"
            )

        logger.debug("域访问权限通过", role=role, domain=domain)

    def get_allowed_domains(self, role: str) -> set[str]:
        """
        返回 role 可访问的数据域集合。

        Args:
            role: 用户角色

        Returns:
            可访问的数据域名称集合

        Raises:
            AuthorizationError: 未知角色
        """
        self._assert_known_role(role)
        return {
            domain
            for domain, roles in DOMAIN_ROLE_MAP.items()
            if role in roles
        }

    def check_can_upload(self, role: str) -> None:
        """
        检查是否有数据上传权限。

        Args:
            role: 用户角色

        Raises:
            AuthorizationError: 未知角色或无上传权限
        """
        self._assert_known_role(role)
        if role not in _UPLOAD_ROLES:
            logger.warning("角色无数据上传权限", role=role)
            raise AuthorizationError(f"角色 {role!r} 无数据上传权限")
        logger.debug("上传权限通过", role=role)

    def check_can_manage_users(self, role: str) -> None:
        """
        检查是否有用户管理权限。

        Args:
            role: 用户角色

        Raises:
            AuthorizationError: 未知角色或无用户管理权限
        """
        self._assert_known_role(role)
        if role not in _USER_MGMT_ROLES:
            logger.warning("角色无用户管理权限", role=role)
            raise AuthorizationError(f"角色 {role!r} 无用户管理权限")
        logger.debug("用户管理权限通过", role=role)

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _assert_known_role(self, role: str) -> None:
        """断言角色在已知角色列表中，否则抛 AuthorizationError"""
        if role not in ROLES:
            logger.warning("未知角色", role=role)
            raise AuthorizationError(f"未知角色：{role!r}")
