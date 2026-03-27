"""数据源与字段映射 ORM 模型"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Date, DateTime, Float, ForeignKey,
    Integer, String, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.meta_session import MetaBase


class Datasource(MetaBase):
    __tablename__ = "datasources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    domain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(256), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    data_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    update_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)  # replace/append
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    biz_table_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    total_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # 关联
    uploaded_by_user: Mapped["User"] = relationship(  # noqa: F821
        "User", back_populates="datasources", foreign_keys=[uploaded_by]
    )
    field_mappings: Mapped[list["FieldMapping"]] = relationship(
        "FieldMapping", back_populates="datasource", cascade="all, delete-orphan"
    )


class FieldMapping(MetaBase):
    __tablename__ = "field_mappings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    datasource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasources.id", ondelete="CASCADE"), nullable=False
    )
    raw_name: Mapped[str] = mapped_column(String(256), nullable=False)
    std_name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    field_type: Mapped[str] = mapped_column(String(32), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    mapping_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # 关联
    datasource: Mapped["Datasource"] = relationship(
        "Datasource", back_populates="field_mappings"
    )
