"""
审计日志记录器

异步写入审计记录到元数据库，不阻塞主请求流程。
写入异常时静默处理（记录日志），保证主流程稳定性。
"""
from __future__ import annotations

import uuid

from loguru import logger

from app.db.meta_session import _MetaSessionFactory
from app.db.models.audit import AuditLog


class AuditLogger:
    """审计日志异步写入器"""

    async def log(
        self,
        *,
        request_id: uuid.UUID,
        user_id: uuid.UUID,
        ip_address: str,
        question: str,
        generated_sql: str,
        tables_accessed: list[str],
        result_row_count: int,
        execution_ms: int,
        status: str,
        block_reason: str | None,
        llm_model_used: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """
        异步写入审计日志到元数据库。

        写入失败时静默处理，不向上抛出异常，确保主流程不受影响。

        Args:
            request_id: 请求唯一标识
            user_id: 发起请求的用户 ID
            ip_address: 客户端 IP 地址
            question: 用户原始问题
            generated_sql: 生成的 SQL（blocked 状态时可为空字符串）
            tables_accessed: 本次查询涉及的表名列表
            result_row_count: 返回结果行数
            execution_ms: SQL 执行耗时（毫秒）
            status: 状态标识，"success" | "failed" | "blocked"
            block_reason: 被拦截时的原因说明（success 时为 None）
            llm_model_used: 本次调用的 LLM 模型名称
            prompt_tokens: 消耗的 prompt token 数
            completion_tokens: 消耗的 completion token 数
        """
        try:
            async with _MetaSessionFactory() as session:
                audit_log = AuditLog(
                    request_id=request_id,
                    user_id=user_id,
                    ip_address=ip_address,
                    question=question,
                    generated_sql=generated_sql or None,
                    tables_accessed=tables_accessed if tables_accessed else None,
                    result_row_count=result_row_count,
                    execution_ms=execution_ms,
                    status=status,
                    block_reason=block_reason,
                    llm_model_used=llm_model_used or None,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
                session.add(audit_log)
                await session.commit()
                logger.info(
                    "审计日志写入成功",
                    request_id=str(request_id),
                    user_id=str(user_id),
                    status=status,
                )
        except Exception as exc:  # noqa: BLE001
            # 审计日志写入失败不影响主流程，仅记录错误
            logger.error(
                "审计日志写入失败",
                request_id=str(request_id),
                user_id=str(user_id),
                error=str(exc),
            )
