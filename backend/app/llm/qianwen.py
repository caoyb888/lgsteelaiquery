"""
通义千问 LLM 客户端

使用 DashScope OpenAI 兼容接口：
  POST https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions

鉴权：Authorization: Bearer {qianwen_api_key}
"""
from __future__ import annotations

import httpx
from loguru import logger

from app.config import get_settings
from app.llm.base import BaseLLMClient, LLMResponse
from app.utils.exceptions import LLMAPIError

_QIANWEN_COMPAT_URL = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
)


class QianwenClient(BaseLLMClient):
    """通义千问客户端（OpenAI 兼容接口）"""

    def __init__(self) -> None:
        settings = get_settings()
        self.model_name: str = settings.qianwen_model
        self._api_key: str = settings.qianwen_api_key
        self._timeout: int = settings.llm_timeout_seconds
        self._base_url: str = _QIANWEN_COMPAT_URL

    async def complete(self, prompt: str, *, max_tokens: int = 2000) -> LLMResponse:
        """调用通义千问生成文本

        Args:
            prompt: 输入提示词
            max_tokens: 最大生成 token 数

        Returns:
            LLMResponse

        Raises:
            LLMAPIError: HTTP 错误或超时时抛出
        """
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }

        logger.info(
            "调用通义千问",
            model=self.model_name,
            prompt_len=len(prompt),
            max_tokens=max_tokens,
        )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._base_url, headers=headers, json=payload
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            logger.error("通义千问请求超时", model=self.model_name, error=str(exc))
            raise LLMAPIError(f"通义千问请求超时：{exc}") from exc
        except httpx.HTTPStatusError as exc:
            logger.error(
                "通义千问 HTTP 错误",
                model=self.model_name,
                status_code=exc.response.status_code,
                error=str(exc),
            )
            raise LLMAPIError(
                f"通义千问 HTTP 错误 {exc.response.status_code}：{exc}"
            ) from exc
        except httpx.RequestError as exc:
            logger.error("通义千问请求异常", model=self.model_name, error=str(exc))
            raise LLMAPIError(f"通义千问请求异常：{exc}") from exc

        data = response.json()
        content: str = data["choices"][0]["message"]["content"]
        usage: dict[str, int] = data.get("usage", {})
        prompt_tokens: int = usage.get("prompt_tokens", 0)
        completion_tokens: int = usage.get("completion_tokens", 0)

        logger.info(
            "通义千问调用成功",
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
        """检查通义千问是否可用

        Returns:
            True 表示可用，False 表示不可用
        """
        try:
            await self.complete("ping", max_tokens=10)
            return True
        except LLMAPIError as exc:
            logger.warning("通义千问健康检查失败", error=str(exc))
            return False
