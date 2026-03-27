"""
单元测试：app/core/data_cleaner.py

覆盖率目标：≥ 90%

测试场景：
- 7 条清洗规则各对应 1 个测试用例
- clean_and_load 返回正确的 CleanResult
- replace 模式生成 DROP+CREATE SQL
- append 模式生成 CREATE IF NOT EXISTS SQL
- update_mode 非法值抛出 FieldMappingError
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, call, patch

import pandas as pd
import pytest

# 设置必要的环境变量（在导入 app 模块前）
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("META_DB_USER", "test_user")
os.environ.setdefault("META_DB_PASSWORD", "test_pass")
os.environ.setdefault("BIZ_DB_USER", "test_user")
os.environ.setdefault("BIZ_DB_PASSWORD", "test_pass")
os.environ.setdefault("REDIS_PASSWORD", "")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-32-chars-xxxxxxxxx")

from app.core.data_cleaner import (
    CleanResult,
    DataCleaner,
    _build_table_name,
    _compute_row_hash,
    _dedup_by_hash,
    _normalize_boolean,
    _normalize_date,
    _strip_unit_to_numeric,
    _truncate_long_string,
)
from app.core.excel_parser import ExcelParseResult, ParsedField
from app.utils.exceptions import FieldMappingError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_parsed_field(
    clean_name: str,
    inferred_type: str,
    raw_name: str | None = None,
    unit: str | None = None,
) -> ParsedField:
    return ParsedField(
        raw_name=raw_name or clean_name,
        clean_name=clean_name,
        unit=unit,
        inferred_type=inferred_type,
        sample_values=[],
        null_ratio=0.0,
    )


def _make_parse_result(
    df: pd.DataFrame,
    fields: list[ParsedField],
) -> ExcelParseResult:
    return ExcelParseResult(
        df=df,
        fields=fields,
        header_row_index=0,
        sheet_name="Sheet1",
        total_rows=len(df),
        warnings=[],
        source_filename="test.xlsx",
    )


def _make_session_factory(session_mock: AsyncMock) -> MagicMock:
    """
    构造一个异步上下文管理器工厂，使 `async with factory() as session` 返回 session_mock。
    """

    @asynccontextmanager
    async def _factory() -> AsyncGenerator[AsyncMock, None]:
        yield session_mock

    factory = MagicMock(side_effect=_factory)
    return factory


def _make_cleaner(session_mock: AsyncMock | None = None) -> tuple[DataCleaner, AsyncMock]:
    if session_mock is None:
        session_mock = AsyncMock()
        session_mock.execute = AsyncMock()
        session_mock.commit = AsyncMock()
        session_mock.rollback = AsyncMock()
    factory = _make_session_factory(session_mock)
    cleaner = DataCleaner(biz_session_factory=factory)
    return cleaner, session_mock


# ---------------------------------------------------------------------------
# 规则 1：去空行
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rule1_empty_rows_removed() -> None:
    """全部字段均为 NaN 的行应被删除"""
    df = pd.DataFrame(
        {
            "name": ["Alice", None, "Bob"],
            "score": [90.0, None, 85.0],
        }
    )
    fields = [
        _make_parsed_field("name", "text"),
        _make_parsed_field("score", "numeric"),
    ]
    parse_result = _make_parse_result(df, fields)

    cleaner, session_mock = _make_cleaner()
    result = await cleaner.clean_and_load(parse_result, "abc12345", "sales", "replace")

    # 第 2 行（全 None）应被删除，只剩 2 行
    assert result.rows_written == 2
    assert result.rows_skipped == 1


@pytest.mark.asyncio
async def test_rule1_no_empty_rows_no_skip() -> None:
    """无空行时 rows_skipped == 0"""
    df = pd.DataFrame({"name": ["Alice", "Bob"], "score": [90.0, 85.0]})
    fields = [
        _make_parsed_field("name", "text"),
        _make_parsed_field("score", "numeric"),
    ]
    parse_result = _make_parse_result(df, fields)
    cleaner, _ = _make_cleaner()
    result = await cleaner.clean_and_load(parse_result, "abc12345", "sales", "replace")
    assert result.rows_skipped == 0
    assert result.rows_written == 2


# ---------------------------------------------------------------------------
# 规则 2：去首尾空格
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rule2_string_strip() -> None:
    """object 列的字符串值首尾空格应被去除"""
    df = pd.DataFrame({"name": ["  Alice  ", " Bob\t", "Charlie"]})
    fields = [_make_parsed_field("name", "text")]
    parse_result = _make_parse_result(df, fields)

    cleaner, session_mock = _make_cleaner()

    # 捕获 INSERT SQL 中的参数，验证值已被 strip
    inserted_values: list[str] = []

    async def mock_execute(stmt: object, params: dict | None = None) -> None:
        if params is not None:
            for v in params.values():
                if isinstance(v, str):
                    inserted_values.append(v)

    session_mock.execute = mock_execute
    await cleaner.clean_and_load(parse_result, "abc12345", "sales", "replace")

    # 所有插入的字符串值不应含首尾空格
    for v in inserted_values:
        assert v == v.strip(), f"值 {v!r} 应已 strip"


# ---------------------------------------------------------------------------
# 规则 3：日期统一为 YYYY-MM-DD
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "input_val, expected",
    [
        ("2026-01-15", "2026-01-15"),
        ("2026/01/15", "2026-01-15"),
        ("20260115", "2026-01-15"),
        ("2026年1月", "2026-01-01"),
        ("2026年1月15日", "2026-01-15"),
        ("2026-01", "2026-01-01"),
        (None, None),
        ("invalid_date", None),
    ],
)
def test_normalize_date_unit(input_val: str | None, expected: str | None) -> None:
    """_normalize_date 单元测试：各种格式均能正确转换"""
    assert _normalize_date(input_val) == expected


@pytest.mark.asyncio
async def test_rule3_date_normalization() -> None:
    """date 类型列中各种日期格式统一为 YYYY-MM-DD"""
    df = pd.DataFrame(
        {"order_date": ["2026年1月", "2026/03/15", "invalid_date"]}
    )
    fields = [_make_parsed_field("order_date", "date")]
    parse_result = _make_parse_result(df, fields)

    cleaner, session_mock = _make_cleaner()

    inserted_values: list[str | None] = []

    async def mock_execute(stmt: object, params: dict | None = None) -> None:
        if params is not None and "INSERT" in str(stmt).upper():
            inserted_values.extend(params.values())

    session_mock.execute = mock_execute
    await cleaner.clean_and_load(parse_result, "abc12345", "sales", "replace")

    # 第一行："2026年1月" → "2026-01-01"
    # 第二行："2026/03/15" → "2026-03-15"
    # 第三行："invalid_date" → None（仍写入，但值为 None）
    dates_in_params = [v for v in inserted_values if v is None or (isinstance(v, str) and "-" in v and len(v) == 10)]
    assert "2026-01-01" in inserted_values
    assert "2026-03-15" in inserted_values


# ---------------------------------------------------------------------------
# 规则 4：数字单位剥离
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "input_val, expected",
    [
        ("1250万", 1250.0),
        ("860元", 860.0),
        ("8500吨", 8500.0),
        ("3.14", 3.14),
        (100, 100.0),
        (100.5, 100.5),
        (None, None),
        ("无数据", None),
    ],
)
def test_strip_unit_to_numeric_unit(input_val: object, expected: float | None) -> None:
    """_strip_unit_to_numeric 单元测试"""
    result = _strip_unit_to_numeric(input_val)
    assert result == expected


@pytest.mark.asyncio
async def test_rule4_numeric_unit_stripped() -> None:
    """numeric 类型列中含单位的字符串应提取纯数字"""
    df = pd.DataFrame({"revenue": ["1250万", "860元", "8500吨"]})
    fields = [_make_parsed_field("revenue", "numeric")]
    parse_result = _make_parse_result(df, fields)

    cleaner, session_mock = _make_cleaner()
    inserted_nums: list[float] = []

    async def mock_execute(stmt: object, params: dict | None = None) -> None:
        if params is not None and "INSERT" in str(stmt).upper():
            for v in params.values():
                if isinstance(v, float):
                    inserted_nums.append(v)

    session_mock.execute = mock_execute
    await cleaner.clean_and_load(parse_result, "abc12345", "sales", "replace")

    assert 1250.0 in inserted_nums
    assert 860.0 in inserted_nums
    assert 8500.0 in inserted_nums


# ---------------------------------------------------------------------------
# 规则 5：布尔值统一
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "input_val, expected",
    [
        ("是", True),
        ("Y", True),
        ("Yes", True),
        ("1", True),
        ("True", True),
        ("否", False),
        ("N", False),
        ("No", False),
        ("0", False),
        ("False", False),
        (None, None),
        ("未知", None),
        (True, True),
        (False, False),
    ],
)
def test_normalize_boolean_unit(input_val: object, expected: bool | None) -> None:
    """_normalize_boolean 单元测试"""
    assert _normalize_boolean(input_val) == expected


@pytest.mark.asyncio
async def test_rule5_boolean_normalization() -> None:
    """boolean 类型列中各种真值和假值都应统一"""
    df = pd.DataFrame({"is_active": ["是", "否", "Y", "N", "True"]})
    fields = [_make_parsed_field("is_active", "boolean")]
    parse_result = _make_parse_result(df, fields)

    cleaner, session_mock = _make_cleaner()
    inserted_bools: list[bool] = []

    async def mock_execute(stmt: object, params: dict | None = None) -> None:
        if params is not None and "INSERT" in str(stmt).upper():
            for v in params.values():
                if isinstance(v, bool):
                    inserted_bools.append(v)

    session_mock.execute = mock_execute
    await cleaner.clean_and_load(parse_result, "abc12345", "sales", "replace")

    assert inserted_bools.count(True) >= 3
    assert inserted_bools.count(False) >= 2


# ---------------------------------------------------------------------------
# 规则 6：超长字符串截断
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "input_val, max_len, expected_suffix",
    [
        ("a" * 501, 500, "..."),
        ("a" * 500, 500, None),   # 恰好 500，不截断
        ("short", 500, None),
        (123, 500, None),         # 非字符串，不截断
    ],
)
def test_truncate_long_string_unit(
    input_val: object, max_len: int, expected_suffix: str | None
) -> None:
    """_truncate_long_string 单元测试"""
    result = _truncate_long_string(input_val, max_len)
    if expected_suffix is not None:
        assert isinstance(result, str)
        assert result.endswith(expected_suffix)
        assert len(result) == max_len + len(expected_suffix)
    else:
        assert result == input_val


@pytest.mark.asyncio
async def test_rule6_long_string_truncated() -> None:
    """text 类型列中超过 500 字符的值应被截断并加 '...'"""
    long_val = "字" * 600  # 600 个字符
    df = pd.DataFrame({"description": [long_val, "short text"]})
    fields = [_make_parsed_field("description", "text")]
    parse_result = _make_parse_result(df, fields)

    cleaner, session_mock = _make_cleaner()
    inserted_texts: list[str] = []

    async def mock_execute(stmt: object, params: dict | None = None) -> None:
        if params is not None and "INSERT" in str(stmt).upper():
            for v in params.values():
                if isinstance(v, str):
                    inserted_texts.append(v)

    session_mock.execute = mock_execute
    await cleaner.clean_and_load(parse_result, "abc12345", "sales", "replace")

    truncated = [v for v in inserted_texts if v.endswith("...")]
    assert len(truncated) >= 1
    assert len(truncated[0]) == 503  # 500 + len("...")


# ---------------------------------------------------------------------------
# 规则 7：追加模式去重
# ---------------------------------------------------------------------------


def test_dedup_by_hash_removes_duplicates() -> None:
    """_dedup_by_hash 应去除 DataFrame 内部的重复行"""
    df = pd.DataFrame(
        {
            "name": ["Alice", "Bob", "Alice"],
            "score": [90.0, 85.0, 90.0],
        }
    )
    warnings: list[str] = []
    result_df, skipped = _dedup_by_hash(df, warnings)

    assert len(result_df) == 2
    assert skipped == 1
    assert any("去重" in w for w in warnings)


@pytest.mark.asyncio
async def test_rule7_append_dedup() -> None:
    """append 模式下重复行（基于行内容哈希）应被跳过"""
    df = pd.DataFrame(
        {
            "name": ["Alice", "Bob", "Alice"],
            "score": [90.0, 85.0, 90.0],
        }
    )
    fields = [
        _make_parsed_field("name", "text"),
        _make_parsed_field("score", "numeric"),
    ]
    parse_result = _make_parse_result(df, fields)

    cleaner, _ = _make_cleaner()
    result = await cleaner.clean_and_load(parse_result, "abc12345", "sales", "append")

    # Alice/90.0 重复出现 2 次，去重后应保留 2 条（Alice 和 Bob）
    assert result.rows_written == 2
    assert result.rows_skipped == 1


# ---------------------------------------------------------------------------
# CleanResult 正确性
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_and_load_result_fields() -> None:
    """clean_and_load 返回的 CleanResult 应包含正确的表名、行数"""
    df = pd.DataFrame({"product": ["钢板", "型钢"], "qty": [100.0, 200.0]})
    fields = [
        _make_parsed_field("product", "text"),
        _make_parsed_field("qty", "numeric"),
    ]
    parse_result = _make_parse_result(df, fields)

    cleaner, _ = _make_cleaner()
    result = await cleaner.clean_and_load(parse_result, "a1b2c3d4", "production", "replace")

    assert result.table_name == "production_a1b2c3d4"
    assert result.rows_written == 2
    assert result.rows_skipped == 0
    assert isinstance(result.warnings, list)


def test_build_table_name() -> None:
    """_build_table_name 应正确拼接域名和 datasource_id 前 8 位十六进制"""
    assert _build_table_name("sales", "a1b2c3d4-e5f6-7890") == "sales_a1b2c3d4"
    assert _build_table_name("finance", "abcdef01") == "finance_abcdef01"


# ---------------------------------------------------------------------------
# replace 模式：DROP + CREATE SQL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_mode_drop_create_sql() -> None:
    """replace 模式应先执行 DROP TABLE IF EXISTS，再执行 CREATE TABLE"""
    df = pd.DataFrame({"col_a": ["v1"]})
    fields = [_make_parsed_field("col_a", "text")]
    parse_result = _make_parse_result(df, fields)

    executed_sqls: list[str] = []

    async def mock_execute(stmt: object, params: dict | None = None) -> None:
        executed_sqls.append(str(stmt))

    session_mock = AsyncMock()
    session_mock.execute = mock_execute
    session_mock.commit = AsyncMock()
    session_mock.rollback = AsyncMock()

    cleaner, _ = _make_cleaner(session_mock)
    await cleaner.clean_and_load(parse_result, "abc12345", "sales", "replace")

    drop_sqls = [s for s in executed_sqls if "DROP TABLE" in s.upper()]
    create_sqls = [s for s in executed_sqls if "CREATE TABLE" in s.upper() and "DROP" not in s.upper()]

    assert len(drop_sqls) >= 1, "replace 模式必须有 DROP TABLE IF EXISTS"
    assert len(create_sqls) >= 1, "replace 模式必须有 CREATE TABLE"

    # DROP 必须在 CREATE 之前
    first_drop_idx = min(executed_sqls.index(s) for s in drop_sqls)
    first_create_idx = min(executed_sqls.index(s) for s in create_sqls)
    assert first_drop_idx < first_create_idx


# ---------------------------------------------------------------------------
# append 模式：CREATE IF NOT EXISTS SQL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_mode_create_if_not_exists() -> None:
    """append 模式应执行 CREATE TABLE IF NOT EXISTS，不执行 DROP TABLE"""
    df = pd.DataFrame({"col_a": ["v1"]})
    fields = [_make_parsed_field("col_a", "text")]
    parse_result = _make_parse_result(df, fields)

    executed_sqls: list[str] = []

    async def mock_execute(stmt: object, params: dict | None = None) -> None:
        executed_sqls.append(str(stmt))

    session_mock = AsyncMock()
    session_mock.execute = mock_execute
    session_mock.commit = AsyncMock()
    session_mock.rollback = AsyncMock()

    cleaner, _ = _make_cleaner(session_mock)
    await cleaner.clean_and_load(parse_result, "abc12345", "sales", "append")

    drop_sqls = [s for s in executed_sqls if "DROP TABLE" in s.upper()]
    create_if_sqls = [
        s for s in executed_sqls if "CREATE TABLE IF NOT EXISTS" in s.upper()
    ]

    assert len(drop_sqls) == 0, "append 模式不应执行 DROP TABLE"
    assert len(create_if_sqls) >= 1, "append 模式必须有 CREATE TABLE IF NOT EXISTS"


# ---------------------------------------------------------------------------
# 非法 update_mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_update_mode_raises() -> None:
    """非法 update_mode 应抛出 FieldMappingError"""
    df = pd.DataFrame({"col": ["val"]})
    fields = [_make_parsed_field("col", "text")]
    parse_result = _make_parse_result(df, fields)

    cleaner, _ = _make_cleaner()
    with pytest.raises(FieldMappingError):
        await cleaner.clean_and_load(parse_result, "abc12345", "sales", "invalid_mode")


# ---------------------------------------------------------------------------
# 边界：清洗后无有效行
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_rows_empty_after_cleaning() -> None:
    """清洗后若所有行均为空，rows_written 应为 0，不执行 INSERT"""
    # 只含全 NaN 行的 DataFrame
    df = pd.DataFrame({"name": [None], "score": [None]})
    fields = [
        _make_parsed_field("name", "text"),
        _make_parsed_field("score", "numeric"),
    ]
    parse_result = _make_parse_result(df, fields)

    executed_sqls: list[str] = []

    async def mock_execute(stmt: object, params: dict | None = None) -> None:
        executed_sqls.append(str(stmt))

    session_mock = AsyncMock()
    session_mock.execute = mock_execute
    session_mock.commit = AsyncMock()
    session_mock.rollback = AsyncMock()

    cleaner, _ = _make_cleaner(session_mock)
    result = await cleaner.clean_and_load(parse_result, "abc12345", "sales", "replace")

    assert result.rows_written == 0
    insert_sqls = [s for s in executed_sqls if "INSERT" in s.upper()]
    assert len(insert_sqls) == 0


# ---------------------------------------------------------------------------
# _compute_row_hash 辅助测试
# ---------------------------------------------------------------------------


def test_compute_row_hash_deterministic() -> None:
    """相同内容的行应产生相同的哈希"""
    row = pd.Series({"name": "Alice", "score": 90.0})
    assert _compute_row_hash(row) == _compute_row_hash(row)


def test_compute_row_hash_different_rows() -> None:
    """不同内容的行应产生不同的哈希"""
    row1 = pd.Series({"name": "Alice", "score": 90.0})
    row2 = pd.Series({"name": "Bob", "score": 85.0})
    assert _compute_row_hash(row1) != _compute_row_hash(row2)
