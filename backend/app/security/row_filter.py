"""
行级权限过滤器

通过表名前缀推断数据域，结合 RBAC 校验用户是否有权访问
SQL 中涉及的所有表。校验通过后原样返回 SQL（不做 SQL 改写，
依靠表名白名单作为最终防线）。
"""
from __future__ import annotations

from loguru import logger

from app.security.rbac import RBACChecker
from app.utils.exceptions import DataPermissionError

# 表名前缀 → 数据域映射
_PREFIX_DOMAIN_MAP: dict[str, str] = {
    "finance_": "finance",
    "sales_": "sales",
    "production_": "production",
    "procurement_": "procurement",
}


class RowLevelFilter:
    """行级权限过滤器"""

    def __init__(self, rbac: RBACChecker) -> None:
        self._rbac = rbac

    async def inject_permission(
        self,
        sql: str,
        user_role: str,
        allowed_tables: set[str],
    ) -> str:
        """
        校验 SQL 中引用的所有表是否在用户有权访问的范围内。

        步骤：
          1. 遍历 allowed_tables 中 SQL 实际涉及的表
          2. 从表名前缀推断域
          3. 调用 rbac.check_domain_access(role, domain)
          4. 校验通过原样返回 sql
          5. 校验失败抛 DataPermissionError

        注意：无法识别域的表名由上游表名白名单兜底，此处放行。

        Args:
            sql: 已通过 SQLValidator 校验的 SQL 字符串
            user_role: 当前用户角色
            allowed_tables: 本次查询涉及的表名集合（来自 SQLValidator.extract_tables）

        Returns:
            原始 sql（不做改写）

        Raises:
            DataPermissionError: 用户无权访问某个数据域
        """
        for table_name in allowed_tables:
            domain = self.get_table_domain(table_name)
            if domain is None:
                # 无法识别域，由白名单兜底，跳过域权限检查
                logger.debug(
                    "表名无法识别域，跳过域权限检查",
                    table=table_name,
                    role=user_role,
                )
                continue

            try:
                self._rbac.check_domain_access(user_role, domain)
            except DataPermissionError:
                logger.warning(
                    "行级权限校验失败",
                    table=table_name,
                    domain=domain,
                    role=user_role,
                )
                raise DataPermissionError(
                    f"角色 {user_role!r} 无权访问表 {table_name!r}（域：{domain}）"
                )

        logger.debug(
            "行级权限校验通过",
            role=user_role,
            tables=list(allowed_tables),
        )
        return sql

    def get_table_domain(self, table_name: str) -> str | None:
        """
        从表名前缀推断数据域。

        Args:
            table_name: 表名（大小写不敏感）

        Returns:
            域名称，如 "finance" / "sales" / "production" / "procurement"；
            无法识别时返回 None。
        """
        lower = table_name.lower()
        for prefix, domain in _PREFIX_DOMAIN_MAP.items():
            if lower.startswith(prefix):
                return domain
        return None
