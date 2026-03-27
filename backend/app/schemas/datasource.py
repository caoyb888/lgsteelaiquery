"""数据源管理相关 Schema"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class FieldMappingPreview(BaseModel):
    raw_name: str
    std_name: str
    display_name: str
    field_type: str
    unit: str | None = None
    confidence: float
    needs_confirm: bool
    mapping_source: str      # exact_match / embedding / llm / manual


class FieldMappingConfirm(BaseModel):
    raw_name: str
    std_name: str            # 人工确认/修改后的标准名
    display_name: str
    field_type: str
    unit: str | None = None


class DatasourceUploadResponse(BaseModel):
    upload_id: str
    status: Literal["pending_confirm", "processing", "success", "error"]
    preview: dict | None = None   # 包含 total_rows, sheets, field_mappings


class DatasourceConfirmRequest(BaseModel):
    confirmed_mappings: list[FieldMappingConfirm] = Field(
        default_factory=list,
        description="有修改的字段映射列表，未修改的不用传",
    )


class DatasourceListItem(BaseModel):
    id: str
    name: str
    domain: str
    description: str | None = None
    original_filename: str
    data_date: date
    status: str
    total_rows: int | None = None
    uploaded_by_name: str | None = None
    created_at: datetime
    is_stale: bool = False          # 是否超期未更新
