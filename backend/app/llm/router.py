"""
LLM 路由器

降级链：qianwen-max → qianwen-plus → wenxin-4.0
- 每个模型最多内部重试 1 次，指数退避（1s → 2s）
- 所有模型均失败 → LLMAllFallbackExhaustedError
- Token 预算检查（全局 + 每用户），超限 → LLMTokenBudgetExceededError
- 每次成功调用后，将 token 消耗记录到 Redis
  key 格式：token:{YYYYMMDD}:global / token:{YYYYMMDD}:{user_id}
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.config import get_settings
from app.llm.base import BaseLLMClient, LLMResponse
from app.utils.exceptions import (
    LLMAllFallbackExhaustedError,
    LLMAPIError,
    LLMTokenBudgetExceededError,
)

# 每个 client 的内部重试次数（首次 + 1 次重试 = 最多 2 次尝试）
_MAX_ATTEMPTS_PER_CLIENT = 2
# 退避等待时间列表（秒），第 i 次失败后等待 _BACKOFF[i]
_BACKOFF = [1, 2]


def _today_key() -> str:
    """返回当天日期字符串 YYYYMMDD（UTC）"""
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d")


class LLMRouter:
    """LLM 路由器，支持降级链、重试退避与 Token 预算管理"""

    def __init__(
        self,
        clients: list[BaseLLMClient] | None = None,
        redis_client: Any | None = None,
    ) -> None:
        settings = get_settings()
        self._clients: list[BaseLLMClient] = clients if clients is not None else []
        self._redis: Any | None = redis_client
        self._global_budget: int = settings.llm_daily_token_budget
        self._user_budget: int = settings.llm_daily_token_budget_per_user

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    async def complete(
        self,
        prompt: str,
        *,
        max_tokens: int = 2000,
        user_id: str | None = None,
    ) -> LLMResponse:
        """尝试降级链，返回第一个成功结果

        Args:
            prompt: 输入提示词
            max_tokens: 最大生成 token 数
            user_id: 当前用户 ID（用于 per-user 预算检查）

        Returns:
            LLMResponse

        Raises:
            LLMTokenBudgetExceededError: Token 预算超限
            LLMAllFallbackExhaustedError: 所有 client 均失败
        """
        await self._check_budget(user_id)

        last_error: LLMAPIError | None = None

        for client in self._clients:
            for attempt in range(_MAX_ATTEMPTS_PER_CLIENT):
                try:
                    logger.info(
                        "LLMRouter 尝试调用",
                        model=client.model_name,
                        attempt=attempt + 1,
                    )
                    response = await client.complete(prompt, max_tokens=max_tokens)
                    await self._record_tokens(response, user_id)
                    return response

                except LLMAPIError as exc:
                    last_error = exc
                    logger.warning(
                        "LLMRouter 调用失败",
                        model=client.model_name,
                        attempt=attempt + 1,
                        error=str(exc),
                    )
                    # 若还有剩余重试次数则退避等待
                    if attempt < _MAX_ATTEMPTS_PER_CLIENT - 1:
                        wait = _BACKOFF[attempt]
                        logger.info(
                            "LLMRouter 退避等待",
                            model=client.model_name,
                            wait_seconds=wait,
                        )
                        await asyncio.sleep(wait)

            logger.warning(
                "LLMRouter 客户端耗尽所有重试，切换下一个",
                model=client.model_name,
            )

        logger.error("LLMRouter 所有模型均失败")
        raise LLMAllFallbackExhaustedError(
            f"所有 AI 模型均不可用，最后错误：{last_error}"
        )

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _get_token_count(self, key: str) -> int:
        """从 Redis 读取 token 计数，若 Redis 不可用则返回 0"""
        if self._redis is None:
            return 0
        try:
            value = await self._redis.get(key)
            return int(value) if value is not None else 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis 读取 token 计数失败，跳过预算检查", error=str(exc))
            return 0

    async def _check_budget(self, user_id: str | None) -> None:
        """检查全局及用户 Token 预算

        Raises:
            LLMTokenBudgetExceededError: 任意预算超限时抛出
        """
        date_key = _today_key()
        global_key = f"token:{date_key}:global"

        global_used = await self._get_token_count(global_key)
        if global_used >= self._global_budget:
            logger.warning(
                "全局 Token 预算超限",
                global_used=global_used,
                global_budget=self._global_budget,
            )
            raise LLMTokenBudgetExceededError(
                f"全局 Token 日预算已超限（已用 {global_used}，上限 {self._global_budget}）"
            )

        if user_id is not None:
            user_key = f"token:{date_key}:{user_id}"
            user_used = await self._get_token_count(user_key)
            if user_used >= self._user_budget:
                logger.warning(
                    "用户 Token 预算超限",
                    user_id=user_id,
                    user_used=user_used,
                    user_budget=self._user_budget,
                )
                raise LLMTokenBudgetExceededError(
                    f"用户 {user_id} Token 日预算已超限"
                    f"（已用 {user_used}，上限 {self._user_budget}）"
                )

    async def _record_tokens(
        self, response: LLMResponse, user_id: str | None
    ) -> None:
        """将本次消耗的 token 数累加到 Redis

        key：token:{YYYYMMDD}:global / token:{YYYYMMDD}:{user_id}
        TTL：26 小时（确保跨天数据可查，同时自动清理）
        """
        if self._redis is None:
            return

        total_tokens = response.prompt_tokens + response.completion_tokens
        date_key = _today_key()
        ttl = 26 * 3600  # 26 小时

        try:
            global_key = f"token:{date_key}:global"
            await self._redis.incrby(global_key, total_tokens)
            await self._redis.expire(global_key, ttl)

            if user_id is not None:
                user_key = f"token:{date_key}:{user_id}"
                await self._redis.incrby(user_key, total_tokens)
                await self._redis.expire(user_key, ttl)

            logger.info(
                "Token 消耗已记录",
                model=response.model,
                total_tokens=total_tokens,
                user_id=user_id,
            )
        except Exception as exc:  # noqa: BLE001
            # Redis 记录失败不影响业务，仅记录警告
            logger.warning("Redis 记录 token 消耗失败", error=str(exc))


# ----------------------------------------------------------------------
# 工厂函数
# ----------------------------------------------------------------------


def get_llm_router() -> LLMRouter:
    """使用 settings 构建真实 LLMRouter（包含通义千问和文心一言 client）

    Returns:
        配置好降级链的 LLMRouter 实例
    """
    from app.llm.qianwen import QianwenClient
    from app.llm.wenxin import WenxinClient

    settings = get_settings()

    # 通义千问降级链：qianwen-max → qianwen-plus
    qianwen_max = QianwenClient()
    qianwen_max.model_name = settings.qianwen_model  # "qwen-max"

    qianwen_plus = QianwenClient()
    qianwen_plus.model_name = "qwen-plus"

    wenxin = WenxinClient()

    clients: list[BaseLLMClient] = [qianwen_max, qianwen_plus, wenxin]

    return LLMRouter(clients=clients)
