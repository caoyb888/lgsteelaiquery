"""
单元测试：app/core/result_formatter.py

覆盖场景：
- display_type=table → 正确的 columns/rows 结构
- display_type=bar_chart → chart_option 包含 xAxis、series，series[*].type=bar
- display_type=line_chart → xAxis.type=category，series[*].type=line
- display_type=pie_chart → series[0].type=pie
- display_type=single_value → 提取第一行第一列的值
- source_info 被强制附加到 FormattedResult
- truncated / total_rows 正确透传
- 空结果集的边界情况
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.core.result_formatter import FormattedResult, NLGResult, ResultFormatter
from app.core.sql_executor import QueryResult
from app.schemas.chat import DataSourceInfo


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

def _make_query_result(
    rows: list[dict[str, Any]],
    columns: list[str] | None = None,
    total_rows: int | None = None,
    truncated: bool = False,
    execution_ms: int = 10,
    from_cache: bool = False,
) -> QueryResult:
    cols = columns if columns is not None else (list(rows[0].keys()) if rows else [])
    return QueryResult(
        rows=rows,
        columns=cols,
        total_rows=total_rows if total_rows is not None else len(rows),
        truncated=truncated,
        execution_ms=execution_ms,
        from_cache=from_cache,
    )


def _make_nlg(
    display_type: str = "table",
    summary: str = "测试摘要",
    title: str | None = None,
) -> NLGResult:
    return NLGResult(summary=summary, display_type=display_type, title=title)  # type: ignore[arg-type]


def _make_datasource_info() -> DataSourceInfo:
    return DataSourceInfo(
        datasource_id="ds-001",
        datasource_name="销售部_月度台账_202603.xlsx",
        data_date="2026-03-15",
        upload_time=datetime(2026, 3, 18, 9, 0, 0),
    )


FORMATTER = ResultFormatter()


# ---------------------------------------------------------------------------
# 通用属性
# ---------------------------------------------------------------------------

class TestFormattedResultCommonFields:
    def test_source_info_always_attached(self) -> None:
        qr = _make_query_result([{"a": 1}])
        nlg = _make_nlg("table")
        ds = _make_datasource_info()

        result = FORMATTER.format(qr, nlg, ds)

        assert result.source_info is ds
        assert result.source_info.datasource_id == "ds-001"

    def test_truncated_propagated(self) -> None:
        qr = _make_query_result([{"a": 1}], truncated=True)
        result = FORMATTER.format(qr, _make_nlg("table"), _make_datasource_info())
        assert result.truncated is True

    def test_not_truncated_propagated(self) -> None:
        qr = _make_query_result([{"a": 1}], truncated=False)
        result = FORMATTER.format(qr, _make_nlg("table"), _make_datasource_info())
        assert result.truncated is False

    def test_total_rows_propagated(self) -> None:
        qr = _make_query_result([{"a": i} for i in range(7)], total_rows=7)
        result = FORMATTER.format(qr, _make_nlg("table"), _make_datasource_info())
        assert result.total_rows == 7

    def test_summary_propagated(self) -> None:
        qr = _make_query_result([{"a": 1}])
        nlg = _make_nlg("table", summary="查询完成，共 1 行")
        result = FORMATTER.format(qr, nlg, _make_datasource_info())
        assert result.summary == "查询完成，共 1 行"

    def test_display_type_propagated(self) -> None:
        for dt in ("table", "bar_chart", "line_chart", "pie_chart", "single_value"):
            qr = _make_query_result([{"x": 1, "y": 2}])
            result = FORMATTER.format(qr, _make_nlg(dt), _make_datasource_info())
            assert result.display_type == dt


# ---------------------------------------------------------------------------
# display_type = table
# ---------------------------------------------------------------------------

class TestFormatTable:
    def test_columns_correct(self) -> None:
        qr = _make_query_result([{"name": "张三", "amount": 100}])
        result = FORMATTER.format(qr, _make_nlg("table"), _make_datasource_info())

        assert result.table_data is not None
        assert result.table_data["columns"] == ["name", "amount"]

    def test_rows_as_list_of_lists(self) -> None:
        rows = [{"name": "张三", "amount": 100}, {"name": "李四", "amount": 200}]
        qr = _make_query_result(rows)
        result = FORMATTER.format(qr, _make_nlg("table"), _make_datasource_info())

        assert result.table_data is not None
        assert result.table_data["rows"] == [["张三", 100], ["李四", 200]]

    def test_chart_option_none_for_table(self) -> None:
        qr = _make_query_result([{"a": 1}])
        result = FORMATTER.format(qr, _make_nlg("table"), _make_datasource_info())
        assert result.chart_option is None
        assert result.single_value is None

    def test_empty_rows(self) -> None:
        qr = _make_query_result([], columns=[])
        result = FORMATTER.format(qr, _make_nlg("table"), _make_datasource_info())
        assert result.table_data == {"columns": [], "rows": []}

    def test_row_order_preserved(self) -> None:
        rows = [{"id": i} for i in range(5)]
        qr = _make_query_result(rows)
        result = FORMATTER.format(qr, _make_nlg("table"), _make_datasource_info())
        ids = [r[0] for r in result.table_data["rows"]]  # type: ignore[index]
        assert ids == list(range(5))


# ---------------------------------------------------------------------------
# display_type = bar_chart
# ---------------------------------------------------------------------------

class TestFormatBarChart:
    def test_chart_option_has_x_axis(self) -> None:
        rows = [{"month": "1月", "sales": 100}, {"month": "2月", "sales": 200}]
        qr = _make_query_result(rows)
        result = FORMATTER.format(qr, _make_nlg("bar_chart"), _make_datasource_info())

        assert result.chart_option is not None
        assert "xAxis" in result.chart_option

    def test_x_axis_data_from_first_column(self) -> None:
        rows = [{"month": "1月", "sales": 100}, {"month": "2月", "sales": 200}]
        qr = _make_query_result(rows)
        result = FORMATTER.format(qr, _make_nlg("bar_chart"), _make_datasource_info())

        x_data = result.chart_option["xAxis"]["data"]  # type: ignore[index]
        assert x_data == ["1月", "2月"]

    def test_series_type_is_bar(self) -> None:
        rows = [{"month": "1月", "sales": 100}]
        qr = _make_query_result(rows)
        result = FORMATTER.format(qr, _make_nlg("bar_chart"), _make_datasource_info())

        assert result.chart_option is not None
        series = result.chart_option["series"]
        assert all(s["type"] == "bar" for s in series)

    def test_multiple_value_columns_generate_multiple_series(self) -> None:
        rows = [{"month": "1月", "sales": 100, "cost": 60}]
        qr = _make_query_result(rows)
        result = FORMATTER.format(qr, _make_nlg("bar_chart"), _make_datasource_info())

        assert result.chart_option is not None
        assert len(result.chart_option["series"]) == 2

    def test_table_data_none_for_chart(self) -> None:
        qr = _make_query_result([{"x": 1, "y": 2}])
        result = FORMATTER.format(qr, _make_nlg("bar_chart"), _make_datasource_info())
        assert result.table_data is None

    def test_title_included_in_chart_option(self) -> None:
        qr = _make_query_result([{"x": 1, "y": 2}])
        result = FORMATTER.format(qr, _make_nlg("bar_chart", title="销售趋势"), _make_datasource_info())
        assert result.chart_option["title"]["text"] == "销售趋势"  # type: ignore[index]


# ---------------------------------------------------------------------------
# display_type = line_chart
# ---------------------------------------------------------------------------

class TestFormatLineChart:
    def test_x_axis_type_is_category(self) -> None:
        rows = [{"date": "2026-01", "amount": 100}]
        qr = _make_query_result(rows)
        result = FORMATTER.format(qr, _make_nlg("line_chart"), _make_datasource_info())

        assert result.chart_option is not None
        assert result.chart_option["xAxis"]["type"] == "category"

    def test_series_type_is_line(self) -> None:
        rows = [{"date": "2026-01", "amount": 100}]
        qr = _make_query_result(rows)
        result = FORMATTER.format(qr, _make_nlg("line_chart"), _make_datasource_info())

        series = result.chart_option["series"]  # type: ignore[index]
        assert all(s["type"] == "line" for s in series)

    def test_x_axis_data_from_first_column(self) -> None:
        rows = [{"date": "2026-01", "v": 1}, {"date": "2026-02", "v": 2}]
        qr = _make_query_result(rows)
        result = FORMATTER.format(qr, _make_nlg("line_chart"), _make_datasource_info())
        assert result.chart_option["xAxis"]["data"] == ["2026-01", "2026-02"]  # type: ignore[index]

    def test_multiple_series(self) -> None:
        rows = [{"date": "2026-01", "sales": 100, "returns": 10}]
        qr = _make_query_result(rows)
        result = FORMATTER.format(qr, _make_nlg("line_chart"), _make_datasource_info())
        assert len(result.chart_option["series"]) == 2  # type: ignore[index]


# ---------------------------------------------------------------------------
# display_type = pie_chart
# ---------------------------------------------------------------------------

class TestFormatPieChart:
    def test_series_type_is_pie(self) -> None:
        rows = [{"product": "钢材", "quantity": 500}]
        qr = _make_query_result(rows)
        result = FORMATTER.format(qr, _make_nlg("pie_chart"), _make_datasource_info())

        assert result.chart_option is not None
        assert result.chart_option["series"][0]["type"] == "pie"

    def test_pie_data_name_and_value(self) -> None:
        rows = [
            {"product": "钢材", "quantity": 500},
            {"product": "铁矿", "quantity": 300},
        ]
        qr = _make_query_result(rows)
        result = FORMATTER.format(qr, _make_nlg("pie_chart"), _make_datasource_info())

        pie_data = result.chart_option["series"][0]["data"]  # type: ignore[index]
        assert pie_data[0] == {"name": "钢材", "value": 500}
        assert pie_data[1] == {"name": "铁矿", "value": 300}

    def test_single_column_pie_value_defaults_to_zero(self) -> None:
        rows = [{"product": "钢材"}]
        qr = _make_query_result(rows, columns=["product"])
        result = FORMATTER.format(qr, _make_nlg("pie_chart"), _make_datasource_info())
        pie_data = result.chart_option["series"][0]["data"]  # type: ignore[index]
        assert pie_data[0]["value"] == 0

    def test_table_data_none_for_pie(self) -> None:
        qr = _make_query_result([{"x": 1, "y": 2}])
        result = FORMATTER.format(qr, _make_nlg("pie_chart"), _make_datasource_info())
        assert result.table_data is None


# ---------------------------------------------------------------------------
# display_type = single_value
# ---------------------------------------------------------------------------

class TestFormatSingleValue:
    def test_extracts_first_row_first_col(self) -> None:
        qr = _make_query_result([{"total": 9999}])
        result = FORMATTER.format(qr, _make_nlg("single_value"), _make_datasource_info())

        assert result.single_value == 9999

    def test_extracts_string_value(self) -> None:
        qr = _make_query_result([{"name": "张三", "age": 30}])
        result = FORMATTER.format(qr, _make_nlg("single_value"), _make_datasource_info())
        assert result.single_value == "张三"

    def test_empty_rows_returns_none(self) -> None:
        qr = _make_query_result([], columns=[])
        result = FORMATTER.format(qr, _make_nlg("single_value"), _make_datasource_info())
        assert result.single_value is None

    def test_table_data_none_for_single_value(self) -> None:
        qr = _make_query_result([{"v": 1}])
        result = FORMATTER.format(qr, _make_nlg("single_value"), _make_datasource_info())
        assert result.table_data is None
        assert result.chart_option is None


# ---------------------------------------------------------------------------
# 未知 display_type 降级
# ---------------------------------------------------------------------------

class TestUnknownDisplayType:
    def test_unknown_display_type_falls_back_to_table(self) -> None:
        qr = _make_query_result([{"a": 1}])
        nlg = NLGResult(summary="测试", display_type="unknown_type")  # type: ignore[arg-type]
        result = FORMATTER.format(qr, nlg, _make_datasource_info())
        assert result.display_type == "table"
        assert result.table_data is not None
