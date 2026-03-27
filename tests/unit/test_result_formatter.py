"""
tests/unit/test_result_formatter.py

ResultFormatter 单元测试。
验证 table / bar_chart / line_chart / pie_chart / single_value 五种展现形式
以及强制附加 DataSourceInfo、截断标志等。
不依赖外部服务。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.result_formatter import FormattedResult, NLGResult, ResultFormatter
from app.core.sql_executor import QueryResult
from app.schemas.chat import DataSourceInfo


# ─── Fixtures ───────────────────────────────────────────────────────────────


def _make_qr(
    rows=None,
    columns=None,
    total_rows: int = 3,
    truncated: bool = False,
    execution_ms: int = 10,
    from_cache: bool = False,
) -> QueryResult:
    if rows is None:
        rows = [
            {"month": "2026-01", "amount": 100},
            {"month": "2026-02", "amount": 200},
            {"month": "2026-03", "amount": 300},
        ]
    if columns is None:
        columns = ["month", "amount"]
    return QueryResult(
        rows=rows,
        columns=columns,
        total_rows=total_rows,
        truncated=truncated,
        execution_ms=execution_ms,
        from_cache=from_cache,
    )


def _make_nlg(
    summary: str = "查询成功",
    display_type: str = "table",
    title: str | None = None,
) -> NLGResult:
    return NLGResult(summary=summary, display_type=display_type, title=title)  # type: ignore[arg-type]


def _make_source_info() -> DataSourceInfo:
    return DataSourceInfo(
        datasource_id="ds-001",
        datasource_name="销售部_月度台账.xlsx",
        data_date="2026-03-15",
        upload_time=datetime(2026, 3, 18, 9, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def formatter() -> ResultFormatter:
    return ResultFormatter()


# ─── FormattedResult 结构 ─────────────────────────────────────────────────────


def test_format_returns_formatted_result(formatter):
    result = formatter.format(_make_qr(), _make_nlg(), _make_source_info())
    assert isinstance(result, FormattedResult)


def test_format_attaches_source_info(formatter):
    src = _make_source_info()
    result = formatter.format(_make_qr(), _make_nlg(), src)
    assert result.source_info is src


def test_format_attaches_summary(formatter):
    result = formatter.format(_make_qr(), _make_nlg(summary="共3条记录"), _make_source_info())
    assert result.summary == "共3条记录"


def test_format_propagates_truncated_flag(formatter):
    qr = _make_qr(truncated=True)
    result = formatter.format(qr, _make_nlg(), _make_source_info())
    assert result.truncated is True


def test_format_propagates_total_rows(formatter):
    qr = _make_qr(total_rows=5000)
    result = formatter.format(qr, _make_nlg(), _make_source_info())
    assert result.total_rows == 5000


# ─── table 格式 ──────────────────────────────────────────────────────────────


def test_table_format_structure(formatter):
    result = formatter.format(_make_qr(), _make_nlg(display_type="table"), _make_source_info())
    assert result.display_type == "table"
    assert result.table_data is not None
    assert result.chart_option is None
    assert result.single_value is None


def test_table_format_columns(formatter):
    result = formatter.format(_make_qr(), _make_nlg(display_type="table"), _make_source_info())
    assert result.table_data["columns"] == ["month", "amount"]


def test_table_format_rows_as_lists(formatter):
    result = formatter.format(_make_qr(), _make_nlg(display_type="table"), _make_source_info())
    rows = result.table_data["rows"]
    assert rows[0] == ["2026-01", 100]
    assert rows[1] == ["2026-02", 200]
    assert rows[2] == ["2026-03", 300]


def test_table_format_empty_result(formatter):
    qr = _make_qr(rows=[], columns=[], total_rows=0)
    result = formatter.format(qr, _make_nlg(display_type="table"), _make_source_info())
    assert result.table_data == {"columns": [], "rows": []}


# ─── bar_chart 格式 ───────────────────────────────────────────────────────────


def test_bar_chart_format_structure(formatter):
    result = formatter.format(_make_qr(), _make_nlg(display_type="bar_chart"), _make_source_info())
    assert result.display_type == "bar_chart"
    assert result.chart_option is not None
    assert result.table_data is None
    assert result.single_value is None


def test_bar_chart_x_axis_data(formatter):
    result = formatter.format(_make_qr(), _make_nlg(display_type="bar_chart"), _make_source_info())
    option = result.chart_option
    assert option["xAxis"]["data"] == ["2026-01", "2026-02", "2026-03"]


def test_bar_chart_series_type(formatter):
    result = formatter.format(_make_qr(), _make_nlg(display_type="bar_chart"), _make_source_info())
    option = result.chart_option
    assert len(option["series"]) == 1
    assert option["series"][0]["type"] == "bar"
    assert option["series"][0]["name"] == "amount"
    assert option["series"][0]["data"] == [100, 200, 300]


def test_bar_chart_title(formatter):
    result = formatter.format(
        _make_qr(),
        _make_nlg(display_type="bar_chart", title="月度销售额"),
        _make_source_info(),
    )
    assert result.chart_option["title"]["text"] == "月度销售额"


def test_bar_chart_multiple_series(formatter):
    rows = [
        {"month": "2026-01", "revenue": 100, "cost": 80},
        {"month": "2026-02", "revenue": 200, "cost": 160},
    ]
    qr = _make_qr(rows=rows, columns=["month", "revenue", "cost"], total_rows=2)
    result = formatter.format(qr, _make_nlg(display_type="bar_chart"), _make_source_info())
    option = result.chart_option
    assert len(option["series"]) == 2
    assert option["series"][0]["name"] == "revenue"
    assert option["series"][1]["name"] == "cost"


def test_bar_chart_empty_result(formatter):
    qr = _make_qr(rows=[], columns=[], total_rows=0)
    result = formatter.format(qr, _make_nlg(display_type="bar_chart"), _make_source_info())
    assert result.chart_option == {}


# ─── line_chart 格式 ──────────────────────────────────────────────────────────


def test_line_chart_format_structure(formatter):
    result = formatter.format(_make_qr(), _make_nlg(display_type="line_chart"), _make_source_info())
    assert result.display_type == "line_chart"
    assert result.chart_option is not None


def test_line_chart_series_type(formatter):
    result = formatter.format(_make_qr(), _make_nlg(display_type="line_chart"), _make_source_info())
    option = result.chart_option
    assert option["series"][0]["type"] == "line"


def test_line_chart_x_axis_data(formatter):
    result = formatter.format(_make_qr(), _make_nlg(display_type="line_chart"), _make_source_info())
    assert result.chart_option["xAxis"]["data"] == ["2026-01", "2026-02", "2026-03"]


def test_line_chart_empty(formatter):
    qr = _make_qr(rows=[], columns=[], total_rows=0)
    result = formatter.format(qr, _make_nlg(display_type="line_chart"), _make_source_info())
    assert result.chart_option == {}


# ─── pie_chart 格式 ───────────────────────────────────────────────────────────


def test_pie_chart_format_structure(formatter):
    rows = [
        {"region": "华北", "amount": 300},
        {"region": "华东", "amount": 500},
        {"region": "华南", "amount": 200},
    ]
    qr = _make_qr(rows=rows, columns=["region", "amount"], total_rows=3)
    result = formatter.format(qr, _make_nlg(display_type="pie_chart"), _make_source_info())
    assert result.display_type == "pie_chart"
    assert result.chart_option is not None


def test_pie_chart_data(formatter):
    rows = [
        {"region": "华北", "amount": 300},
        {"region": "华东", "amount": 500},
    ]
    qr = _make_qr(rows=rows, columns=["region", "amount"], total_rows=2)
    result = formatter.format(qr, _make_nlg(display_type="pie_chart"), _make_source_info())
    pie_data = result.chart_option["series"][0]["data"]
    assert pie_data == [
        {"name": "华北", "value": 300},
        {"name": "华东", "value": 500},
    ]


def test_pie_chart_single_column_value_is_zero(formatter):
    """只有一列时，value 应为 0。"""
    rows = [{"region": "华北"}, {"region": "华东"}]
    qr = _make_qr(rows=rows, columns=["region"], total_rows=2)
    result = formatter.format(qr, _make_nlg(display_type="pie_chart"), _make_source_info())
    pie_data = result.chart_option["series"][0]["data"]
    assert all(item["value"] == 0 for item in pie_data)


def test_pie_chart_empty(formatter):
    qr = _make_qr(rows=[], columns=[], total_rows=0)
    result = formatter.format(qr, _make_nlg(display_type="pie_chart"), _make_source_info())
    assert result.chart_option == {}


# ─── single_value 格式 ────────────────────────────────────────────────────────


def test_single_value_format_structure(formatter):
    qr = _make_qr(
        rows=[{"total": 42}],
        columns=["total"],
        total_rows=1,
    )
    result = formatter.format(qr, _make_nlg(display_type="single_value"), _make_source_info())
    assert result.display_type == "single_value"
    assert result.single_value == 42
    assert result.table_data is None
    assert result.chart_option is None


def test_single_value_empty_result(formatter):
    qr = _make_qr(rows=[], columns=[], total_rows=0)
    result = formatter.format(qr, _make_nlg(display_type="single_value"), _make_source_info())
    assert result.single_value is None


def test_single_value_uses_first_row_first_col(formatter):
    rows = [{"a": 10, "b": 20}, {"a": 30, "b": 40}]
    qr = _make_qr(rows=rows, columns=["a", "b"], total_rows=2)
    result = formatter.format(qr, _make_nlg(display_type="single_value"), _make_source_info())
    assert result.single_value == 10


# ─── 未知 display_type 降级 ─────────────────────────────────────────────────


def test_unknown_display_type_falls_back_to_table(formatter):
    nlg = _make_nlg(display_type="unknown_type")  # type: ignore[arg-type]
    result = formatter.format(_make_qr(), nlg, _make_source_info())
    assert result.display_type == "table"
    assert result.table_data is not None


# ─── ECharts option 必要字段 ──────────────────────────────────────────────────


def test_bar_chart_has_required_echarts_fields(formatter):
    result = formatter.format(_make_qr(), _make_nlg(display_type="bar_chart"), _make_source_info())
    option = result.chart_option
    for field in ("title", "tooltip", "legend", "xAxis", "yAxis", "series"):
        assert field in option, f"缺少 ECharts 字段: {field}"


def test_line_chart_has_required_echarts_fields(formatter):
    result = formatter.format(_make_qr(), _make_nlg(display_type="line_chart"), _make_source_info())
    option = result.chart_option
    for field in ("title", "tooltip", "legend", "xAxis", "yAxis", "series"):
        assert field in option, f"缺少 ECharts 字段: {field}"


def test_pie_chart_has_required_echarts_fields(formatter):
    rows = [{"r": "A", "v": 1}]
    qr = _make_qr(rows=rows, columns=["r", "v"], total_rows=1)
    result = formatter.format(qr, _make_nlg(display_type="pie_chart"), _make_source_info())
    option = result.chart_option
    for field in ("title", "tooltip", "legend", "series"):
        assert field in option, f"缺少 ECharts 字段: {field}"
