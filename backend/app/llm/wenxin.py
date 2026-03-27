"""
文心一言 LLM 客户端

鉴权流程（两步）：
  Step 1：POST https://aip.baidubce.com/oauth/2.0/token
          获取 access_token，缓存 25 小时（asyncio.Lock 防并发重复刷新）
  Step 2：POST https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/ernie-4.0-8k-latest
          携带 access_token 进行对话
"""
from __future__ import annotations

import asyncio
import time

import httpx
from loguru import logger

from app.config import get_settings
from app.llm.base import BaseLLMClient, LLMResponse
from app.utils.exceptions import LLMAPIError

_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
_CHAT_URL = (
    "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat"
    "/ernie-4.0-8k-latest"
)
# access_token 有效期 30 天，保守缓存 25 小时
_TOKEN_CACHE_SECONDS = 25 * 3600


class WenxinClient(BaseLLMClient):
    """文心一言客户端"""

    def __init__(self) -> None:
        settings = get_settings()
        self.model_name: str = settings.wenxin_model
        self._api_key: str = settings.wenxin_api_key
        self._secret_key: str = settings.wenxin_secret_key
        self._timeout: int = settings.llm_timeout_seconds

        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._token_lock: asyncio.Lock = asyncio.Lock()

    async def _get_access_token(self) -> str:
        """获取（或返回缓存的）access_token，asyncio.Lock 保证并发安全

        Returns:
            access_token 字符串

        Raises:
            LLMAPIError: 获取 token 失败时抛出
        """
        async with self._token_lock:
            now = time.monotonic()
            if self._access_token and now < self._token_expires_at:
                return self._access_token

            logger.info("刷新文心一言 access_token")
            params = {
                "grant_type": "client_credentials",
                "client_id": self._api_key,
                "client_secret": self._secret_key,
            }
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(_TOKEN_URL, params=params)
                    response.raise_for_status()
            except httpx.TimeoutException as exc:
                logger.error("文心一言获取 token 超时", error=str(exc))
                raise LLMAPIError(f"文心一言获取 token 超时：{exc}") from exc
            except httpx.HTTPStatusError as exc:
                logger.error(
                    "文心一言获取 token HTTP 错误",
                    status_code=exc.response.status_code,
                    error=str(exc),
                )
                raise LLMAPIError(
                    f"文心一言获取 token HTTP 错误 {exc.response.status_code}：{exc}"
                ) from exc
            except httpx.RequestError as exc:
                logger.error("文心一言获取 token 请求异常", error=str(exc))
                raise LLMAPIError(f"文心一言获取 token 请求异常：{exc}") from exc

            data = response.json()
            if "error" in data:
                raise LLMAPIError(
                    f"文心一言获取 token 失败：{data.get('error_description', data['error'])}"
                )

            self._access_token = data["access_token"]
            self._token_expires_at = now + _TOKEN_CACHE_SECONDS
            logger.info("文心一言 access_token 刷新成功")
            return self._access_token  # type: ignore[return-value]

    async def complete(self, prompt: str, *, max_tokens: int = 2000) -> LLMResponse:
        """调用文心一言生成文本

        Args:
            prompt: 输入提示词
            max_tokens: 最大生成 token 数

        Returns:
            LLMResponse

        Raises:
            LLMAPIError: HTTP 错误、超时或 API 返回错误时抛出
        """
        access_token = await self._get_access_token()

        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "max_output_tokens": max_tokens,
        }

        logger.info(
            "调用文心一言",
            model=self.model_name,
            prompt_len=len(prompt),
            max_tokens=max_tokens,
        )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    _CHAT_URL,
                    params={"access_token": access_token},
                    json=payload,
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            logger.error("文心一言请求超时", model=self.model_name, error=str(exc))
            raise LLMAPIError(f"文心一言请求超时：{exc}") from exc
        except httpx.HTTPStatusError as exc:
            logger.error(
                "文心一言 HTTP 错误",
                model=self.model_name,
                status_code=exc.response.status_code,
                error=str(exc),
            )
            raise LLMAPIError(
                f"文心一言 HTTP 错误 {exc.response.status_code}：{exc}"
            ) from exc
        except httpx.RequestError as exc:
            logger.error("文心一言请求异常", model=self.model_name, error=str(exc))
            raise LLMAPIError(f"文心一言请求异常：{exc}") from exc

        data = response.json()
        if "error_code" in data:
            raise LLMAPIError(
                f"文心一言 API 错误 {data['error_code']}：{data.get('error_msg', '')}"
            )

        content: str = data["result"]
        usage: dict[str, int] = data.get("usage", {})
        prompt_tokens: int = usage.get("prompt_tokens", 0)
        completion_tokens: int = usage.get("completion_tokens", 0)

        logger.info(
            "文心一言调用成功",
            model=self.model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=self.model_name,
        )

    async def health_check(self) -> bool:
        """检查文心一言是否可用

        Returns:
            True 表示可用，False 表示不可用
        """
        try:
            await self.complete("ping", max_tokens=10)
            return True
        except LLMAPIError as exc:
            logger.warning("文心一言健康检查失败", error=str(exc))
            return False
