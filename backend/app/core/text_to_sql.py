"""
Text-to-SQL 引擎

主流程：
1. 语义缓存查找（QASemanticCache）
2. Schema 检索（DataDictionaryManager.get_schema_context）
3. Prompt 构建（PromptBuilder.build_standard_prompt）
4. 发送脱敏校验（Desensitizer.validate_prompt）
5. LLM 生成 SQL（LLMRouter.complete）
6. SQL 安全校验（SQLValidator.validate）
7. 失败时最多重试 settings.max_sql_retry 次（3 次）
8. 缓存成功结果
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from loguru import logger

from app.config import Settings, get_settings
from app.core.prompt_builder import PromptBuilder, PromptContext
from app.knowledge.cache import QASemanticCache
from app.knowledge.dictionary import DataDictionaryManager, SchemaContext
from app.llm.router import LLMRouter
from app.security.desensitize import Desensitizer
from app.security.sql_validator import SQLValidator
from app.utils.exceptions import SQLGenerationError


@dataclass
class SQLGenerationResult:
    """SQL 生成结果数据类"""

    sql: str
    model_used: str
    prompt_tokens: int
    completion_tokens: int
    retry_count: int
    from_cache: bool


def _extract_sql(raw: str) -> str:
    """
    从 LLM 原始响应中提取 SQL 字符串。

    处理步骤：
    1. 去除 Markdown 代码块（```sql ... ``` 或 ``` ... ```）
    2. 去除前后空白
    3. 保证以 SELECT 开头（大小写不敏感）

    Args:
        raw: LLM 原始响应文本。

    Returns:
        清洁后的 SQL 字符串。

    Raises:
        SQLGenerationError: SQL 不以 SELECT 开头时抛出。
    """
    # 去除 Markdown 代码块
    # 匹配 ```sql ... ``` 或 ``` ... ```
    code_block_pattern = re.compile(
        r"```(?:sql)?\s*\n?(.*?)\n?```",
        re.IGNORECASE | re.DOTALL,
    )
    match = code_block_pattern.search(raw)
    if match:
        sql = match.group(1).strip()
    else:
        sql = raw.strip()

    if not sql:
        raise SQLGenerationError("LLM 返回了空内容，无法提取 SQL")

    if not re.match(r"^\s*SELECT\b", sql, re.IGNORECASE):
        raise SQLGenerationError(
            f"LLM 生成的 SQL 不以 SELECT 开头，已拒绝执行。原始内容：{sql[:200]}"
        )

    return sql


class TextToSQLEngine:
    """
    Text-to-SQL 主引擎。

    组合语义缓存、Schema 检索、Prompt 构建、LLM 调用、SQL 校验等组件，
    实现从自然语言问题到安全 SQL 的完整转化流程。
    """

    def __init__(
        self,
        llm_router: LLMRouter,
        prompt_builder: PromptBuilder,
        sql_validator: SQLValidator,
        desensitizer: Desensitizer,
        dictionary_manager: DataDictionaryManager | None = None,
        qa_cache: QASemanticCache | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._llm_router = llm_router
        self._prompt_builder = prompt_builder
        self._sql_validator = sql_validator
        self._desensitizer = desensitizer
        self._dictionary_manager = dictionary_manager
        self._qa_cache = qa_cache
        self._settings = settings or get_settings()

    async def generate(
        self,
        question: str,
        domain: str,
        allowed_tables: set[str],
        conversation_history: list[dict],
        user_role: str,
        user_id: str | None = None,
    ) -> SQLGenerationResult:
        """
        主生成流程：缓存 → Schema检索 → Prompt构建 → 脱敏校验 → LLM → SQL校验 → 缓存写入。

        Args:
            question: 用户自然语言问题（已经过 clean_question 清洁）。
            domain: 数据域名称（如 sales / finance / production / procurement）。
            allowed_tables: 当前用户被授权访问的表名集合。
            conversation_history: 最近 N 轮对话历史 [{role, content}]。
            user_role: 用户角色（用于 Prompt 构建）。
            user_id: 用户 ID（用于 LLM token 预算统计）。

        Returns:
            SQLGenerationResult 数据类，包含生成的 SQL 及元信息。

        Raises:
            SQLGenerationError: 所有重试均失败时抛出。
        """
        # 步骤 1：语义缓存查找
        if self._qa_cache is not None:
            cached = await self._qa_cache.get(question)
            if cached is not None:
                cached_sql: str = cached.get("sql", "")
                if cached_sql:
                    logger.info(
                        "QA 语义缓存命中，跳过 LLM 调用",
                        question_len=len(question),
                        domain=domain,
                    )
                    return SQLGenerationResult(
                        sql=cached_sql,
                        model_used=cached.get("model_used", "cache"),
                        prompt_tokens=0,
                        completion_tokens=0,
                        retry_count=0,
                        from_cache=True,
                    )

        # 步骤 2：Schema 检索
        if self._dictionary_manager is not None:
            schema_context = await self._dictionary_manager.get_schema_context(
                query=question,
                domain=domain,
            )
        else:
            schema_context = SchemaContext()

        # 步骤 3：构建 PromptContext
        ctx = PromptContext(
            question=question,
            domain=domain,
            schema_context=schema_context,
            conversation_history=conversation_history,
            user_role=user_role,
        )

        max_retry = self._settings.max_sql_retry
        last_error_msg = ""
        last_error_sql = ""

        for attempt in range(max_retry):
            # 步骤 3a：构建 Prompt
            if attempt == 0:
                prompt = self._prompt_builder.build_standard_prompt(ctx)
            else:
                prompt = self._prompt_builder.build_retry_prompt(
                    ctx,
                    error_sql=last_error_sql,
                    error_msg=last_error_msg,
                )

            # 步骤 4：发送脱敏校验
            try:
                self._desensitizer.validate_prompt(prompt)
            except SQLGenerationError as exc:
                logger.error(
                    "Prompt 脱敏校验失败，终止生成",
                    error=str(exc),
                    attempt=attempt + 1,
                )
                raise

            # 步骤 5：LLM 生成
            try:
                response = await self._llm_router.complete(
                    prompt=prompt,
                    max_tokens=self._settings.llm_max_tokens_per_request,
                    user_id=user_id,
                )
            except Exception as exc:
                logger.warning(
                    "LLM 调用失败",
                    attempt=attempt + 1,
                    error=str(exc),
                )
                last_error_msg = str(exc)
                last_error_sql = ""
                if attempt == max_retry - 1:
                    raise SQLGenerationError(
                        f"LLM 调用失败（已重试 {max_retry} 次）：{exc}"
                    ) from exc
                continue

            # 提取 SQL
            try:
                sql = _extract_sql(response.content)
            except SQLGenerationError as exc:
                logger.warning(
                    "SQL 提取失败",
                    attempt=attempt + 1,
                    error=str(exc),
                    raw_content=response.content[:200],
                )
                last_error_msg = str(exc)
                last_error_sql = response.content.strip()
                if attempt == max_retry - 1:
                    raise SQLGenerationError(
                        f"SQL 提取失败（已重试 {max_retry} 次）：{exc}"
                    ) from exc
                continue

            # 步骤 6：SQL 安全校验
            try:
                self._sql_validator.validate(sql, allowed_tables)
            except Exception as exc:
                logger.warning(
                    "SQL 安全校验失败",
                    attempt=attempt + 1,
                    error=str(exc),
                    sql_snippet=sql[:200],
                )
                last_error_msg = str(exc)
                last_error_sql = sql
                if attempt == max_retry - 1:
                    raise SQLGenerationError(
                        f"SQL 安全校验失败（已重试 {max_retry} 次）：{exc}"
                    ) from exc
                continue

            # 步骤 7：生成成功
            logger.info(
                "SQL 生成成功",
                model=response.model,
                attempt=attempt + 1,
                domain=domain,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
            )

            # 步骤 8：缓存写入
            if self._qa_cache is not None:
                try:
                    await self._qa_cache.set(
                        question,
                        {
                            "sql": sql,
                            "model_used": response.model,
                            "domain": domain,
                        },
                    )
                except Exception as cache_exc:  # noqa: BLE001
                    # 缓存写入失败不影响正常流程
                    logger.warning(
                        "QA 缓存写入失败",
                        error=str(cache_exc),
                    )

            return SQLGenerationResult(
                sql=sql,
                model_used=response.model,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                retry_count=attempt,
                from_cache=False,
            )

        # 理论上不会走到此处，保险起见
        raise SQLGenerationError(
            f"SQL 生成失败，已达最大重试次数 {max_retry}"
        )
