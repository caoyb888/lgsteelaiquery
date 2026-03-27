"""
LLM 抽象基类

所有 LLM 客户端必须继承 BaseLLMClient，实现 complete() 和 health_check()。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    """LLM 响应数据类"""

    content: str
    prompt_tokens: int
    completion_tokens: int
    model: str


class BaseLLMClient(ABC):
    """LLM 客户端抽象基类"""

    model_name: str  # 子类必须赋值

    @abstractmethod
    async def complete(self, prompt: str, *, max_tokens: int = 2000) -> LLMResponse:
        """调用 LLM 生成文本

        Args:
            prompt: 输入提示词
            max_tokens: 最大生成 token 数

        Returns:
            LLMResponse 数据类

        Raises:
            LLMAPIError: 调用失败时抛出
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """检查模型是否可用

        Returns:
            True 表示可用，False 表示不可用
        """
