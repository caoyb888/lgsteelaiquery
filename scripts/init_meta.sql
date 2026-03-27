-- 元数据库初始化 SQL（由 Docker 容器首次启动时执行）
-- 注意：Alembic 管理表结构变更，此脚本仅用于容器首次初始化时创建扩展
-- 正式表结构通过 alembic upgrade head 创建

-- 启用 UUID 扩展
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 授权业务数据库只读账号（容器启动后由管理员手动执行，此处作为提示）
-- CREATE USER lgsteel_biz_readonly WITH PASSWORD 'readonly_pass';
-- GRANT CONNECT ON DATABASE lgsteel_biz TO lgsteel_biz_readonly;
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO lgsteel_biz_readonly;
