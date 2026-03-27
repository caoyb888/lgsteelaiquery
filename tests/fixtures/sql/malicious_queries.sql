-- 恶意/危险 SQL 样本（用于 sql_validator 安全测试，所有语句必须被拦截）

DROP TABLE users;
TRUNCATE TABLE sales_a3f2b1c0;
DELETE FROM finance_8d7e6f5a;
UPDATE sales_a3f2b1c0 SET revenue = 0;
INSERT INTO sales_a3f2b1c0 VALUES (1, 'test');
CREATE TABLE evil AS SELECT 1;
ALTER TABLE users ADD COLUMN x INT;
EXEC xp_cmdshell('rm -rf /');
SELECT 1; DROP TABLE users;
SELECT 1 -- comment
DROP TABLE t;
SELECT * INTO OUTFILE '/etc/passwd';
SELECT LOAD_FILE('/etc/passwd');
SELECT * FROM secret_salary_table;
SELECT * FROM information_schema.tables;
SELECT * FROM pg_catalog.pg_tables;
