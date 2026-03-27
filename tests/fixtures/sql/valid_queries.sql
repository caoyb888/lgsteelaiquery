-- 合法 SELECT 查询样本（用于 sql_validator 测试）

SELECT * FROM sales_a3f2b1c0;

SELECT product_line, SUM(revenue) AS total_revenue
FROM sales_a3f2b1c0
GROUP BY product_line;

SELECT a.product_name, b.cost
FROM sales_a3f2b1c0 a
JOIN finance_8d7e6f5a b ON a.product_code = b.product_code;

SELECT * FROM sales_a3f2b1c0
WHERE report_month >= '2026-01-01'
ORDER BY revenue DESC
LIMIT 10;

WITH cte AS (
    SELECT * FROM sales_a3f2b1c0
)
SELECT * FROM cte;

SELECT COUNT(*) AS total FROM production_b2c3d4e5;

SELECT
    report_month,
    SUM(revenue) AS monthly_revenue,
    SUM(cost) AS monthly_cost,
    SUM(revenue) - SUM(cost) AS gross_profit
FROM finance_8d7e6f5a
GROUP BY report_month
ORDER BY report_month DESC;
