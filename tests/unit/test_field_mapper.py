"""
单元测试：app/core/field_mapper.py

覆盖率目标：≥ 90%

测试场景：
- 精确匹配（raw_name 与 std_name 完全一致）返回 "exact" source
- Embedding 匹配（mock 返回高置信度）返回 "embedding" source
- LLM fallback（embedding 低置信度）调用 LLM 并返回 "llm" source
- 所有策略失败时返回原始名称，source="raw"，confidence=0.5
- needs_confirmation() 正确过滤低置信度字段
- dictionary_manager=None 时降级为精确匹配+原始名称
- LLM 响应 JSON 解析失败时降级
- embedding 匹配抛出异常时降级
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

# 设置必要的环境变量（在导入 app 模块前）
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("META_DB_USER", "test_user")
os.environ.setdefault("META_DB_PASSWORD", "test_pass")
os.environ.setdefault("BIZ_DB_USER", "test_user")
os.environ.setdefault("BIZ_DB_PASSWORD", "test_pass")
os.environ.setdefault("REDIS_PASSWORD", "")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-32-chars-xxxxxxxxx")

import pandas as pd

from app.core.excel_parser import ExcelParseResult, ParsedField
from app.core.field_mapper import (
    FieldMapper,
    MappingCandidate,
    _extract_json,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_parsed_field(
    raw_name: str,
    clean_name: str | None = None,
    inferred_type: str = "text",
    sample_values: list | None = None,
    unit: str | None = None,
) -> ParsedField:
    return ParsedField(
        raw_name=raw_name,
        clean_name=clean_name or raw_name,
        unit=unit,
        inferred_type=inferred_type,
        sample_values=sample_values or ["样本1", "样本2"],
        null_ratio=0.0,
    )


def _make_parse_result(fields: list[ParsedField]) -> ExcelParseResult:
    df = pd.DataFrame({f.clean_name: [] for f in fields})
    return ExcelParseResult(
        df=df,
        fields=fields,
        header_row_index=0,
        sheet_name="Sheet1",
        total_rows=0,
        warnings=[],
        source_filename="test.xlsx",
    )


def _make_dict_manager(search_results: list[dict] | None = None) -> AsyncMock:
    """构造返回固定搜索结果的 dictionary_manager mock"""
    mgr = AsyncMock()
    mgr.search_fields = AsyncMock(return_value=search_results or [])
    return mgr


def _make_llm_router(response_content: str = "") -> AsyncMock:
    """构造返回固定内容的 llm_router mock"""
    router = AsyncMock()
    response_mock = MagicMock()
    response_mock.content = response_content
    router.complete = AsyncMock(return_value=response_mock)
    return router


# ---------------------------------------------------------------------------
# 策略 1：精确匹配
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exact_match_by_std_name() -> None:
    """raw_name 与 std_name 完全一致时应返回 'exact' source，置信度 1.0"""
    field = _make_parsed_field("sales_revenue")
    parse_result = _make_parse_result([field])

    dict_entries = [
        {
            "std_name": "sales_revenue",
            "display_name": "销售收入",
            "domain": "sales",
            "synonyms": [],
            "unit": "万元",
            "similarity": 0.95,
        }
    ]
    mgr = _make_dict_manager(dict_entries)
    mapper = FieldMapper(dictionary_manager=mgr, llm_router=None)
    candidates = await mapper.map_fields(parse_result, "sales")

    assert len(candidates) == 1
    c = candidates[0]
    assert c.mapping_source == "exact"
    assert c.std_name == "sales_revenue"
    assert c.confidence == 1.0
    assert c.display_name == "销售收入"
    assert c.unit == "万元"


@pytest.mark.asyncio
async def test_exact_match_by_display_name() -> None:
    """raw_name 与 display_name 完全一致时应返回 'exact' source"""
    field = _make_parsed_field("销售收入")
    parse_result = _make_parse_result([field])

    dict_entries = [
        {
            "std_name": "sales_revenue",
            "display_name": "销售收入",
            "domain": "sales",
            "synonyms": [],
            "unit": "万元",
            "similarity": 0.90,
        }
    ]
    mgr = _make_dict_manager(dict_entries)
    mapper = FieldMapper(dictionary_manager=mgr, llm_router=None)
    candidates = await mapper.map_fields(parse_result, "sales")

    assert candidates[0].mapping_source == "exact"
    assert candidates[0].std_name == "sales_revenue"


@pytest.mark.asyncio
async def test_exact_match_by_synonym() -> None:
    """raw_name 与 synonyms 中某项完全一致时应返回 'exact' source"""
    field = _make_parsed_field("收入金额")
    parse_result = _make_parse_result([field])

    dict_entries = [
        {
            "std_name": "sales_revenue",
            "display_name": "销售收入",
            "domain": "sales",
            "synonyms": ["收入金额", "销售额"],
            "unit": "万元",
            "similarity": 0.88,
        }
    ]
    mgr = _make_dict_manager(dict_entries)
    mapper = FieldMapper(dictionary_manager=mgr, llm_router=None)
    candidates = await mapper.map_fields(parse_result, "sales")

    assert candidates[0].mapping_source == "exact"
    assert candidates[0].std_name == "sales_revenue"


# ---------------------------------------------------------------------------
# 策略 2：Embedding 语义匹配
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embedding_match_high_confidence() -> None:
    """Embedding 返回高置信度（≥ 0.7）时应返回 'embedding' source"""
    field = _make_parsed_field("sales_amount")
    parse_result = _make_parse_result([field])

    # dictionary_manager.search_fields 返回空列表（精确匹配失败），
    # 第二次调用（embedding 匹配）返回高相似度结果
    call_count = 0

    async def mock_search(query: str, domain: str, top_k: int = 1) -> list[dict]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # 第一次：精确匹配时调用 search_fields 加载字典条目（返回空 → 精确匹配失败）
            return []
        # 第二次：embedding 匹配
        return [
            {
                "std_name": "sales_revenue",
                "display_name": "销售收入",
                "domain": "sales",
                "unit": "万元",
                "similarity": 0.85,
            }
        ]

    mgr = AsyncMock()
    mgr.search_fields = mock_search

    mapper = FieldMapper(dictionary_manager=mgr, llm_router=None)
    candidates = await mapper.map_fields(parse_result, "sales")

    assert candidates[0].mapping_source == "embedding"
    assert candidates[0].std_name == "sales_revenue"
    assert candidates[0].confidence == 0.85


@pytest.mark.asyncio
async def test_embedding_match_low_confidence_falls_through() -> None:
    """Embedding 返回低置信度（< 0.7）时应继续尝试下一策略"""
    field = _make_parsed_field("unknown_field")
    parse_result = _make_parse_result([field])

    # 精确匹配和 embedding 均无法匹配
    async def mock_search(query: str, domain: str, top_k: int = 1) -> list[dict]:
        return [
            {
                "std_name": "some_field",
                "display_name": "某字段",
                "similarity": 0.50,  # < 0.7 阈值
            }
        ]

    mgr = AsyncMock()
    mgr.search_fields = mock_search

    # 无 LLM，最终降级为原始名称
    mapper = FieldMapper(dictionary_manager=mgr, llm_router=None)
    candidates = await mapper.map_fields(parse_result, "sales")

    assert candidates[0].mapping_source == "raw"
    assert candidates[0].confidence == 0.5


@pytest.mark.asyncio
async def test_embedding_exception_falls_through() -> None:
    """Embedding 抛出异常时应降级为下一策略，不崩溃"""
    field = _make_parsed_field("exc_field")
    parse_result = _make_parse_result([field])

    call_count = 0

    async def mock_search(query: str, domain: str, top_k: int = 1) -> list[dict]:
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise RuntimeError("ChromaDB connection error")
        return []

    mgr = AsyncMock()
    mgr.search_fields = mock_search

    mapper = FieldMapper(dictionary_manager=mgr, llm_router=None)
    candidates = await mapper.map_fields(parse_result, "sales")

    # 降级为原始名称
    assert candidates[0].mapping_source == "raw"


# ---------------------------------------------------------------------------
# 策略 3：LLM fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_fallback_called_when_embedding_low() -> None:
    """Embedding 低置信度时应调用 LLM，并返回 'llm' source"""
    field = _make_parsed_field("未知字段A")
    parse_result = _make_parse_result([field])

    async def mock_search(query: str, domain: str, top_k: int = 1) -> list[dict]:
        return [{"std_name": "x", "display_name": "x", "similarity": 0.40}]

    mgr = AsyncMock()
    mgr.search_fields = mock_search

    llm_json = json.dumps(
        {"std_name": "production_qty", "display_name": "产量", "confidence": 0.75}
    )
    llm_router = _make_llm_router(llm_json)

    mapper = FieldMapper(dictionary_manager=mgr, llm_router=llm_router)
    candidates = await mapper.map_fields(parse_result, "production")

    llm_router.complete.assert_called_once()
    assert candidates[0].mapping_source == "llm"
    assert candidates[0].std_name == "production_qty"
    assert candidates[0].confidence == 0.75


@pytest.mark.asyncio
async def test_llm_json_parse_failure_falls_through() -> None:
    """LLM 返回无法解析的 JSON 时应降级为原始名称"""
    field = _make_parsed_field("bad_llm_field")
    parse_result = _make_parse_result([field])

    async def mock_search(query: str, domain: str, top_k: int = 1) -> list[dict]:
        return []

    mgr = AsyncMock()
    mgr.search_fields = mock_search

    llm_router = _make_llm_router("这不是JSON内容，模型输出了奇怪的东西")
    mapper = FieldMapper(dictionary_manager=mgr, llm_router=llm_router)
    candidates = await mapper.map_fields(parse_result, "sales")

    assert candidates[0].mapping_source == "raw"


@pytest.mark.asyncio
async def test_llm_exception_falls_through() -> None:
    """LLM 抛出异常时应降级为原始名称，不崩溃"""
    field = _make_parsed_field("error_field")
    parse_result = _make_parse_result([field])

    async def mock_search(query: str, domain: str, top_k: int = 1) -> list[dict]:
        return []

    mgr = AsyncMock()
    mgr.search_fields = mock_search

    llm_router = AsyncMock()
    llm_router.complete = AsyncMock(side_effect=RuntimeError("LLM timeout"))

    mapper = FieldMapper(dictionary_manager=mgr, llm_router=llm_router)
    candidates = await mapper.map_fields(parse_result, "sales")

    assert candidates[0].mapping_source == "raw"
    assert candidates[0].confidence == 0.5


@pytest.mark.asyncio
async def test_llm_returns_empty_std_name_falls_through() -> None:
    """LLM 返回空 std_name 时应降级为原始名称"""
    field = _make_parsed_field("empty_field")
    parse_result = _make_parse_result([field])

    async def mock_search(query: str, domain: str, top_k: int = 1) -> list[dict]:
        return []

    mgr = AsyncMock()
    mgr.search_fields = mock_search

    # LLM 返回 std_name 为空字符串
    llm_json = json.dumps({"std_name": "", "display_name": "某字段", "confidence": 0.7})
    llm_router = _make_llm_router(llm_json)

    mapper = FieldMapper(dictionary_manager=mgr, llm_router=llm_router)
    candidates = await mapper.map_fields(parse_result, "sales")

    assert candidates[0].mapping_source == "raw"


# ---------------------------------------------------------------------------
# 策略 4：原始名称降级
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_raw_fallback_all_strategies_fail() -> None:
    """所有策略均失败时，source='raw'，confidence=0.5，std_name=clean_name"""
    field = _make_parsed_field("totally_unknown", "totally_clean")
    parse_result = _make_parse_result([field])

    async def mock_search(query: str, domain: str, top_k: int = 1) -> list[dict]:
        return []

    mgr = AsyncMock()
    mgr.search_fields = mock_search

    llm_router = _make_llm_router("{}")  # 空 JSON，std_name 为空
    mapper = FieldMapper(dictionary_manager=mgr, llm_router=llm_router)
    candidates = await mapper.map_fields(parse_result, "sales")

    assert candidates[0].mapping_source == "raw"
    assert candidates[0].confidence == 0.5
    assert candidates[0].std_name == "totally_clean"
    assert candidates[0].raw_name == "totally_unknown"


@pytest.mark.asyncio
async def test_raw_fallback_preserves_unit() -> None:
    """原始名称降级时，unit 应从 ParsedField.unit 继承"""
    field = _make_parsed_field("revenue", unit="万元")
    parse_result = _make_parse_result([field])

    mapper = FieldMapper(dictionary_manager=None, llm_router=None)
    candidates = await mapper.map_fields(parse_result, "sales")

    assert candidates[0].mapping_source == "raw"
    assert candidates[0].unit == "万元"


# ---------------------------------------------------------------------------
# needs_confirmation()
# ---------------------------------------------------------------------------


def test_needs_confirmation_filters_low_confidence() -> None:
    """needs_confirmation 应只返回置信度低于阈值（0.70）的候选"""
    mapper = FieldMapper(dictionary_manager=None, llm_router=None)

    candidates = [
        MappingCandidate("f1", "std_f1", "展示f1", 1.0, "exact", None),
        MappingCandidate("f2", "std_f2", "展示f2", 0.85, "embedding", None),
        MappingCandidate("f3", "std_f3", "展示f3", 0.65, "llm", None),
        MappingCandidate("f4", "std_f4", "展示f4", 0.50, "raw", None),
    ]

    to_confirm = mapper.needs_confirmation(candidates)
    # 0.65 和 0.5 均低于 0.70
    assert len(to_confirm) == 2
    assert all(c.confidence < 0.70 for c in to_confirm)


def test_needs_confirmation_empty_list() -> None:
    """空候选列表时应返回空列表"""
    mapper = FieldMapper(dictionary_manager=None, llm_router=None)
    assert mapper.needs_confirmation([]) == []


def test_needs_confirmation_all_high_confidence() -> None:
    """所有候选置信度均 ≥ 0.70 时应返回空列表"""
    mapper = FieldMapper(dictionary_manager=None, llm_router=None)
    candidates = [
        MappingCandidate("f1", "std_f1", "展示f1", 0.70, "embedding", None),
        MappingCandidate("f2", "std_f2", "展示f2", 1.0, "exact", None),
    ]
    assert mapper.needs_confirmation(candidates) == []


# ---------------------------------------------------------------------------
# dictionary_manager=None 降级行为
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_dict_manager_uses_raw_fallback() -> None:
    """dictionary_manager=None 时跳过精确匹配和 embedding，直接降级为原始名称"""
    field = _make_parsed_field("some_field")
    parse_result = _make_parse_result([field])

    mapper = FieldMapper(dictionary_manager=None, llm_router=None)
    candidates = await mapper.map_fields(parse_result, "sales")

    assert candidates[0].mapping_source == "raw"
    assert candidates[0].std_name == "some_field"
    assert candidates[0].confidence == 0.5


@pytest.mark.asyncio
async def test_no_dict_manager_with_llm_router() -> None:
    """dictionary_manager=None 但有 llm_router 时，应调用 LLM 策略"""
    field = _make_parsed_field("qty_col")
    parse_result = _make_parse_result([field])

    llm_json = json.dumps(
        {"std_name": "production_qty", "display_name": "产量", "confidence": 0.80}
    )
    llm_router = _make_llm_router(llm_json)

    mapper = FieldMapper(dictionary_manager=None, llm_router=llm_router)
    candidates = await mapper.map_fields(parse_result, "production")

    # 无 dict_manager → 精确匹配跳过 → embedding 跳过 → LLM 调用
    llm_router.complete.assert_called_once()
    assert candidates[0].mapping_source == "llm"
    assert candidates[0].std_name == "production_qty"


# ---------------------------------------------------------------------------
# map_fields：空字段列表
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_map_fields_empty_fields() -> None:
    """parse_result.fields 为空时应返回空列表"""
    parse_result = _make_parse_result([])
    mapper = FieldMapper(dictionary_manager=None, llm_router=None)
    candidates = await mapper.map_fields(parse_result, "sales")
    assert candidates == []


# ---------------------------------------------------------------------------
# 多字段映射
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_fields_independent_mapping() -> None:
    """多个字段应各自独立映射，顺序与 fields 一致"""
    fields = [
        _make_parsed_field("销售收入"),
        _make_parsed_field("unknown_xyz"),
        _make_parsed_field("product_name"),
    ]
    parse_result = _make_parse_result(fields)

    dict_entries = [
        {
            "std_name": "sales_revenue",
            "display_name": "销售收入",
            "domain": "sales",
            "synonyms": [],
            "unit": "万元",
            "similarity": 0.99,
        }
    ]
    mgr = _make_dict_manager(dict_entries)
    mapper = FieldMapper(dictionary_manager=mgr, llm_router=None)
    candidates = await mapper.map_fields(parse_result, "sales")

    assert len(candidates) == 3
    # 第一个字段精确匹配
    assert candidates[0].mapping_source == "exact"
    assert candidates[0].std_name == "sales_revenue"
    # 后两个字段降级（embedding 返回空 → LLM 无 → raw）
    assert candidates[1].mapping_source in ("raw", "embedding", "llm")
    assert candidates[2].mapping_source in ("raw", "embedding", "llm")


# ---------------------------------------------------------------------------
# _extract_json 辅助函数测试
# ---------------------------------------------------------------------------


def test_extract_json_plain() -> None:
    """纯 JSON 字符串应原样提取"""
    text = '{"std_name": "revenue", "confidence": 0.9}'
    assert _extract_json(text) == text


def test_extract_json_markdown_code_block() -> None:
    """包裹在 markdown 代码块中的 JSON 应正确提取"""
    text = '```json\n{"std_name": "revenue", "confidence": 0.9}\n```'
    result = _extract_json(text)
    parsed = json.loads(result)
    assert parsed["std_name"] == "revenue"


def test_extract_json_with_prefix_text() -> None:
    """JSON 前有前缀文本时应提取 {} 部分"""
    text = '下面是结果：{"std_name": "qty", "confidence": 0.7}'
    result = _extract_json(text)
    parsed = json.loads(result)
    assert parsed["std_name"] == "qty"


def test_extract_json_no_braces() -> None:
    """无 {} 时应返回原始文本（后续解析会失败，此处不崩溃）"""
    text = "没有任何JSON内容"
    result = _extract_json(text)
    assert result == text


# ---------------------------------------------------------------------------
# LLM 响应 JSON 含 markdown 代码块
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_response_with_markdown_code_block() -> None:
    """LLM 返回包含 markdown 代码块的 JSON 时应正确解析"""
    field = _make_parsed_field("月销售量")
    parse_result = _make_parse_result([field])

    async def mock_search(query: str, domain: str, top_k: int = 1) -> list[dict]:
        return []

    mgr = AsyncMock()
    mgr.search_fields = mock_search

    llm_content = (
        "```json\n"
        '{"std_name": "monthly_sales_qty", "display_name": "月销售量", "confidence": 0.82}\n'
        "```"
    )
    llm_router = _make_llm_router(llm_content)
    mapper = FieldMapper(dictionary_manager=mgr, llm_router=llm_router)
    candidates = await mapper.map_fields(parse_result, "sales")

    assert candidates[0].mapping_source == "llm"
    assert candidates[0].std_name == "monthly_sales_qty"
    assert candidates[0].confidence == 0.82
