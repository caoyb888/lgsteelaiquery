"""
单元测试：text_to_sql 相关模块

覆盖目标：≥ 85%
涵盖模块：
  - app/core/text_to_sql.py   (TextToSQLEngine, _extract_sql)
  - app/core/prompt_builder.py (PromptBuilder)
  - app/core/nlg.py           (NLGService)
  - app/security/desensitize.py (Desensitizer)
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.nlg import NLGResult, NLGService
from app.core.prompt_builder import PromptBuilder, PromptContext
from app.core.text_to_sql import SQLGenerationResult, TextToSQLEngine, _extract_sql
from app.knowledge.dictionary import SchemaContext
from app.security.desensitize import Desensitizer
from app.utils.exceptions import SQLGenerationError, SQLSafetyViolationError


# ---------------------------------------------------------------------------
# 共用 Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def desensitizer() -> Desensitizer:
    return Desensitizer()


@pytest.fixture()
def prompt_builder() -> PromptBuilder:
    return PromptBuilder()


@pytest.fixture()
def mock_llm_router() -> MagicMock:
    router = MagicMock()
    router.complete = AsyncMock()
    return router


@pytest.fixture()
def mock_sql_validator() -> MagicMock:
    validator = MagicMock()
    validator.validate = MagicMock(return_value=None)
    return validator


@pytest.fixture()
def mock_qa_cache() -> MagicMock:
    cache = MagicMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock(return_value=None)
    return cache


@pytest.fixture()
def mock_dictionary_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.get_schema_context = AsyncMock(return_value=SchemaContext())
    return mgr


@pytest.fixture()
def fake_llm_response() -> MagicMock:
    resp = MagicMock()
    resp.content = "SELECT * FROM sales_table WHERE report_month = '2026-03'"
    resp.model = "qwen-max"
    resp.prompt_tokens = 100
    resp.completion_tokens = 50
    return resp


@pytest.fixture()
def allowed_tables() -> set[str]:
    return {"sales_table", "finance_table"}


@pytest.fixture()
def base_ctx(prompt_builder: PromptBuilder) -> PromptContext:
    return PromptContext(
        question="查询本月销售额",
        domain="sales",
        schema_context=SchemaContext(),
        conversation_history=[],
        user_role="analyst",
    )


def _make_engine(
    llm_router: Any,
    sql_validator: Any,
    desensitizer: Any,
    dictionary_manager: Any = None,
    qa_cache: Any = None,
) -> TextToSQLEngine:
    from app.config import Settings

    settings = Settings(
        max_sql_retry=3,
        llm_max_tokens_per_request=2000,
    )
    return TextToSQLEngine(
        llm_router=llm_router,
        prompt_builder=PromptBuilder(),
        sql_validator=sql_validator,
        desensitizer=desensitizer,
        dictionary_manager=dictionary_manager,
        qa_cache=qa_cache,
        settings=settings,
    )


# ===========================================================================
# _extract_sql
# ===========================================================================


class TestExtractSQL:
    def test_strips_sql_code_block(self) -> None:
        raw = "```sql\nSELECT * FROM sales\n```"
        result = _extract_sql(raw)
        assert result == "SELECT * FROM sales"

    def test_strips_generic_code_block(self) -> None:
        raw = "```\nSELECT id FROM users\n```"
        result = _extract_sql(raw)
        assert result == "SELECT id FROM users"

    def test_plain_select_returned_as_is(self) -> None:
        sql = "SELECT COUNT(*) FROM orders"
        assert _extract_sql(sql) == sql

    def test_select_case_insensitive(self) -> None:
        sql = "select * from table1"
        result = _extract_sql(sql)
        assert result == sql

    def test_strips_leading_trailing_whitespace(self) -> None:
        raw = "   SELECT 1   "
        assert _extract_sql(raw) == "SELECT 1"

    def test_non_select_raises(self) -> None:
        with pytest.raises(SQLGenerationError, match="不以 SELECT 开头"):
            _extract_sql("INSERT INTO t VALUES (1)")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(SQLGenerationError):
            _extract_sql("")

    def test_code_block_with_non_select_raises(self) -> None:
        raw = "```sql\nDROP TABLE users\n```"
        with pytest.raises(SQLGenerationError, match="不以 SELECT 开头"):
            _extract_sql(raw)


# ===========================================================================
# TextToSQLEngine
# ===========================================================================


class TestTextToSQLEngine:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_sql(
        self,
        mock_llm_router: MagicMock,
        mock_sql_validator: MagicMock,
        desensitizer: Desensitizer,
        mock_qa_cache: MagicMock,
        allowed_tables: set[str],
    ) -> None:
        """缓存命中时直接返回，不调用 LLM"""
        mock_qa_cache.get = AsyncMock(
            return_value={"sql": "SELECT 1", "model_used": "cache"}
        )
        engine = _make_engine(
            mock_llm_router, mock_sql_validator, desensitizer, qa_cache=mock_qa_cache
        )

        result = await engine.generate(
            question="test",
            domain="sales",
            allowed_tables=allowed_tables,
            conversation_history=[],
            user_role="analyst",
        )

        assert result.from_cache is True
        assert result.sql == "SELECT 1"
        assert result.model_used == "cache"
        mock_llm_router.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_first_attempt_success(
        self,
        mock_llm_router: MagicMock,
        mock_sql_validator: MagicMock,
        desensitizer: Desensitizer,
        mock_qa_cache: MagicMock,
        fake_llm_response: MagicMock,
        allowed_tables: set[str],
    ) -> None:
        """首次生成成功，SQL 校验通过"""
        mock_llm_router.complete = AsyncMock(return_value=fake_llm_response)
        engine = _make_engine(
            mock_llm_router, mock_sql_validator, desensitizer, qa_cache=mock_qa_cache
        )

        result = await engine.generate(
            question="查询本月销售额",
            domain="sales",
            allowed_tables=allowed_tables,
            conversation_history=[],
            user_role="analyst",
        )

        assert result.from_cache is False
        assert result.retry_count == 0
        assert result.sql.startswith("SELECT")
        assert result.model_used == "qwen-max"
        mock_llm_router.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_on_validation_failure_then_success(
        self,
        mock_llm_router: MagicMock,
        desensitizer: Desensitizer,
        mock_qa_cache: MagicMock,
        allowed_tables: set[str],
    ) -> None:
        """首次 SQL 校验失败 → 重试，第二次成功"""
        bad_response = MagicMock()
        bad_response.content = "SELECT * FROM sales_table"
        bad_response.model = "qwen-max"
        bad_response.prompt_tokens = 80
        bad_response.completion_tokens = 20

        good_response = MagicMock()
        good_response.content = "SELECT revenue FROM sales_table"
        good_response.model = "qwen-max"
        good_response.prompt_tokens = 100
        good_response.completion_tokens = 30

        mock_llm_router.complete = AsyncMock(
            side_effect=[bad_response, good_response]
        )

        # 第一次校验失败，第二次通过
        sql_validator = MagicMock()
        sql_validator.validate = MagicMock(
            side_effect=[SQLSafetyViolationError("未授权的表"), None]
        )

        engine = _make_engine(
            mock_llm_router, sql_validator, desensitizer, qa_cache=mock_qa_cache
        )

        result = await engine.generate(
            question="查询销售收入",
            domain="sales",
            allowed_tables=allowed_tables,
            conversation_history=[],
            user_role="analyst",
        )

        assert result.from_cache is False
        assert result.retry_count == 1
        assert mock_llm_router.complete.call_count == 2

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_raises(
        self,
        mock_llm_router: MagicMock,
        desensitizer: Desensitizer,
        mock_qa_cache: MagicMock,
        allowed_tables: set[str],
    ) -> None:
        """三次重试均失败 → 抛 SQLGenerationError"""
        response = MagicMock()
        response.content = "SELECT * FROM sales_table"
        response.model = "qwen-max"
        response.prompt_tokens = 80
        response.completion_tokens = 20

        mock_llm_router.complete = AsyncMock(return_value=response)

        sql_validator = MagicMock()
        sql_validator.validate = MagicMock(
            side_effect=SQLSafetyViolationError("未授权的表访问")
        )

        engine = _make_engine(
            mock_llm_router, sql_validator, desensitizer, qa_cache=mock_qa_cache
        )

        with pytest.raises(SQLGenerationError):
            await engine.generate(
                question="查询销售额",
                domain="sales",
                allowed_tables=allowed_tables,
                conversation_history=[],
                user_role="analyst",
            )

        assert mock_llm_router.complete.call_count == 3

    @pytest.mark.asyncio
    async def test_llm_failure_all_retries_raises(
        self,
        mock_llm_router: MagicMock,
        mock_sql_validator: MagicMock,
        desensitizer: Desensitizer,
        mock_qa_cache: MagicMock,
        allowed_tables: set[str],
    ) -> None:
        """LLM 调用每次均抛异常，三次后抛 SQLGenerationError"""
        from app.utils.exceptions import LLMAllFallbackExhaustedError

        mock_llm_router.complete = AsyncMock(
            side_effect=LLMAllFallbackExhaustedError("所有模型不可用")
        )
        engine = _make_engine(
            mock_llm_router, mock_sql_validator, desensitizer, qa_cache=mock_qa_cache
        )

        with pytest.raises(SQLGenerationError):
            await engine.generate(
                question="查询数据",
                domain="sales",
                allowed_tables=allowed_tables,
                conversation_history=[],
                user_role="analyst",
            )

    @pytest.mark.asyncio
    async def test_cache_write_failure_does_not_raise(
        self,
        mock_llm_router: MagicMock,
        mock_sql_validator: MagicMock,
        desensitizer: Desensitizer,
        fake_llm_response: MagicMock,
        allowed_tables: set[str],
    ) -> None:
        """缓存写入失败不影响正常流程"""
        mock_llm_router.complete = AsyncMock(return_value=fake_llm_response)

        cache = MagicMock()
        cache.get = AsyncMock(return_value=None)
        cache.set = AsyncMock(side_effect=Exception("Redis 连接失败"))

        engine = _make_engine(
            mock_llm_router, mock_sql_validator, desensitizer, qa_cache=cache
        )

        result = await engine.generate(
            question="查询数据",
            domain="sales",
            allowed_tables=allowed_tables,
            conversation_history=[],
            user_role="analyst",
        )
        assert result.sql.startswith("SELECT")

    @pytest.mark.asyncio
    async def test_no_cache_no_dictionary_manager(
        self,
        mock_llm_router: MagicMock,
        mock_sql_validator: MagicMock,
        desensitizer: Desensitizer,
        fake_llm_response: MagicMock,
        allowed_tables: set[str],
    ) -> None:
        """没有缓存和字典管理器时也能正常工作"""
        mock_llm_router.complete = AsyncMock(return_value=fake_llm_response)
        engine = _make_engine(
            mock_llm_router,
            mock_sql_validator,
            desensitizer,
            dictionary_manager=None,
            qa_cache=None,
        )

        result = await engine.generate(
            question="查询销售额",
            domain="sales",
            allowed_tables=allowed_tables,
            conversation_history=[],
            user_role="analyst",
        )
        assert result.from_cache is False


# ===========================================================================
# PromptBuilder
# ===========================================================================


class TestPromptBuilder:
    def _make_ctx(
        self,
        question: str = "查询本月收入",
        domain: str = "finance",
        history: list[dict] | None = None,
        schema: SchemaContext | None = None,
    ) -> PromptContext:
        return PromptContext(
            question=question,
            domain=domain,
            schema_context=schema or SchemaContext(),
            conversation_history=history or [],
            user_role="analyst",
        )

    def test_standard_prompt_contains_question(
        self, prompt_builder: PromptBuilder
    ) -> None:
        ctx = self._make_ctx(question="查询本月收入")
        prompt = prompt_builder.build_standard_prompt(ctx)
        assert "查询本月收入" in prompt

    def test_standard_prompt_contains_system_prompt(
        self, prompt_builder: PromptBuilder
    ) -> None:
        ctx = self._make_ctx()
        prompt = prompt_builder.build_standard_prompt(ctx)
        assert "SELECT" in prompt
        assert "莱钢" in prompt

    def test_standard_prompt_contains_history(
        self, prompt_builder: PromptBuilder
    ) -> None:
        history = [
            {"role": "user", "content": "上月收入多少"},
            {"role": "assistant", "content": "上月收入 5000 万"},
        ]
        ctx = self._make_ctx(history=history)
        prompt = prompt_builder.build_standard_prompt(ctx)
        assert "上月收入多少" in prompt
        assert "上月收入 5000 万" in prompt

    def test_standard_prompt_contains_schema_yaml(
        self, prompt_builder: PromptBuilder
    ) -> None:
        schema = SchemaContext(
            domain_schema_yaml="table: sales\ncolumns:\n  - revenue",
            matched_fields=[],
            few_shot_examples=[],
        )
        ctx = self._make_ctx(schema=schema)
        prompt = prompt_builder.build_standard_prompt(ctx)
        assert "table: sales" in prompt

    def test_standard_prompt_contains_few_shots(
        self, prompt_builder: PromptBuilder
    ) -> None:
        schema = SchemaContext(
            few_shot_examples=[
                {"question": "示例问题", "sql": "SELECT 1"},
            ]
        )
        ctx = self._make_ctx(schema=schema)
        prompt = prompt_builder.build_standard_prompt(ctx)
        assert "示例问题" in prompt
        assert "SELECT 1" in prompt

    def test_standard_prompt_contains_matched_fields(
        self, prompt_builder: PromptBuilder
    ) -> None:
        schema = SchemaContext(
            matched_fields=[
                {
                    "std_name": "revenue",
                    "display_name": "收入",
                    "unit": "万元",
                    "description": "销售收入",
                }
            ]
        )
        ctx = self._make_ctx(schema=schema)
        prompt = prompt_builder.build_standard_prompt(ctx)
        assert "revenue" in prompt

    def test_retry_prompt_contains_error_sql(
        self, prompt_builder: PromptBuilder
    ) -> None:
        ctx = self._make_ctx()
        error_sql = "SELECT * FROM unknown_table"
        error_msg = "未授权的表访问：unknown_table"
        prompt = prompt_builder.build_retry_prompt(ctx, error_sql, error_msg)
        assert error_sql in prompt
        assert error_msg in prompt

    def test_retry_prompt_contains_question(
        self, prompt_builder: PromptBuilder
    ) -> None:
        ctx = self._make_ctx(question="查询本月成本")
        prompt = prompt_builder.build_retry_prompt(
            ctx, "SELECT 1", "SQL 校验失败"
        )
        assert "查询本月成本" in prompt

    def test_retry_prompt_contains_correction_hint(
        self, prompt_builder: PromptBuilder
    ) -> None:
        ctx = self._make_ctx()
        prompt = prompt_builder.build_retry_prompt(
            ctx, "SELECT bad", "SQL 安全校验失败"
        )
        assert "请根据以上错误信息" in prompt or "重新生成" in prompt

    def test_empty_history_no_history_section(
        self, prompt_builder: PromptBuilder
    ) -> None:
        ctx = self._make_ctx(history=[])
        prompt = prompt_builder.build_standard_prompt(ctx)
        assert "对话历史" not in prompt

    def test_format_history_user_assistant_labels(
        self, prompt_builder: PromptBuilder
    ) -> None:
        history = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]
        result = prompt_builder._format_history(history)  # noqa: SLF001
        assert "用户" in result
        assert "助手" in result


# ===========================================================================
# NLGService._determine_display_type
# ===========================================================================


class TestNLGDisplayType:
    @pytest.fixture()
    def nlg(self) -> NLGService:
        return NLGService(llm_router=None)

    def test_single_value(self, nlg: NLGService) -> None:
        rows = [{"total": 12345}]
        display_type, title = nlg._determine_display_type(  # noqa: SLF001
            "本月总收入", ["total"], rows
        )
        assert display_type == "single_value"
        assert title is None

    def test_line_chart_trend_keyword(self, nlg: NLGService) -> None:
        rows = [{"report_month": "2026-01", "revenue": 100}] * 3
        display_type, title = nlg._determine_display_type(  # noqa: SLF001
            "近3个月收入趋势", ["report_month", "revenue"], rows
        )
        assert display_type == "line_chart"
        assert title is not None

    def test_line_chart_change_keyword(self, nlg: NLGService) -> None:
        rows = [{"date": "2026-01", "value": 1}] * 2
        display_type, _ = nlg._determine_display_type(  # noqa: SLF001
            "收入变化情况", ["date", "value"], rows
        )
        assert display_type == "line_chart"

    def test_line_chart_yoy_keyword(self, nlg: NLGService) -> None:
        rows = [{"report_month": "2026-01", "v": 1}] * 2
        display_type, _ = nlg._determine_display_type(  # noqa: SLF001
            "同比增长分析", ["report_month", "v"], rows
        )
        assert display_type == "line_chart"

    def test_pie_chart_ratio_keyword(self, nlg: NLGService) -> None:
        rows = [{"product": "A", "ratio": 0.3}] * 3
        display_type, title = nlg._determine_display_type(  # noqa: SLF001
            "各产品线占比", ["product", "ratio"], rows
        )
        assert display_type == "pie_chart"
        assert title is not None

    def test_pie_chart_distribution_keyword(self, nlg: NLGService) -> None:
        rows = [{"region": "A", "cnt": 10}] * 5
        display_type, _ = nlg._determine_display_type(  # noqa: SLF001
            "销售分布情况", ["region", "cnt"], rows
        )
        assert display_type == "pie_chart"

    def test_bar_chart_comparison_keyword(self, nlg: NLGService) -> None:
        rows = [{"dept": "A", "revenue": 100}] * 5
        display_type, title = nlg._determine_display_type(  # noqa: SLF001
            "各部门收入对比", ["dept", "revenue"], rows
        )
        assert display_type == "bar_chart"
        assert title is not None

    def test_bar_chart_rank_keyword(self, nlg: NLGService) -> None:
        rows = [{"product": "A", "sales": 99}] * 5
        display_type, _ = nlg._determine_display_type(  # noqa: SLF001
            "销售排名前10产品", ["product", "sales"], rows
        )
        assert display_type == "bar_chart"

    def test_default_table(self, nlg: NLGService) -> None:
        rows = [{"a": 1, "b": 2}] * 20
        display_type, title = nlg._determine_display_type(  # noqa: SLF001
            "查询所有订单", ["a", "b"], rows
        )
        assert display_type == "table"
        assert title is None

    def test_empty_rows_default_table(self, nlg: NLGService) -> None:
        display_type, title = nlg._determine_display_type(  # noqa: SLF001
            "查询记录", ["col1"], []
        )
        assert display_type == "table"
        assert title is None

    @pytest.mark.asyncio
    async def test_generate_summary_template_when_no_llm(self) -> None:
        """llm_router=None 时使用模板摘要"""
        nlg = NLGService(llm_router=None)
        result = await nlg.generate_summary(
            question="查询收入",
            sql="SELECT revenue FROM sales_table",
            rows=[{"revenue": 5000}],
            column_names=["revenue"],
        )
        assert isinstance(result, NLGResult)
        assert len(result.summary) > 0

    @pytest.mark.asyncio
    async def test_generate_summary_empty_rows_template(self) -> None:
        """空结果时模板摘要提示未找到数据"""
        nlg = NLGService(llm_router=None)
        result = await nlg.generate_summary(
            question="查询不存在的数据",
            sql="SELECT * FROM sales_table WHERE 1=0",
            rows=[],
            column_names=["id"],
        )
        assert "未找到" in result.summary

    @pytest.mark.asyncio
    async def test_generate_summary_llm_success(
        self, mock_llm_router: MagicMock
    ) -> None:
        """llm_router 可用时调用 LLM 生成摘要"""
        llm_response = MagicMock()
        llm_response.content = "本月销售收入为 5000 万元，同比增长 10%。"
        mock_llm_router.complete = AsyncMock(return_value=llm_response)

        nlg = NLGService(llm_router=mock_llm_router)
        result = await nlg.generate_summary(
            question="本月销售收入",
            sql="SELECT SUM(revenue) FROM sales_table",
            rows=[{"sum": 5000}],
            column_names=["sum"],
        )
        assert "5000" in result.summary
        mock_llm_router.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_summary_llm_fallback_on_error(
        self, mock_llm_router: MagicMock
    ) -> None:
        """LLM 调用失败时降级为模板摘要"""
        mock_llm_router.complete = AsyncMock(side_effect=Exception("LLM 不可用"))
        nlg = NLGService(llm_router=mock_llm_router)

        result = await nlg.generate_summary(
            question="查询收入",
            sql="SELECT revenue FROM t",
            rows=[{"revenue": 100}, {"revenue": 200}],
            column_names=["revenue"],
        )
        # 降级后仍返回有效摘要
        assert isinstance(result, NLGResult)
        assert len(result.summary) > 0


# ===========================================================================
# Desensitizer
# ===========================================================================


class TestDesensitizer:
    def test_validate_prompt_passes_clean_prompt(
        self, desensitizer: Desensitizer
    ) -> None:
        """干净的 Prompt 不抛异常"""
        desensitizer.validate_prompt(
            "请根据表结构生成 SQL：表名 sales，字段 revenue（万元）。"
        )

    def test_validate_prompt_detects_phone_number(
        self, desensitizer: Desensitizer
    ) -> None:
        """包含 11 位手机号时抛 SQLGenerationError"""
        with pytest.raises(SQLGenerationError, match="敏感信息"):
            desensitizer.validate_prompt("联系电话：13812345678，请查询。")

    def test_validate_prompt_detects_id_card(
        self, desensitizer: Desensitizer
    ) -> None:
        """包含 18 位身份证号时抛 SQLGenerationError"""
        with pytest.raises(SQLGenerationError, match="敏感信息"):
            desensitizer.validate_prompt("身份证：370102199001011234 请查询。")

    def test_validate_prompt_detects_password_keyword(
        self, desensitizer: Desensitizer
    ) -> None:
        """包含 password 字样时抛 SQLGenerationError"""
        with pytest.raises(SQLGenerationError, match="敏感信息"):
            desensitizer.validate_prompt("password=abc123 is the secret.")

    def test_validate_prompt_detects_api_key_keyword(
        self, desensitizer: Desensitizer
    ) -> None:
        """包含 api_key 字样时抛 SQLGenerationError"""
        with pytest.raises(SQLGenerationError, match="敏感信息"):
            desensitizer.validate_prompt("api_key=sk-xxxx123456")

    def test_validate_prompt_detects_passwd(
        self, desensitizer: Desensitizer
    ) -> None:
        """包含 passwd 字样时抛 SQLGenerationError"""
        with pytest.raises(SQLGenerationError, match="敏感信息"):
            desensitizer.validate_prompt("passwd: toosimple")

    def test_validate_prompt_detects_secret(
        self, desensitizer: Desensitizer
    ) -> None:
        """包含 secret 字样时抛 SQLGenerationError"""
        with pytest.raises(SQLGenerationError, match="敏感信息"):
            desensitizer.validate_prompt("SECRET=abcdef")

    def test_clean_question_replaces_phone(
        self, desensitizer: Desensitizer
    ) -> None:
        """手机号被替换为 [REDACTED]"""
        q = "联系 13812345678 查询数据"
        result = desensitizer.clean_question(q)
        assert "13812345678" not in result
        assert "[REDACTED]" in result

    def test_clean_question_replaces_id_card(
        self, desensitizer: Desensitizer
    ) -> None:
        """18 位身份证被替换为 [REDACTED]"""
        q = "查询身份证 370102199001011234 的记录"
        result = desensitizer.clean_question(q)
        assert "370102199001011234" not in result
        assert "[REDACTED]" in result

    def test_clean_question_no_sensitive_data_unchanged(
        self, desensitizer: Desensitizer
    ) -> None:
        """不含敏感数据的问题保持不变"""
        q = "查询本月各部门收入汇总"
        assert desensitizer.clean_question(q) == q

    def test_get_safe_schema_basic(self, desensitizer: Desensitizer) -> None:
        """生成的 Schema 包含表名和字段名"""
        fields = [
            {"name": "revenue", "type": "numeric", "unit": "万元", "description": "销售收入"},
            {"name": "cost", "type": "numeric", "unit": "万元", "description": "销售成本"},
        ]
        schema = desensitizer.get_safe_schema("sales_table", fields)
        assert "sales_table" in schema
        assert "revenue" in schema
        assert "cost" in schema

    def test_get_safe_schema_contains_type_and_unit(
        self, desensitizer: Desensitizer
    ) -> None:
        """生成的 Schema 包含字段类型和单位"""
        fields = [{"name": "amount", "type": "numeric", "unit": "元", "description": "金额"}]
        schema = desensitizer.get_safe_schema("t", fields)
        assert "numeric" in schema
        assert "单位：元" in schema

    def test_get_safe_schema_no_real_values(
        self, desensitizer: Desensitizer
    ) -> None:
        """生成的 Schema 不包含真实数据值"""
        real_value = "9999999.99"
        fields = [
            {"name": "revenue", "type": "numeric", "unit": "", "description": "收入"}
        ]
        schema = desensitizer.get_safe_schema("sales", fields)
        assert real_value not in schema

    def test_get_safe_schema_empty_fields(
        self, desensitizer: Desensitizer
    ) -> None:
        """字段列表为空时仍能生成合法 Schema"""
        schema = desensitizer.get_safe_schema("empty_table", [])
        assert "empty_table" in schema
        assert "字段" in schema

    def test_get_safe_schema_missing_optional_keys(
        self, desensitizer: Desensitizer
    ) -> None:
        """字段 dict 中缺少 unit/description 时不抛异常"""
        fields = [{"name": "col1", "type": "text"}]
        schema = desensitizer.get_safe_schema("t", fields)
        assert "col1" in schema
