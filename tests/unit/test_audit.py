"""
单元测试：app/security/audit.py

覆盖率目标：100%

使用 unittest.mock 对 _MetaSessionFactory 进行 mock，避免真实数据库连接。
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.security.audit import AuditLogger


# ---------------------------------------------------------------------------
# 辅助：构造标准审计参数
# ---------------------------------------------------------------------------

def _default_kwargs() -> dict:
    return dict(
        request_id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        user_id=uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        ip_address="192.168.1.100",
        question="本月销售总额是多少？",
        generated_sql="SELECT SUM(revenue) FROM sales_a3f2b1c0",
        tables_accessed=["sales_a3f2b1c0"],
        result_row_count=1,
        execution_ms=42,
        status="success",
        block_reason=None,
        llm_model_used="qianwen-max",
        prompt_tokens=150,
        completion_tokens=25,
    )


# ---------------------------------------------------------------------------
# Mock 工厂
# ---------------------------------------------------------------------------

def _make_mock_session(
    add_side_effect: Exception | None = None,
    commit_side_effect: Exception | None = None,
) -> MagicMock:
    """
    创建 mock session，区分同步/异步方法：
    - add() 是同步方法（MagicMock）
    - commit() 是异步方法（AsyncMock）
    """
    session = MagicMock()
    add_mock = MagicMock()
    if add_side_effect is not None:
        add_mock.side_effect = add_side_effect
    session.add = add_mock

    commit_mock = AsyncMock()
    if commit_side_effect is not None:
        commit_mock.side_effect = commit_side_effect
    session.commit = commit_mock
    return session


def _make_mock_session_factory(session: MagicMock) -> MagicMock:
    """
    返回一个 mock 的 _MetaSessionFactory，使其作为 async context manager
    使用时 yield 给定的 session。
    """
    factory = MagicMock()

    @asynccontextmanager
    async def _ctx_mgr() -> AsyncGenerator[MagicMock, None]:
        yield session

    # 每次调用返回新的 context manager 实例
    factory.side_effect = lambda: _ctx_mgr()
    return factory


# ---------------------------------------------------------------------------
# 测试：正常写入
# ---------------------------------------------------------------------------

class TestAuditLoggerSuccess:
    @pytest.mark.asyncio
    async def test_log_adds_audit_record_and_commits(self) -> None:
        """log() 应创建 AuditLog 实例并提交到 session"""
        mock_session = _make_mock_session()
        mock_factory = _make_mock_session_factory(mock_session)

        with patch("app.security.audit._MetaSessionFactory", mock_factory):
            logger = AuditLogger()
            await logger.log(**_default_kwargs())

        mock_session.add.assert_called_once()
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_log_sets_correct_fields(self) -> None:
        """验证 AuditLog 实例字段赋值正确"""
        mock_session = _make_mock_session()
        mock_factory = _make_mock_session_factory(mock_session)
        kwargs = _default_kwargs()

        with patch("app.security.audit._MetaSessionFactory", mock_factory):
            with patch("app.security.audit.AuditLog") as MockAuditLog:
                mock_instance = MagicMock()
                MockAuditLog.return_value = mock_instance

                logger = AuditLogger()
                await logger.log(**kwargs)

        MockAuditLog.assert_called_once_with(
            request_id=kwargs["request_id"],
            user_id=kwargs["user_id"],
            ip_address=kwargs["ip_address"],
            question=kwargs["question"],
            generated_sql=kwargs["generated_sql"],
            tables_accessed=kwargs["tables_accessed"],
            result_row_count=kwargs["result_row_count"],
            execution_ms=kwargs["execution_ms"],
            status=kwargs["status"],
            block_reason=None,
            llm_model_used=kwargs["llm_model_used"],
            prompt_tokens=kwargs["prompt_tokens"],
            completion_tokens=kwargs["completion_tokens"],
        )
        mock_session.add.assert_called_once_with(mock_instance)

    @pytest.mark.asyncio
    async def test_log_status_blocked(self) -> None:
        """status=blocked 时 block_reason 应正确传递，空 SQL 转 None"""
        mock_session = _make_mock_session()
        mock_factory = _make_mock_session_factory(mock_session)

        kwargs = _default_kwargs()
        kwargs["status"] = "blocked"
        kwargs["block_reason"] = "SQL 包含禁止的模式"
        kwargs["generated_sql"] = ""

        with patch("app.security.audit._MetaSessionFactory", mock_factory):
            with patch("app.security.audit.AuditLog") as MockAuditLog:
                mock_instance = MagicMock()
                MockAuditLog.return_value = mock_instance

                logger = AuditLogger()
                await logger.log(**kwargs)

        call_kwargs = MockAuditLog.call_args.kwargs
        assert call_kwargs["status"] == "blocked"
        assert call_kwargs["block_reason"] == "SQL 包含禁止的模式"
        # 空字符串 generated_sql → 存为 None
        assert call_kwargs["generated_sql"] is None

    @pytest.mark.asyncio
    async def test_log_empty_tables_accessed_stores_none(self) -> None:
        """tables_accessed 为空列表时应存 None"""
        mock_session = _make_mock_session()
        mock_factory = _make_mock_session_factory(mock_session)

        kwargs = _default_kwargs()
        kwargs["tables_accessed"] = []

        with patch("app.security.audit._MetaSessionFactory", mock_factory):
            with patch("app.security.audit.AuditLog") as MockAuditLog:
                mock_instance = MagicMock()
                MockAuditLog.return_value = mock_instance

                logger = AuditLogger()
                await logger.log(**kwargs)

        call_kwargs = MockAuditLog.call_args.kwargs
        assert call_kwargs["tables_accessed"] is None

    @pytest.mark.asyncio
    async def test_log_empty_llm_model_stores_none(self) -> None:
        """llm_model_used 为空字符串时应存 None"""
        mock_session = _make_mock_session()
        mock_factory = _make_mock_session_factory(mock_session)

        kwargs = _default_kwargs()
        kwargs["llm_model_used"] = ""

        with patch("app.security.audit._MetaSessionFactory", mock_factory):
            with patch("app.security.audit.AuditLog") as MockAuditLog:
                mock_instance = MagicMock()
                MockAuditLog.return_value = mock_instance

                logger = AuditLogger()
                await logger.log(**kwargs)

        call_kwargs = MockAuditLog.call_args.kwargs
        assert call_kwargs["llm_model_used"] is None


# ---------------------------------------------------------------------------
# 测试：数据库写入异常时静默处理
# ---------------------------------------------------------------------------

class TestAuditLoggerFailSilently:
    @pytest.mark.asyncio
    async def test_db_commit_error_is_swallowed(self) -> None:
        """session.commit() 抛异常时不向上传播"""
        mock_session = _make_mock_session(
            commit_side_effect=Exception("DB connection lost")
        )
        mock_factory = _make_mock_session_factory(mock_session)

        with patch("app.security.audit._MetaSessionFactory", mock_factory):
            logger = AuditLogger()
            # 不应抛出任何异常
            await logger.log(**_default_kwargs())

    @pytest.mark.asyncio
    async def test_session_add_error_is_swallowed(self) -> None:
        """session.add() 抛异常时不向上传播"""
        mock_session = _make_mock_session(
            add_side_effect=RuntimeError("ORM error")
        )
        mock_factory = _make_mock_session_factory(mock_session)

        with patch("app.security.audit._MetaSessionFactory", mock_factory):
            logger = AuditLogger()
            await logger.log(**_default_kwargs())

    @pytest.mark.asyncio
    async def test_factory_error_is_swallowed(self) -> None:
        """Session 工厂本身抛异常时不向上传播"""
        def _bad_factory() -> None:
            raise ConnectionError("Cannot connect to DB")

        with patch("app.security.audit._MetaSessionFactory", side_effect=_bad_factory):
            logger = AuditLogger()
            await logger.log(**_default_kwargs())
