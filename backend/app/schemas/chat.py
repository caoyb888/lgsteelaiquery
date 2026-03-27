"""对话查询相关 Schema"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class DataSourceInfo(BaseModel):
    """数据来源与时效标注（每条查询结果强制附带）"""
    datasource_id: str
    datasource_name: str
    data_date: str           # 数据截止日期 YYYY-MM-DD
    upload_time: datetime    # 上传时间


class ChatQueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, description="用户自然语言问题")
    conversation_id: str | None = Field(None, description="多轮对话 ID，新对话不传")
    datasource_ids: list[str] | None = Field(None, description="指定查询的数据源 ID 列表，不传则查所有有权限的数据源")


class ChatQueryResponse(BaseModel):
    answer_text: str
    display_type: Literal["single_value", "table", "bar_chart", "line_chart", "pie_chart"]
    chart_option: dict[str, Any] | None = None   # ECharts option
    table_data: list[dict[str, Any]] | None = None
    sql: str | None = None                        # debug 模式下返回
    data_sources: list[DataSourceInfo] = Field(default_factory=list)
    confidence: float | None = None
    execution_ms: int | None = None
    conversation_id: str | None = None


class FeedbackRequest(BaseModel):
    feedback: Literal[1, -1] = Field(..., description="1=点赞 -1=点踩")
