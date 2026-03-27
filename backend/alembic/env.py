"""
Alembic 迁移环境配置

使用同步连接执行迁移（Alembic 不支持 async），
URL 从 Settings 中读取 meta_db_url_sync。
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 将 backend/ 加入 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

# 加载 FastAPI 应用配置
from app.config import get_settings

# 导入所有模型，确保 Alembic 能检测到表
from app.db.meta_session import MetaBase
import app.db.models  # noqa: F401（触发所有模型注册）

settings = get_settings()

# Alembic 配置对象
config = context.config

# 设置日志
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 设置目标 metadata（Alembic 自动检测表变更）
target_metadata = MetaBase.metadata

# 从 Settings 注入数据库 URL（覆盖 alembic.ini 中的占位 URL）
config.set_main_option("sqlalchemy.url", settings.meta_db_url_sync)


def run_migrations_offline() -> None:
    """在离线模式（不连接数据库）下生成迁移 SQL 脚本"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
