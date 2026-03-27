"""
自然语言生成（NLG）模块

将 SQL 查询结果转化为自然语言摘要，并判断最佳展示类型。
展示类型判断规则优先，LLM 辅助生成摘要文本。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.llm.router import LLMRouter


# 规则关键词列表（用于展示类型判断）
_TREND_KEYWORDS: list[str] = ["趋势", "走势", "变化", "环比", "同比", "按月", "按年"]
_PIE_KEYWORDS: list[str] = ["占比", "比例", "分布", "构成", "份额"]
_BAR_KEYWORDS: list[str] = ["对比", "排名", "各", "前", "top", "TOP", "排行"]

# 时间相关列名的正则（用于 line_chart 判断）
_TIME_COLUMN_PATTERN: re.Pattern[str] = re.compile(
    r"(year|month|date|time|日期|月份|年份|report_month|created_at|updated_at)",
    re.IGNORECASE,
)

# LLM 生成摘要的 Prompt 模板
_SUMMARY_PROMPT_TEMPLATE = """根据以下查询信息，用1-3句简洁的中文描述查询结果的关键信息。
不要重复用户的问题，直接描述结果，不要使用 Markdown 格式。

用户问题：{question}
查询 SQL：{sql}
结果行数：{row_count}
列名：{columns}
数据样本（最多5行）：
{sample_data}

请直接输出描述，不超过100字。"""


@dataclass
class NLGResult:
    """NLG 生成结果数据类"""

    summary: str            # 自然语言摘要（1-3 句）
    display_type: str       # "table" | "bar_chart" | "line_chart" | "pie_chart" | "single_value"
    chart_title: str | None


class NLGService:
    """
    将 SQL 查询结果转化为自然语言摘要，并判断最佳展示类型。
    """

    def __init__(self, llm_router: LLMRouter | None = None) -> None:
        self._llm_router = llm_router

    async def generate_summary(
        self,
        question: str,
        sql: str,
        rows: list[dict[str, Any]],
        column_names: list[str],
    ) -> NLGResult:
        """
        生成查询结果的自然语言摘要和展示类型。

        流程：
        1. 规则判断展示类型
        2. 若 llm_router 可用：调用 LLM 生成 1-3 句中文摘要
           若 llm_router 不可用：使用模板生成摘要

        Args:
            question: 用户原始自然语言问题。
            sql: 执行的 SQL 语句。
            rows: 查询结果行列表（每行为 dict）。
            column_names: 列名列表。

        Returns:
            NLGResult 数据类，包含摘要、展示类型和图表标题。
        """
        display_type, chart_title = self._determine_display_type(
            question, column_names, rows
        )

        summary = await self._generate_text_summary(
            question=question,
            sql=sql,
            rows=rows,
            column_names=column_names,
        )

        logger.info(
            "NLG 生成完成",
            display_type=display_type,
            row_count=len(rows),
            summary_len=len(summary),
        )

        return NLGResult(
            summary=summary,
            display_type=display_type,
            chart_title=chart_title,
        )

    def _determine_display_type(
        self,
        question: str,
        column_names: list[str],
        rows: list[dict[str, Any]],
    ) -> tuple[str, str | None]:
        """
        规则判断展示类型（优先级从高到低）。

        规则：
        - rows == 1 且 columns == 1：single_value
        - 问题含"趋势/走势/变化/环比/同比" + 时间列：line_chart
        - 问题含"占比/比例/分布/构成"：pie_chart
        - 问题含"对比/排名/各.*/前N"：bar_chart
        - 其余：table

        Args:
            question: 用户问题文本。
            column_names: 结果集列名列表。
            rows: 查询结果行列表。

        Returns:
            (display_type, chart_title) 元组。
        """
        row_count = len(rows)
        col_count = len(column_names)

        # 规则 1：单值
        if row_count == 1 and col_count == 1:
            return "single_value", None

        has_time_column = any(
            _TIME_COLUMN_PATTERN.search(col) for col in column_names
        )

        # 规则 2：趋势/走势/变化 + 时间列 → 折线图
        if has_time_column and any(kw in question for kw in _TREND_KEYWORDS):
            chart_title = f"{question[:20]}趋势图" if len(question) > 20 else f"{question}趋势图"
            return "line_chart", chart_title

        # 规则 3：占比/比例/分布/构成 → 饼图
        if any(kw in question for kw in _PIE_KEYWORDS):
            chart_title = f"{question[:20]}分布图" if len(question) > 20 else f"{question}分布图"
            return "pie_chart", chart_title

        # 规则 4：对比/排名/各.../前N → 柱状图
        if any(kw in question for kw in _BAR_KEYWORDS):
            chart_title = f"{question[:20]}对比图" if len(question) > 20 else f"{question}对比图"
            return "bar_chart", chart_title

        # 默认：表格
        return "table", None

    async def _generate_text_summary(
        self,
        question: str,
        sql: str,
        rows: list[dict[str, Any]],
        column_names: list[str],
    ) -> str:
        """
        生成文本摘要。

        若 llm_router 可用则调用 LLM；否则使用模板。

        Args:
            question: 用户原始问题。
            sql: 执行的 SQL。
            rows: 查询结果行。
            column_names: 列名列表。

        Returns:
            摘要字符串。
        """
        if self._llm_router is not None:
            try:
                summary = await self._call_llm_for_summary(
                    question=question,
                    sql=sql,
                    rows=rows,
                    column_names=column_names,
                )
                return summary
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "LLM 摘要生成失败，降级为模板摘要",
                    error=str(exc),
                )

        return self._template_summary(question, rows, column_names)

    async def _call_llm_for_summary(
        self,
        question: str,
        sql: str,
        rows: list[dict[str, Any]],
        column_names: list[str],
    ) -> str:
        """调用 LLM 生成摘要"""
        assert self._llm_router is not None  # noqa: S101

        row_count = len(rows)
        sample_rows = rows[:5]
        sample_lines: list[str] = []
        for row in sample_rows:
            line = "  " + ", ".join(
                f"{k}={v}" for k, v in row.items()
            )
            sample_lines.append(line)
        sample_data = "\n".join(sample_lines) if sample_lines else "（无数据）"

        prompt = _SUMMARY_PROMPT_TEMPLATE.format(
            question=question,
            sql=sql,
            row_count=row_count,
            columns=", ".join(column_names),
            sample_data=sample_data,
        )

        response = await self._llm_router.complete(prompt=prompt, max_tokens=200)
        summary = response.content.strip()
        logger.debug("LLM 摘要生成成功", summary_len=len(summary))
        return summary

    def _template_summary(
        self,
        question: str,
        rows: list[dict[str, Any]],
        column_names: list[str],
    ) -> str:
        """
        基于模板生成摘要（LLM 不可用时的降级方案）。

        Args:
            question: 用户问题。
            rows: 查询结果行。
            column_names: 列名列表。

        Returns:
            简单的模板摘要字符串。
        """
        row_count = len(rows)

        if row_count == 0:
            return f"根据您的问题「{question}」，未找到相关数据。"

        if row_count == 1 and len(column_names) == 1:
            value = next(iter(rows[0].values()), "")
            return f"查询结果：{column_names[0]} 为 {value}。"

        col_desc = "、".join(column_names[:3])
        if len(column_names) > 3:
            col_desc += " 等"

        return (
            f"根据您的问题「{question}」，"
            f"共查询到 {row_count} 条记录，"
            f"包含 {col_desc} 等字段信息。"
        )
