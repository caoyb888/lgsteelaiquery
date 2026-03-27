"""
LLM 发送脱敏校验模块

在发送 Prompt 给外部 LLM 前，确保请求体不包含真实业务数据。
采用白名单策略：Prompt 只允许包含 Schema 信息、用户自然语言问题、
Few-shot 示例（合规数据）。
"""
from __future__ import annotations

import re
from typing import Any

from loguru import logger

from app.utils.exceptions import SQLGenerationError


class Desensitizer:
    """
    在发送 Prompt 给外部 LLM 前，确保请求体不包含真实业务数据。

    采用白名单策略：Prompt 只允许包含 Schema 信息、用户自然语言问题、
    Few-shot 示例（合规数据）。敏感模式扫描使用正则黑名单。
    """

    # 禁止出现在 Prompt 中的模式
    _SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
        re.compile(r"\b\d{11}\b"),                                    # 手机号
        re.compile(r"\b\d{18}\b"),                                    # 身份证
        re.compile(r"password|passwd|secret|api_key", re.IGNORECASE), # 密钥字样
    ]

    def validate_prompt(self, prompt: str) -> None:
        """
        扫描 prompt 中是否含敏感信息，发现则抛 SQLGenerationError（code 1001）。

        Args:
            prompt: 待发送给外部 LLM 的完整 Prompt 字符串。

        Raises:
            SQLGenerationError: Prompt 中包含敏感信息时抛出。
        """
        for pattern in self._SENSITIVE_PATTERNS:
            if pattern.search(prompt):
                logger.warning(
                    "Prompt 包含敏感信息，拒绝发送",
                    pattern=pattern.pattern,
                    snippet=prompt[:100],
                )
                raise SQLGenerationError(
                    f"Prompt 中含有敏感信息（匹配模式：{pattern.pattern}），已拒绝发送给外部 LLM"
                )

        logger.debug("Prompt 脱敏校验通过", prompt_len=len(prompt))

    def get_safe_schema(
        self,
        table_name: str,
        fields: list[dict[str, Any]],
    ) -> str:
        """
        生成脱敏后的 Schema 描述字符串（仅字段名、类型、单位，不含真实值）。

        格式：
            表名：{table_name}
            字段：
              - {field_name}（{type}，单位：{unit}）: {description}
              ...

        Args:
            table_name: 表名（脱敏映射后的标准名）。
            fields: 字段列表，每项包含 name、type、unit、description 等键。
                    缺失键时使用空字符串占位，不抛出异常。

        Returns:
            格式化的 Schema 描述字符串，不含任何真实数据值。
        """
        lines: list[str] = [f"表名：{table_name}", "字段："]
        for field in fields:
            field_name: str = str(field.get("name", ""))
            field_type: str = str(field.get("type", ""))
            unit: str = str(field.get("unit", ""))
            description: str = str(field.get("description", ""))

            unit_part = f"，单位：{unit}" if unit else ""
            desc_part = f": {description}" if description else ""
            lines.append(f"  - {field_name}（{field_type}{unit_part}）{desc_part}")

        schema_str = "\n".join(lines)
        logger.debug(
            "生成脱敏 Schema",
            table_name=table_name,
            field_count=len(fields),
        )
        return schema_str

    def clean_question(self, question: str) -> str:
        """
        过滤问题中的敏感词（手机号、身份证等），替换为 [REDACTED]。

        Args:
            question: 用户原始自然语言问题。

        Returns:
            替换敏感词后的清洁问题字符串。
        """
        cleaned = question
        # 仅替换数字型敏感模式（手机号、身份证），不替换关键字模式
        numeric_patterns: list[re.Pattern[str]] = [
            re.compile(r"\b\d{18}\b"),  # 身份证（先替换，避免截断）
            re.compile(r"\b\d{11}\b"),  # 手机号
        ]
        for pattern in numeric_patterns:
            cleaned = pattern.sub("[REDACTED]", cleaned)

        if cleaned != question:
            logger.info(
                "用户问题中的敏感信息已替换",
                original_len=len(question),
                cleaned_len=len(cleaned),
            )
        return cleaned
