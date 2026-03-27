"""
结果格式化器

将 QueryResult + NLGResult 格式化为前端可直接渲染的结构。
支持 table / bar_chart / line_chart / pie_chart / single_value 五种展现形式。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from loguru import logger

from app.core.sql_executor import QueryResult
from app.schemas.chat import DataSourceInfo

DisplayType = Literal["table", "bar_chart", "line_chart", "pie_chart", "single_value"]


@dataclass
class NLGResult:
    """
    自然语言生成结果（由 nlg.py 产生，此处定义接口契约）。

    display_type 指导 ResultFormatter 选择展现形式。
    summary      是给用户展示的自然语言摘要文本。
    title        可选图表标题。
    """

    summary: str
    display_type: DisplayType = "table"
    title: str | None = None


@dataclass
class FormattedResult:
    """前端可直接渲染的格式化结果"""

    display_type: DisplayType
    table_data: dict[str, Any] | None     # display_type=table 时填充
    chart_option: dict[str, Any] | None   # 图表类型时填充 ECharts option
    single_value: Any                     # display_type=single_value 时填充
    summary: str                          # 自然语言摘要
    source_info: DataSourceInfo           # 数据来源（强制附加）
    truncated: bool                       # 是否被截断
    total_rows: int


class ResultFormatter:
    """
    结果格式化器。

    根据 nlg_result.display_type 将 QueryResult 转换为对应的前端渲染结构，
    并强制附加 DataSourceInfo 和截断标志。
    """

    def format(
        self,
        query_result: QueryResult,
        nlg_result: NLGResult,
        datasource_info: DataSourceInfo,
    ) -> FormattedResult:
        """
        路由到对应格式化方法并组装 FormattedResult。

        Raises:
            ValueError: display_type 不在支持范围内（防御性校验）
        """
        display_type = nlg_result.display_type
        title = nlg_result.title

        table_data: dict[str, Any] | None = None
        chart_option: dict[str, Any] | None = None
        single_value: Any = None

        if display_type == "table":
            table_data = self._format_table(query_result)
        elif display_type == "bar_chart":
            chart_option = self._format_bar_chart(query_result, title)
        elif display_type == "line_chart":
            chart_option = self._format_line_chart(query_result, title)
        elif display_type == "pie_chart":
            chart_option = self._format_pie_chart(query_result, title)
        elif display_type == "single_value":
            single_value = self._format_single_value(query_result)
        else:
            logger.warning("未知 display_type，降级为 table", display_type=display_type)
            display_type = "table"
            table_data = self._format_table(query_result)

        logger.info(
            "结果格式化完成",
            display_type=display_type,
            total_rows=query_result.total_rows,
            truncated=query_result.truncated,
        )

        return FormattedResult(
            display_type=display_type,
            table_data=table_data,
            chart_option=chart_option,
            single_value=single_value,
            summary=nlg_result.summary,
            source_info=datasource_info,
            truncated=query_result.truncated,
            total_rows=query_result.total_rows,
        )

    # ------------------------------------------------------------------
    # 私有格式化方法
    # ------------------------------------------------------------------

    def _format_table(self, qr: QueryResult) -> dict[str, Any]:
        """
        返回 {columns: list[str], rows: list[list]}。

        每行转为按 columns 顺序排列的值列表，前端直接渲染为 <table>。
        """
        rows_as_lists: list[list[Any]] = [
            [row.get(col) for col in qr.columns]
            for row in qr.rows
        ]
        return {"columns": qr.columns, "rows": rows_as_lists}

    def _format_bar_chart(
        self,
        qr: QueryResult,
        title: str | None,
    ) -> dict[str, Any]:
        """
        生成 ECharts bar chart option。

        - x 轴：第一列（分类维度）
        - y 轴：其余数值列，多列时生成多 series
        """
        if not qr.columns:
            return {}

        x_col = qr.columns[0]
        value_cols = qr.columns[1:]

        x_data: list[Any] = [row.get(x_col) for row in qr.rows]

        series: list[dict[str, Any]] = []
        for col in value_cols:
            series.append(
                {
                    "name": col,
                    "type": "bar",
                    "data": [row.get(col) for row in qr.rows],
                }
            )

        option: dict[str, Any] = {
            "title": {"text": title or ""},
            "tooltip": {"trigger": "axis"},
            "legend": {"data": value_cols},
            "xAxis": {
                "type": "category",
                "data": x_data,
            },
            "yAxis": {"type": "value"},
            "series": series,
        }
        return option

    def _format_line_chart(
        self,
        qr: QueryResult,
        title: str | None,
    ) -> dict[str, Any]:
        """
        生成 ECharts line chart option。

        - x 轴：第一列（通常为时间维度）
        - y 轴：其余数值列，多列时生成多 series
        """
        if not qr.columns:
            return {}

        x_col = qr.columns[0]
        value_cols = qr.columns[1:]

        x_data: list[Any] = [row.get(x_col) for row in qr.rows]

        series: list[dict[str, Any]] = []
        for col in value_cols:
            series.append(
                {
                    "name": col,
                    "type": "line",
                    "data": [row.get(col) for row in qr.rows],
                }
            )

        option: dict[str, Any] = {
            "title": {"text": title or ""},
            "tooltip": {"trigger": "axis"},
            "legend": {"data": value_cols},
            "xAxis": {
                "type": "category",
                "data": x_data,
            },
            "yAxis": {"type": "value"},
            "series": series,
        }
        return option

    def _format_pie_chart(
        self,
        qr: QueryResult,
        title: str | None,
    ) -> dict[str, Any]:
        """
        生成 ECharts pie chart option。

        - name：第一列
        - value：第二列（数值）
        若列数不足两列，value 一律为 0。
        """
        if not qr.columns:
            return {}

        name_col = qr.columns[0]
        value_col = qr.columns[1] if len(qr.columns) > 1 else None

        pie_data: list[dict[str, Any]] = [
            {
                "name": row.get(name_col),
                "value": row.get(value_col) if value_col else 0,
            }
            for row in qr.rows
        ]

        option: dict[str, Any] = {
            "title": {"text": title or "", "left": "center"},
            "tooltip": {"trigger": "item"},
            "legend": {"orient": "vertical", "left": "left"},
            "series": [
                {
                    "name": title or "",
                    "type": "pie",
                    "radius": "50%",
                    "data": pie_data,
                    "emphasis": {
                        "itemStyle": {
                            "shadowBlur": 10,
                            "shadowOffsetX": 0,
                            "shadowColor": "rgba(0, 0, 0, 0.5)",
                        }
                    },
                }
            ],
        }
        return option

    def _format_single_value(self, qr: QueryResult) -> Any:
        """提取第一行第一列的值，结果为空时返回 None。"""
        if qr.rows and qr.columns:
            return qr.rows[0].get(qr.columns[0])
        return None
