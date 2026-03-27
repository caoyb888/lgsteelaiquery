"""
开发测试数据初始化脚本

用法：
    python scripts/seed_data.py

功能：
    - 创建初始角色用户（admin / 各域测试用户）
    - 写入 Few-shot 示例问答
    - 写入初始数据字典条目

前提：开发环境数据库已启动（./scripts/start.sh dev）
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 将 backend/ 加入 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# 导入配置（加载 .env.local 或 .env）
from app.config import get_settings

settings = get_settings()


# ---------------------------------------------------------------------------
# 测试用户数据
# ---------------------------------------------------------------------------
TEST_USERS = [
    {
        "username": "admin",
        "display_name": "系统管理员",
        "email": "admin@lgsteel.internal",
        "role": "admin",
        "password_plain": "Admin@2026!",
    },
    {
        "username": "test_analyst",
        "display_name": "测试分析师",
        "email": "analyst@lgsteel.internal",
        "role": "analyst",
        "password_plain": "Test@2026!",
    },
    {
        "username": "test_finance",
        "display_name": "测试财务用户",
        "email": "finance@lgsteel.internal",
        "role": "finance_user",
        "password_plain": "Test@2026!",
    },
    {
        "username": "test_sales",
        "display_name": "测试销售用户",
        "email": "sales@lgsteel.internal",
        "role": "sales_user",
        "password_plain": "Test@2026!",
    },
    {
        "username": "test_data_manager",
        "display_name": "测试数据维护员",
        "email": "data_manager@lgsteel.internal",
        "role": "data_manager",
        "password_plain": "Test@2026!",
    },
    {
        "username": "perf_test_user",
        "display_name": "性能测试用户",
        "email": "perf@lgsteel.internal",
        "role": "analyst",
        "password_plain": "Perf@2026!",
    },
]


# ---------------------------------------------------------------------------
# Few-shot 示例（各域各 2 条作为种子，后续由业务方补充）
# ---------------------------------------------------------------------------
FEW_SHOT_EXAMPLES = [
    # --- 财务域 ---
    {
        "domain": "finance",
        "difficulty": "easy",
        "question": "本月销售总收入是多少？",
        "sql": (
            "SELECT SUM(revenue) AS total_revenue "
            "FROM finance_monthly_revenue "
            "WHERE report_month >= DATE_TRUNC('month', CURRENT_DATE) "
            "  AND report_month <  DATE_TRUNC('month', CURRENT_DATE) + INTERVAL '1 month'"
        ),
        "description": "当月收入聚合查询",
        "source": "manual",
    },
    {
        "domain": "finance",
        "difficulty": "medium",
        "question": "今年一季度各产品线的收入分别是多少？",
        "sql": (
            "SELECT product_line, SUM(revenue) AS total_revenue "
            "FROM finance_monthly_revenue "
            "WHERE report_month >= DATE_TRUNC('year', CURRENT_DATE) "
            "  AND report_month <  DATE_TRUNC('year', CURRENT_DATE) + INTERVAL '3 months' "
            "GROUP BY product_line "
            "ORDER BY total_revenue DESC"
        ),
        "description": "一季度产品线分组聚合",
        "source": "manual",
    },
    # --- 销售域 ---
    {
        "domain": "sales",
        "difficulty": "easy",
        "question": "本月销售订单总数是多少？",
        "sql": (
            "SELECT COUNT(*) AS order_count "
            "FROM sales_orders "
            "WHERE order_date >= DATE_TRUNC('month', CURRENT_DATE)"
        ),
        "description": "当月订单计数",
        "source": "manual",
    },
    {
        "domain": "sales",
        "difficulty": "medium",
        "question": "近三个月各客户的回款金额排名前10？",
        "sql": (
            "SELECT customer_name, SUM(payment_amount) AS total_payment "
            "FROM sales_payment_records "
            "WHERE payment_date >= CURRENT_DATE - INTERVAL '3 months' "
            "GROUP BY customer_name "
            "ORDER BY total_payment DESC "
            "LIMIT 10"
        ),
        "description": "近三月客户回款排名",
        "source": "manual",
    },
    # --- 生产域 ---
    {
        "domain": "production",
        "difficulty": "easy",
        "question": "上周高炉日均产量是多少吨？",
        "sql": (
            "SELECT AVG(daily_output) AS avg_daily_output "
            "FROM production_daily_report "
            "WHERE report_date >= DATE_TRUNC('week', CURRENT_DATE) - INTERVAL '1 week' "
            "  AND report_date <  DATE_TRUNC('week', CURRENT_DATE)"
        ),
        "description": "上周高炉日均产量",
        "source": "manual",
    },
    {
        "domain": "production",
        "difficulty": "medium",
        "question": "本月各工序的产量完成率是多少？",
        "sql": (
            "SELECT process_name, "
            "       SUM(actual_output) AS actual, "
            "       SUM(planned_output) AS planned, "
            "       ROUND(SUM(actual_output) * 100.0 / NULLIF(SUM(planned_output), 0), 2) AS completion_rate "
            "FROM production_monthly_summary "
            "WHERE report_month = DATE_TRUNC('month', CURRENT_DATE) "
            "GROUP BY process_name "
            "ORDER BY completion_rate ASC"
        ),
        "description": "当月各工序产量完成率",
        "source": "manual",
    },
    # --- 采购域 ---
    {
        "domain": "procurement",
        "difficulty": "easy",
        "question": "近三个月焦炭的平均采购价格是多少？",
        "sql": (
            "SELECT AVG(unit_price) AS avg_unit_price "
            "FROM procurement_records "
            "WHERE material_name LIKE '%焦炭%' "
            "  AND purchase_date >= CURRENT_DATE - INTERVAL '3 months'"
        ),
        "description": "近三月焦炭均价",
        "source": "manual",
    },
    {
        "domain": "procurement",
        "difficulty": "medium",
        "question": "本月各供应商的采购金额占比是多少？",
        "sql": (
            "SELECT supplier_name, "
            "       SUM(total_amount) AS supplier_amount, "
            "       ROUND(SUM(total_amount) * 100.0 / SUM(SUM(total_amount)) OVER (), 2) AS percentage "
            "FROM procurement_records "
            "WHERE purchase_date >= DATE_TRUNC('month', CURRENT_DATE) "
            "GROUP BY supplier_name "
            "ORDER BY supplier_amount DESC"
        ),
        "description": "当月供应商采购占比",
        "source": "manual",
    },
]


async def seed(session: AsyncSession) -> None:
    from sqlalchemy import text

    print("开始写入种子数据...")

    # ---- 写入用户 ----
    print(f"  写入 {len(TEST_USERS)} 个测试用户...")
    for user in TEST_USERS:
        # 简单 bcrypt 哈希（生产环境用 passlib）
        import hashlib
        pw_hash = "bcrypt_placeholder_" + hashlib.sha256(
            user["password_plain"].encode()
        ).hexdigest()[:16]

        await session.execute(
            text("""
                INSERT INTO users (username, display_name, email, role, password_hash)
                VALUES (:username, :display_name, :email, :role, :password_hash)
                ON CONFLICT (username) DO NOTHING
            """),
            {
                "username": user["username"],
                "display_name": user["display_name"],
                "email": user["email"],
                "role": user["role"],
                "password_hash": pw_hash,
            },
        )

    # ---- 写入 Few-shot 示例 ----
    print(f"  写入 {len(FEW_SHOT_EXAMPLES)} 条 Few-shot 示例...")
    for example in FEW_SHOT_EXAMPLES:
        await session.execute(
            text("""
                INSERT INTO few_shot_examples (domain, difficulty, question, sql, description, source)
                VALUES (:domain, :difficulty, :question, :sql, :description, :source)
                ON CONFLICT DO NOTHING
            """),
            example,
        )

    await session.commit()
    print("✅ 种子数据写入完成。")


async def main() -> None:
    engine = create_async_engine(settings.meta_db_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)  # type: ignore[call-overload]

    async with async_session() as session:
        await seed(session)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
