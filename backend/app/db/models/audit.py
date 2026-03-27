"""审计日志 ORM 模型"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, SmallInteger, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, INET, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.meta_session import MetaBase


class AuditLog(MetaBase):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    generated_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    tables_accessed: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    result_row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    execution_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # success/failed/blocked
    block_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_model_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feedback: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)  # 1/0/-1
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # 关联
    user: Mapped["User"] = relationship("User", back_populates="audit_logs")  # noqa: F821
