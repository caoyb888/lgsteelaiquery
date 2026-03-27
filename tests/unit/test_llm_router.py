"""
单元测试：LLM 客户端与路由器

覆盖范围：
- QianwenClient.complete() 正常 / HTTP 500 / timeout
- WenxinClient.complete() 正常（含 token 刷新）
- LLMRouter.complete() 正常 / 降级 / 全部失败
- LLMRouter 全局预算超限 / 用户预算超限
- 退避 asyncio.sleep 验证

不发送任何真实网络请求，全部通过 pytest-mock / unittest.mock 实现。
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.llm.base import LLMResponse
from app.llm.qianwen import QianwenClient
from app.llm.router import LLMRouter
from app.llm.wenxin import WenxinClient
from app.utils.exceptions import (
    LLMAllFallbackExhaustedError,
    LLMAPIError,
    LLMTokenBudgetExceededError,
)

# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------


def _make_qianwen_response(
    content: str = "测试回答",
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
    model: str = "qwen-max",
) -> MagicMock:
    """构造通义千问 HTTP 响应 mock"""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
        "model": model,
    }
    return mock_resp


def _make_wenxin_token_response(access_token: str = "fake_token") -> MagicMock:
    """构造文心一言获取 token 的 HTTP 响应 mock"""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"access_token": access_token}
    return mock_resp


def _make_wenxin_chat_response(
    result: str = "文心回答",
    prompt_tokens: int = 8,
    completion_tokens: int = 15,
) -> MagicMock:
    """构造文心一言对话 HTTP 响应 mock"""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "result": result,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        },
    }
    return mock_resp


def _make_mock_client(
    model_name: str = "mock-model",
    side_effect: Any = None,
    return_value: LLMResponse | None = None,
) -> MagicMock:
    """构造 BaseLLMClient mock"""
    client = MagicMock()
    client.model_name = model_name
    if side_effect is not None:
        client.complete = AsyncMock(side_effect=side_effect)
    else:
        rv = return_value or LLMResponse(
            content="ok",
            prompt_tokens=5,
            completion_tokens=10,
            model=model_name,
        )
        client.complete = AsyncMock(return_value=rv)
    return client


def _make_mock_redis(global_used: int = 0, user_used: int = 0) -> AsyncMock:
    """构造 Redis mock，get 返回预设计数"""
    redis = AsyncMock()

    async def _get(key: str) -> bytes | None:
        if key.endswith(":global"):
            return str(global_used).encode() if global_used > 0 else None
        # user key
        return str(user_used).encode() if user_used > 0 else None

    redis.get = AsyncMock(side_effect=_get)
    redis.incrby = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    return redis


# ---------------------------------------------------------------------------
# QianwenClient 测试
# ---------------------------------------------------------------------------


class TestQianwenClient:
    """通义千问客户端单元测试"""

    @pytest.mark.asyncio
    async def test_complete_success(self) -> None:
        """正常调用：返回 LLMResponse"""
        client = QianwenClient()
        mock_resp = _make_qianwen_response(content="SQL 生成结果", prompt_tokens=15)

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_resp)

        with patch("app.llm.qianwen.httpx.AsyncClient", return_value=mock_http):
            result = await client.complete("查询本月收入", max_tokens=500)

        assert isinstance(result, LLMResponse)
        assert result.content == "SQL 生成结果"
        assert result.prompt_tokens == 15
        assert result.completion_tokens == 20
        assert result.model == client.model_name

    @pytest.mark.asyncio
    async def test_complete_http_500_raises_llm_api_error(self) -> None:
        """HTTP 500 → 抛出 LLMAPIError"""
        client = QianwenClient()

        error_resp = MagicMock(spec=httpx.Response)
        error_resp.status_code = 500
        http_error = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=error_resp,
        )

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(side_effect=http_error)

        with patch("app.llm.qianwen.httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(LLMAPIError) as exc_info:
                await client.complete("问题")

        assert "500" in str(exc_info.value.message)

    @pytest.mark.asyncio
    async def test_complete_timeout_raises_llm_api_error(self) -> None:
        """请求超时 → 抛出 LLMAPIError"""
        client = QianwenClient()
        timeout_exc = httpx.TimeoutException("read timeout")

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(side_effect=timeout_exc)

        with patch("app.llm.qianwen.httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(LLMAPIError) as exc_info:
                await client.complete("问题")

        assert "超时" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_complete_request_error_raises_llm_api_error(self) -> None:
        """网络请求异常 → 抛出 LLMAPIError"""
        client = QianwenClient()
        req_error = httpx.ConnectError("connection refused")

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(side_effect=req_error)

        with patch("app.llm.qianwen.httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(LLMAPIError):
                await client.complete("问题")

    @pytest.mark.asyncio
    async def test_health_check_success(self) -> None:
        """health_check 成功返回 True"""
        client = QianwenClient()
        mock_resp = _make_qianwen_response(content="pong")

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_resp)

        with patch("app.llm.qianwen.httpx.AsyncClient", return_value=mock_http):
            result = await client.health_check()

        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure_returns_false(self) -> None:
        """health_check 异常返回 False"""
        client = QianwenClient()

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with patch("app.llm.qianwen.httpx.AsyncClient", return_value=mock_http):
            result = await client.health_check()

        assert result is False


# ---------------------------------------------------------------------------
# WenxinClient 测试
# ---------------------------------------------------------------------------


class TestWenxinClient:
    """文心一言客户端单元测试"""

    @pytest.mark.asyncio
    async def test_complete_success_with_token_refresh(self) -> None:
        """正常调用（含 token 刷新）：返回 LLMResponse"""
        client = WenxinClient()
        token_resp = _make_wenxin_token_response("my_access_token")
        chat_resp = _make_wenxin_chat_response(result="文心回答内容")

        call_count = 0

        async def _mock_post(url: str, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if "oauth" in url:
                return token_resp
            return chat_resp

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(side_effect=_mock_post)

        with patch("app.llm.wenxin.httpx.AsyncClient", return_value=mock_http):
            result = await client.complete("查询上季度产量", max_tokens=300)

        assert isinstance(result, LLMResponse)
        assert result.content == "文心回答内容"
        assert result.prompt_tokens == 8
        assert result.completion_tokens == 15
        # token + chat = 2 次 HTTP 请求
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_complete_uses_cached_token(self) -> None:
        """已有有效 token 时不重新获取"""
        import time

        client = WenxinClient()
        # 手动注入已缓存的 token
        client._access_token = "cached_token"
        client._token_expires_at = time.monotonic() + 3600

        chat_resp = _make_wenxin_chat_response(result="缓存 token 回答")

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=chat_resp)

        with patch("app.llm.wenxin.httpx.AsyncClient", return_value=mock_http):
            result = await client.complete("问题")

        assert result.content == "缓存 token 回答"
        # 只有一次 HTTP 调用（对话），无 token 刷新
        assert mock_http.post.call_count == 1

    @pytest.mark.asyncio
    async def test_complete_chat_http_error_raises_llm_api_error(self) -> None:
        """对话接口 HTTP 错误 → 抛出 LLMAPIError"""
        import time

        client = WenxinClient()
        client._access_token = "valid_token"
        client._token_expires_at = time.monotonic() + 3600

        error_resp = MagicMock(spec=httpx.Response)
        error_resp.status_code = 503
        http_error = httpx.HTTPStatusError(
            "503 Service Unavailable",
            request=MagicMock(),
            response=error_resp,
        )

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(side_effect=http_error)

        with patch("app.llm.wenxin.httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(LLMAPIError) as exc_info:
                await client.complete("问题")

        assert "503" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_complete_chat_timeout_raises_llm_api_error(self) -> None:
        """对话接口超时 → 抛出 LLMAPIError"""
        import time

        client = WenxinClient()
        client._access_token = "valid_token"
        client._token_expires_at = time.monotonic() + 3600

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with patch("app.llm.wenxin.httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(LLMAPIError) as exc_info:
                await client.complete("问题")

        assert "超时" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_token_refresh_http_error_raises_llm_api_error(self) -> None:
        """获取 access_token 时 HTTP 错误 → 抛出 LLMAPIError"""
        client = WenxinClient()

        error_resp = MagicMock(spec=httpx.Response)
        error_resp.status_code = 401
        http_error = httpx.HTTPStatusError(
            "401 Unauthorized",
            request=MagicMock(),
            response=error_resp,
        )

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(side_effect=http_error)

        with patch("app.llm.wenxin.httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(LLMAPIError) as exc_info:
                await client.complete("问题")

        assert "401" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_token_refresh_error_field_raises_llm_api_error(self) -> None:
        """获取 token 响应含 error 字段 → 抛出 LLMAPIError"""
        client = WenxinClient()

        token_resp = MagicMock(spec=httpx.Response)
        token_resp.raise_for_status = MagicMock()
        token_resp.json.return_value = {
            "error": "invalid_client",
            "error_description": "Client authentication failed",
        }

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=token_resp)

        with patch("app.llm.wenxin.httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(LLMAPIError) as exc_info:
                await client.complete("问题")

        assert "Client authentication failed" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_health_check_success(self) -> None:
        """health_check 成功返回 True"""
        import time

        client = WenxinClient()
        client._access_token = "valid_token"
        client._token_expires_at = time.monotonic() + 3600

        chat_resp = _make_wenxin_chat_response(result="pong")
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=chat_resp)

        with patch("app.llm.wenxin.httpx.AsyncClient", return_value=mock_http):
            result = await client.health_check()

        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure_returns_false(self) -> None:
        """health_check 异常返回 False"""
        import time

        client = WenxinClient()
        client._access_token = "valid_token"
        client._token_expires_at = time.monotonic() + 3600

        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        with patch("app.llm.wenxin.httpx.AsyncClient", return_value=mock_http):
            result = await client.health_check()

        assert result is False


# ---------------------------------------------------------------------------
# LLMRouter 测试
# ---------------------------------------------------------------------------


class TestLLMRouter:
    """LLM 路由器单元测试"""

    # --- 基本调用 ---

    @pytest.mark.asyncio
    async def test_first_client_success_returns_immediately(self) -> None:
        """第一个 client 成功，直接返回，不调用后续 client"""
        expected = LLMResponse(
            content="结果", prompt_tokens=10, completion_tokens=5, model="model-a"
        )
        client_a = _make_mock_client("model-a", return_value=expected)
        client_b = _make_mock_client("model-b")
        redis = _make_mock_redis()

        router = LLMRouter(clients=[client_a, client_b], redis_client=redis)
        result = await router.complete("问题", user_id="u1")

        assert result is expected
        client_a.complete.assert_awaited_once()
        client_b.complete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_first_client_fails_fallback_to_second(self) -> None:
        """第一个 client 失败后，自动降级到第二个"""
        expected = LLMResponse(
            content="fallback 结果",
            prompt_tokens=8,
            completion_tokens=12,
            model="model-b",
        )
        client_a = _make_mock_client("model-a", side_effect=LLMAPIError("model-a 不可用"))
        client_b = _make_mock_client("model-b", return_value=expected)
        redis = _make_mock_redis()

        with patch("app.llm.router.asyncio.sleep", new_callable=AsyncMock):
            router = LLMRouter(clients=[client_a, client_b], redis_client=redis)
            result = await router.complete("问题")

        assert result is expected

    @pytest.mark.asyncio
    async def test_all_clients_fail_raises_all_fallback_exhausted(self) -> None:
        """所有 client 均失败 → 抛出 LLMAllFallbackExhaustedError"""
        client_a = _make_mock_client("model-a", side_effect=LLMAPIError("a 不可用"))
        client_b = _make_mock_client("model-b", side_effect=LLMAPIError("b 不可用"))
        redis = _make_mock_redis()

        with patch("app.llm.router.asyncio.sleep", new_callable=AsyncMock):
            router = LLMRouter(clients=[client_a, client_b], redis_client=redis)
            with pytest.raises(LLMAllFallbackExhaustedError):
                await router.complete("问题")

    @pytest.mark.asyncio
    async def test_empty_client_list_raises_all_fallback_exhausted(self) -> None:
        """空 client 列表 → 直接抛出 LLMAllFallbackExhaustedError"""
        router = LLMRouter(clients=[], redis_client=None)
        with pytest.raises(LLMAllFallbackExhaustedError):
            await router.complete("问题")

    # --- Token 预算 ---

    @pytest.mark.asyncio
    async def test_global_budget_exceeded_raises_error(self) -> None:
        """全局 Token 预算超限 → 抛出 LLMTokenBudgetExceededError"""
        # 全局已使用量 = 预算值（触发超限）
        from app.config import get_settings

        budget = get_settings().llm_daily_token_budget
        redis = _make_mock_redis(global_used=budget)

        router = LLMRouter(clients=[_make_mock_client()], redis_client=redis)
        with pytest.raises(LLMTokenBudgetExceededError) as exc_info:
            await router.complete("问题")

        assert "全局" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_user_budget_exceeded_raises_error(self) -> None:
        """每用户 Token 预算超限 → 抛出 LLMTokenBudgetExceededError"""
        from app.config import get_settings

        user_budget = get_settings().llm_daily_token_budget_per_user
        redis = _make_mock_redis(global_used=0, user_used=user_budget)

        router = LLMRouter(clients=[_make_mock_client()], redis_client=redis)
        with pytest.raises(LLMTokenBudgetExceededError) as exc_info:
            await router.complete("问题", user_id="user_123")

        assert "user_123" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_no_user_id_skips_per_user_budget_check(self) -> None:
        """未传 user_id 时不进行 per-user 预算检查"""
        from app.config import get_settings

        user_budget = get_settings().llm_daily_token_budget_per_user
        # 即使 user 计数超限，但未传 user_id，不应触发
        redis = _make_mock_redis(global_used=0, user_used=user_budget)

        expected = LLMResponse(
            content="ok", prompt_tokens=5, completion_tokens=5, model="m"
        )
        router = LLMRouter(
            clients=[_make_mock_client(return_value=expected)], redis_client=redis
        )
        # user_id=None → 不检查 per-user 预算
        result = await router.complete("问题", user_id=None)
        assert result is expected

    # --- Redis token 记录 ---

    @pytest.mark.asyncio
    async def test_tokens_recorded_to_redis_after_success(self) -> None:
        """成功后 Redis incrby 被正确调用"""
        expected = LLMResponse(
            content="ok", prompt_tokens=100, completion_tokens=50, model="m"
        )
        client = _make_mock_client(return_value=expected)
        redis = _make_mock_redis()

        router = LLMRouter(clients=[client], redis_client=redis)
        await router.complete("问题", user_id="u99")

        # incrby 应被调用 2 次（global + user）
        assert redis.incrby.await_count == 2
        # 验证 total_tokens = 150
        calls = [call.args for call in redis.incrby.await_args_list]
        token_values = [c[1] for c in calls]
        assert all(v == 150 for v in token_values)

    @pytest.mark.asyncio
    async def test_tokens_recorded_global_only_when_no_user_id(self) -> None:
        """无 user_id 时只记录全局 token"""
        expected = LLMResponse(
            content="ok", prompt_tokens=10, completion_tokens=10, model="m"
        )
        client = _make_mock_client(return_value=expected)
        redis = _make_mock_redis()

        router = LLMRouter(clients=[client], redis_client=redis)
        await router.complete("问题", user_id=None)

        # 只记录 global（1 次 incrby）
        assert redis.incrby.await_count == 1

    @pytest.mark.asyncio
    async def test_redis_failure_does_not_raise(self) -> None:
        """Redis 记录失败时不影响正常返回"""
        expected = LLMResponse(
            content="ok", prompt_tokens=5, completion_tokens=5, model="m"
        )
        client = _make_mock_client(return_value=expected)

        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)  # 预算检查通过
        redis.incrby = AsyncMock(side_effect=Exception("Redis 连接断开"))
        redis.expire = AsyncMock()

        router = LLMRouter(clients=[client], redis_client=redis)
        result = await router.complete("问题", user_id="u1")
        # 即使 Redis 写入失败，结果仍正常返回
        assert result is expected

    # --- 退避等待 ---

    @pytest.mark.asyncio
    async def test_backoff_sleep_called_on_retry(self) -> None:
        """单 client 首次失败后，asyncio.sleep 被调用（退避等待）"""
        # 第一次失败，第二次成功
        expected = LLMResponse(
            content="retry ok", prompt_tokens=5, completion_tokens=5, model="m"
        )
        call_count = 0

        async def _flaky(*args: Any, **kwargs: Any) -> LLMResponse:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise LLMAPIError("第一次失败")
            return expected

        client = MagicMock()
        client.model_name = "flaky-model"
        client.complete = AsyncMock(side_effect=_flaky)
        redis = _make_mock_redis()

        with patch(
            "app.llm.router.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            router = LLMRouter(clients=[client], redis_client=redis)
            result = await router.complete("问题")

        assert result is expected
        # 第一次失败后应有退避 sleep
        mock_sleep.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_backoff_sleep_called_for_each_failing_client(self) -> None:
        """每个 client 内部重试各有退避等待"""
        # 两个 client，都是首次失败、重试也失败
        client_a = _make_mock_client("model-a", side_effect=LLMAPIError("a 失败"))
        client_b = _make_mock_client("model-b", side_effect=LLMAPIError("b 失败"))
        redis = _make_mock_redis()

        with patch(
            "app.llm.router.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            router = LLMRouter(clients=[client_a, client_b], redis_client=redis)
            with pytest.raises(LLMAllFallbackExhaustedError):
                await router.complete("问题")

        # 每个 client 各 1 次内部重试退避，共 2 次 sleep
        assert mock_sleep.await_count == 2

    # --- 重试次数验证 ---

    @pytest.mark.asyncio
    async def test_each_client_retried_exactly_max_attempts_times(self) -> None:
        """每个 client 最多重试 _MAX_ATTEMPTS_PER_CLIENT 次"""
        from app.llm.router import _MAX_ATTEMPTS_PER_CLIENT

        client = _make_mock_client("m", side_effect=LLMAPIError("always fail"))
        redis = _make_mock_redis()

        with patch("app.llm.router.asyncio.sleep", new_callable=AsyncMock):
            router = LLMRouter(clients=[client], redis_client=redis)
            with pytest.raises(LLMAllFallbackExhaustedError):
                await router.complete("问题")

        assert client.complete.await_count == _MAX_ATTEMPTS_PER_CLIENT

    # --- 无 Redis 时的行为 ---

    @pytest.mark.asyncio
    async def test_works_without_redis(self) -> None:
        """未提供 Redis 时正常工作（跳过预算检查与记录）"""
        expected = LLMResponse(
            content="no redis ok", prompt_tokens=3, completion_tokens=3, model="m"
        )
        router = LLMRouter(
            clients=[_make_mock_client(return_value=expected)], redis_client=None
        )
        result = await router.complete("问题", user_id="u1")
        assert result is expected
