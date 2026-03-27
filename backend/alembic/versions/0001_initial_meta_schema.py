"""初始元数据库表结构

Revision ID: 0001
Revises:
Create Date: 2026-03-27

创建全部元数据表：
  - users（用户）
  - datasources（Excel 数据源注册）
  - field_mappings（字段映射）
  - data_dictionary（数据字典）
  - audit_logs（查询审计日志）
  - conversation_history（对话历史持久化）
  - few_shot_examples（Few-shot 示例库）
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- 启用 pgcrypto 扩展（用于 gen_random_uuid()）----
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    # ================================================================
    # users（用户表）
    # ================================================================
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("email", sa.String(256), nullable=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    # ================================================================
    # datasources（数据源注册表）
    # ================================================================
    op.create_table(
        "datasources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("domain", sa.String(32), nullable=False),
        sa.Column("description", sa.String(512), nullable=True),
        sa.Column("original_filename", sa.String(256), nullable=False),
        sa.Column("file_path", sa.String(512), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("data_date", sa.Date(), nullable=False),
        sa.Column("update_mode", sa.String(16), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("biz_table_name", sa.String(128), nullable=True),
        sa.Column("total_rows", sa.Integer(), nullable=True),
        sa.Column(
            "uploaded_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_datasources_domain", "datasources", ["domain"])
    op.create_index("ix_datasources_data_date", "datasources", ["data_date"])

    # ================================================================
    # field_mappings（字段映射表）
    # ================================================================
    op.create_table(
        "field_mappings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "datasource_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("datasources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("raw_name", sa.String(256), nullable=False),
        sa.Column("std_name", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("field_type", sa.String(32), nullable=False),
        sa.Column("unit", sa.String(64), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("mapping_source", sa.String(32), nullable=True),
        sa.Column(
            "confirmed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # ================================================================
    # data_dictionary（数据字典）
    # ================================================================
    op.create_table(
        "data_dictionary",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("std_name", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("domain", sa.String(32), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("synonyms", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("unit", sa.String(64), nullable=True),
        sa.Column("example_values", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("embedding", postgresql.ARRAY(sa.Float()), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_data_dictionary_std_name", "data_dictionary", ["std_name"], unique=True)

    # ================================================================
    # audit_logs（查询审计日志）
    # ================================================================
    op.create_table(
        "audit_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("generated_sql", sa.Text(), nullable=True),
        sa.Column("tables_accessed", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("result_row_count", sa.Integer(), nullable=True),
        sa.Column("execution_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("block_reason", sa.Text(), nullable=True),
        sa.Column("llm_model_used", sa.String(64), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("feedback", sa.SmallInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_status", "audit_logs", ["status"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    # ================================================================
    # conversation_history（对话历史持久化）
    # ================================================================
    op.create_table(
        "conversation_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("turn_index", sa.SmallInteger(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("generated_sql", sa.Text(), nullable=True),
        sa.Column("answer_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_conversation_history_conv_id",
        "conversation_history",
        ["conversation_id", "turn_index"],
    )

    # ================================================================
    # few_shot_examples（Few-shot 示例库）
    # ================================================================
    op.create_table(
        "few_shot_examples",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("domain", sa.String(32), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("sql", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("difficulty", sa.String(16), nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_few_shot_examples_domain", "few_shot_examples", ["domain"])


def downgrade() -> None:
    op.drop_table("few_shot_examples")
    op.drop_table("conversation_history")
    op.drop_table("audit_logs")
    op.drop_table("data_dictionary")
    op.drop_table("field_mappings")
    op.drop_table("datasources")
    op.drop_table("users")
