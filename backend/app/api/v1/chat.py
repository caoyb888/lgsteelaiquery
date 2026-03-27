"""
对话查询 API

POST /api/v1/chat/query            — 自然语言查询主接口
GET  /api/v1/chat/history          — 获取对话历史
POST /api/v1/chat/{log_id}/feedback — 提交查询结果反馈（点赞/点踩）
"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from loguru import logger
from sqlalchemy import select, update

from app.config import get_settings as _get_settings
from app.core.conversation import ConversationManager
from app.core.nlg import NLGService
from app.core.prompt_builder import PromptBuilder
from app.core.result_formatter import NLGResult, ResultFormatter
from app.core.sql_executor import SQLExecutor
from app.core.text_to_sql import TextToSQLEngine
from app.security.desensitize import Desensitizer
from app.db.biz_session import get_biz_session_factory
from app.db.meta_session import _MetaSessionFactory
from app.db.models.audit import AuditLog
from app.db.models.datasource import Datasource
from app.dependencies import CurrentUserIdDep, MetaSessionDep, RedisDep
from app.knowledge.dictionary import DataDictionaryManager
from app.knowledge.embedding import EmbeddingService
from app.llm.router import get_llm_router
from app.schemas.chat import (
    ChatQueryRequest,
    ChatQueryResponse,
    DataSourceInfo,
    FeedbackRequest,
)
from app.schemas.common import ApiResponse
from app.security.rbac import RBACChecker
from app.security.row_filter import RowLevelFilter

_rbac = RBACChecker()
from app.security.sql_validator import SQLValidator
from app.utils.exceptions import (
    DataPermissionError,
    LLMAllFallbackExhaustedError,
    QueryTimeoutError,
    SQLExecutionError,
    SQLGenerationError,
    SQLSafetyViolationError,
)

router = APIRouter()


async def _get_user_role(session, user_id: uuid.UUID) -> str:
    from app.db.models.user import User

    result = await session.execute(
        select(User.role).where(User.id == user_id, User.is_active.is_(True))
    )
    role = result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已被禁用")
    return role


async def _get_datasource_info(session, datasource_id: str) -> DataSourceInfo | None:
    """获取数据源时效信息用于结果标注"""
    try:
        ds_uuid = uuid.UUID(datasource_id)
    except ValueError:
        return None
    result = await session.execute(
        select(Datasource).where(Datasource.id == ds_uuid, Datasource.status == "active")
    )
    ds: Datasource | None = result.scalar_one_or_none()
    if ds is None:
        return None
    return DataSourceInfo(
        datasource_id=datasource_id,
        datasource_name=ds.name,
        data_date=str(ds.data_date),
        upload_time=ds.updated_at,
    )


@router.post("/query", response_model=ApiResponse[ChatQueryResponse])
async def query(
    request: ChatQueryRequest,
    http_request: Request,
    current_user_id: CurrentUserIdDep,
    session: MetaSessionDep,
    redis: RedisDep,
) -> ApiResponse[ChatQueryResponse]:
    """
    自然语言查询主接口。

    流程：
    1. 获取用户角色与权限域
    2. 从 Redis 读取对话历史，构建上下文问题
    3. 语义检索相关 Schema
    4. 构建 Prompt → 调用 LLM → 生成 SQL
    5. SQL 安全校验 + 行级权限注入
    6. 执行 SQL，格式化结果，生成 NLG 摘要
    7. 写入审计日志，更新对话历史
    """
    start_ts = time.monotonic()
    request_id = str(uuid.uuid4())
    conversation_id = request.conversation_id or str(uuid.uuid4())

    # 1. 用户角色与权限域
    user_role = await _get_user_role(session, current_user_id)
    allowed_domains = _rbac.get_allowed_domains(user_role)
    allowed_tables: set[str] = set()  # 将在 SQL 生成后从 validator 推断

    # 2. 对话历史 + 上下文问题
    conv_manager = ConversationManager(
        redis_client=redis,
        meta_session_factory=_MetaSessionFactory,
        settings=None,
    )
    history = await conv_manager.get_history(conversation_id)
    contextual_question = conv_manager.build_contextual_question(history, request.question)

    # 3. 语义检索相关 Schema（从知识库）
    import chromadb as _chromadb
    _settings = _get_settings()
    llm_client = get_llm_router()
    embed_service = EmbeddingService(redis_client=redis)
    chroma_client = await _chromadb.AsyncHttpClient(
        host=_settings.chroma_host, port=_settings.chroma_port
    )
    dict_manager = DataDictionaryManager(
        embedding_service=embed_service,
        chroma_client=chroma_client,
        meta_session_factory=_MetaSessionFactory,
    )

    # 默认取第一个有权限的域
    domain = list(allowed_domains)[0] if allowed_domains else "unknown"
    schema_context = await dict_manager.get_schema_context(
        query=contextual_question,
        domain=domain,
    )

    # 4. Text-to-SQL
    sql_engine = TextToSQLEngine(
        llm_router=llm_client,
        prompt_builder=PromptBuilder(),
        sql_validator=SQLValidator(),
        desensitizer=Desensitizer(),
        dictionary_manager=dict_manager,
    )
    sql_result = None
    audit_status = "failed"
    audit_block_reason: str | None = None
    generated_sql: str | None = None
    query_result = None
    formatted = None

    try:
        sql_result = await sql_engine.generate(
            question=contextual_question,
            domain=domain,
            allowed_tables=allowed_tables,
            conversation_history=[
                {"role": "user", "content": t.question} for t in history[-3:]
            ],
            user_role=user_role,
            user_id=str(current_user_id),
        )
        generated_sql = sql_result.sql

        # 5. SQL 安全校验
        validator = SQLValidator()
        is_safe, reason = validator.validate(generated_sql, allowed_tables=set())
        if not is_safe:
            audit_status = "blocked"
            audit_block_reason = reason
            raise SQLSafetyViolationError(reason or "SQL 安全校验失败")

        # 6. 执行 SQL
        from app.db.biz_session import get_biz_session_factory

        row_filter = RowLevelFilter()
        executor = SQLExecutor(
            biz_session_factory=get_biz_session_factory(),
            row_filter=row_filter,
            redis_client=redis,
        )
        query_result = await executor.execute(
            sql=generated_sql,
            user_role=user_role,
            allowed_tables=allowed_tables,
            user_id=str(current_user_id),
        )

        # 7. NLG + 格式化
        nlg_service = NLGService(llm_router=llm_client)
        nlg_result = await nlg_service.generate_summary(
            question=request.question,
            sql=generated_sql or "",
            rows=query_result.rows,
            column_names=query_result.columns,
        )

        # 数据来源信息
        datasource_id = (request.datasource_ids[0] if request.datasource_ids else "unknown")
        source_info = await _get_datasource_info(session, datasource_id) or DataSourceInfo(
            datasource_id=datasource_id,
            datasource_name="未知数据源",
            data_date="",
            upload_time=__import__("datetime").datetime.utcnow(),
        )

        formatter = ResultFormatter()
        formatted = formatter.format(
            query_result=query_result,
            nlg_result=NLGResult(
                summary=nlg_result.summary,
                display_type=nlg_result.display_type,  # type: ignore[arg-type]
                title=nlg_result.chart_title,
            ),
            datasource_info=source_info,
        )

        audit_status = "success"

        # 更新对话历史
        await conv_manager.add_turn(
            conversation_id=conversation_id,
            question=request.question,
            generated_sql=generated_sql,
            answer_summary=nlg_result.summary[:500],
        )

    except (SQLSafetyViolationError, DataPermissionError) as exc:
        audit_block_reason = str(exc)
        audit_status = "blocked"
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except QueryTimeoutError as exc:
        audit_status = "failed"
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="查询超时，请简化查询条件"
        ) from exc
    except (SQLGenerationError, LLMAllFallbackExhaustedError) as exc:
        audit_status = "failed"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"SQL 生成失败：{exc}"
        ) from exc
    except SQLExecutionError as exc:
        audit_status = "failed"
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"查询执行失败：{exc}"
        ) from exc
    finally:
        # 写入审计日志（不阻塞响应）
        elapsed_ms = int((time.monotonic() - start_ts) * 1000)
        client_ip = getattr(http_request.client, "host", None) if http_request.client else None
        try:
            audit = AuditLog(
                request_id=uuid.UUID(request_id),
                user_id=current_user_id,
                ip_address=client_ip,
                question=request.question,
                generated_sql=generated_sql,
                result_row_count=query_result.total_rows if query_result else None,
                execution_ms=elapsed_ms,
                status=audit_status,
                block_reason=audit_block_reason,
                llm_model_used=sql_result.model_used if sql_result else None,
                prompt_tokens=sql_result.tokens.get("prompt") if sql_result and sql_result.tokens else None,
                completion_tokens=sql_result.tokens.get("completion") if sql_result and sql_result.tokens else None,
            )
            session.add(audit)
            await session.commit()
        except Exception as exc:
            logger.error("审计日志写入失败", error=str(exc))

    assert formatted is not None
    assert query_result is not None

    response_data = ChatQueryResponse(
        answer_text=formatted.summary,
        display_type=formatted.display_type,
        chart_option=formatted.chart_option,
        table_data=formatted.table_data,
        sql=generated_sql if _get_settings().debug else None,
        data_sources=[formatted.source_info],
        execution_ms=int((time.monotonic() - start_ts) * 1000),
        conversation_id=conversation_id,
    )
    return ApiResponse.ok(data=response_data, request_id=request_id)


@router.get("/history")
async def get_history(
    current_user_id: CurrentUserIdDep,
    session: MetaSessionDep,
    conversation_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> ApiResponse[list]:
    """获取当前用户的查询历史（来自审计日志）"""
    from app.db.models.audit import AuditLog

    stmt = (
        select(AuditLog)
        .where(AuditLog.user_id == current_user_id, AuditLog.status == "success")
        .order_by(AuditLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    results = await session.execute(stmt)
    logs = results.scalars().all()
    items = [
        {
            "log_id": str(log.id),
            "question": log.question,
            "created_at": log.created_at.isoformat(),
            "execution_ms": log.execution_ms,
        }
        for log in logs
    ]
    return ApiResponse.ok(data=items)


@router.post("/{log_id}/feedback")
async def submit_feedback(
    log_id: str,
    request: FeedbackRequest,
    current_user_id: CurrentUserIdDep,
    session: MetaSessionDep,
) -> ApiResponse[None]:
    """提交查询结果反馈（1=点赞 -1=点踩）"""
    from app.db.models.audit import AuditLog

    try:
        log_uuid = uuid.UUID(log_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="log_id 格式错误") from exc

    result = await session.execute(
        select(AuditLog).where(
            AuditLog.id == log_uuid,
            AuditLog.user_id == current_user_id,
        )
    )
    log: AuditLog | None = result.scalar_one_or_none()
    if log is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="日志记录不存在")

    await session.execute(
        update(AuditLog)
        .where(AuditLog.id == log_uuid)
        .values(feedback=request.feedback)
    )
    await session.commit()
    logger.info("用户反馈已记录", log_id=log_id, feedback=request.feedback)
    return ApiResponse.ok(data=None, message="反馈已记录")
