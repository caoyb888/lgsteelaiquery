"""
Prompt 构建器

构建两套 Prompt 模板：
- standard_prompt：正常查询
- retry_prompt：携带上次错误信息引导 LLM 修正
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.knowledge.dictionary import SchemaContext


@dataclass
class PromptContext:
    """传递给 PromptBuilder 的上下文数据类"""

    question: str
    domain: str
    schema_context: SchemaContext           # 来自 DataDictionaryManager
    conversation_history: list[dict[str, Any]]  # 最近 N 轮 [{role, content}]
    user_role: str


class PromptBuilder:
    """
    构建两套 Prompt 模板：
    - standard_prompt：正常查询
    - retry_prompt：携带上次错误信息引导 LLM 修正
    """

    SYSTEM_PROMPT = """你是莱钢集团的企业内部数据查询助手。
根据用户提供的表结构和业务描述，将用户的自然语言问题转化为 PostgreSQL SELECT 语句。

规则：
1. 只生成 SELECT 语句，禁止 INSERT/UPDATE/DELETE/DROP 等操作
2. 表名、字段名必须与提供的 Schema 完全一致
3. 涉及日期过滤时，report_month 字段格式为 'YYYY-MM'
4. 如果问题不明确，选择最可能的解释生成 SQL
5. 只输出 SQL，不要解释，不要 Markdown 代码块
"""

    def build_standard_prompt(self, ctx: PromptContext) -> str:
        """
        构建标准查询 Prompt。

        格式：
          [系统提示]
          [Schema 信息]
          [对话历史（若有）]
          用户问题：{question}

        Args:
            ctx: 包含问题、域、Schema 和历史的上下文对象。

        Returns:
            完整的 Prompt 字符串。
        """
        parts: list[str] = [self.SYSTEM_PROMPT.strip()]

        schema_section = self._format_schema(ctx)
        if schema_section:
            parts.append(schema_section)

        history_section = self._format_history(ctx.conversation_history)
        if history_section:
            parts.append(history_section)

        parts.append(f"用户问题：{ctx.question}")

        prompt = "\n\n".join(parts)
        logger.debug(
            "标准 Prompt 构建完成",
            domain=ctx.domain,
            prompt_len=len(prompt),
        )
        return prompt

    def build_retry_prompt(
        self,
        ctx: PromptContext,
        error_sql: str,
        error_msg: str,
    ) -> str:
        """
        构建重试 Prompt，附带上次错误信息引导 LLM 修正。

        在标准 Prompt 基础上追加上次生成的 SQL 和错误原因，
        引导 LLM 修正错误。

        Args:
            ctx: 包含问题、域、Schema 和历史的上下文对象。
            error_sql: 上次生成的有问题的 SQL。
            error_msg: 上次执行/校验时的错误消息。

        Returns:
            带有错误修正引导的 Prompt 字符串。
        """
        base_prompt = self.build_standard_prompt(ctx)

        retry_section = (
            "【上次生成的 SQL 存在问题，请修正】\n"
            f"上次生成的 SQL：\n{error_sql}\n\n"
            f"错误原因：{error_msg}\n\n"
            "请根据以上错误信息，重新生成正确的 SQL。"
        )

        prompt = f"{base_prompt}\n\n{retry_section}"
        logger.debug(
            "重试 Prompt 构建完成",
            domain=ctx.domain,
            prompt_len=len(prompt),
            error_msg_len=len(error_msg),
        )
        return prompt

    def _format_schema(self, ctx: PromptContext) -> str:
        """
        格式化 Schema 部分（表名、字段列表、few-shot 示例）。

        Args:
            ctx: Prompt 上下文。

        Returns:
            格式化后的 Schema 字符串，若 Schema 为空则返回空字符串。
        """
        sc = ctx.schema_context
        parts: list[str] = []

        if sc.domain_schema_yaml:
            parts.append(f"【数据库表结构（{ctx.domain} 域）】\n{sc.domain_schema_yaml}")

        if sc.matched_fields:
            field_lines: list[str] = ["【相关字段】"]
            for f in sc.matched_fields:
                std_name = f.get("std_name", "")
                display = f.get("display_name", "")
                unit = f.get("unit", "")
                desc = f.get("description", "")
                unit_part = f"，单位：{unit}" if unit else ""
                desc_part = f"，含义：{desc}" if desc else ""
                field_lines.append(
                    f"  {std_name}（{display}{unit_part}{desc_part}）"
                )
            parts.append("\n".join(field_lines))

        if sc.few_shot_examples:
            example_lines: list[str] = ["【参考示例】"]
            for i, ex in enumerate(sc.few_shot_examples, start=1):
                q = ex.get("question", "")
                sql = ex.get("sql", "")
                example_lines.append(f"示例{i}：\n  问题：{q}\n  SQL：{sql}")
            parts.append("\n".join(example_lines))

        return "\n\n".join(parts)

    def _format_history(self, history: list[dict[str, Any]]) -> str:
        """
        格式化对话历史。

        Args:
            history: 对话历史列表，每项含 role（user/assistant）和 content。

        Returns:
            格式化后的历史字符串，若历史为空则返回空字符串。
        """
        if not history:
            return ""

        lines: list[str] = ["【对话历史】"]
        for turn in history:
            role: str = turn.get("role", "user")
            content: str = str(turn.get("content", ""))
            role_label = "用户" if role == "user" else "助手"
            lines.append(f"{role_label}：{content}")

        return "\n".join(lines)
