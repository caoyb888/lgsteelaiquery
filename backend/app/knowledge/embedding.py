"""
向量化服务模块

调用通义千问 text-embedding-v3 接口生成文本向量，
结果缓存到 Redis（TTL 24h，key = embedding:{sha256(text)[:16]}）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from app.config import Settings, get_settings
from app.utils.exceptions import LLMAPIError

_EMBEDDING_API_URL = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
)
_EMBEDDING_MODEL = "text-embedding-v3"
_BATCH_SIZE = 25


@dataclass
class EmbeddingResult:
    text: str
    vector: list[float]
    model: str


def _cache_key(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"embedding:{digest}"


class EmbeddingService:
    """
    调用通义千问 text-embedding-v3 接口生成向量，
    结果缓存到 Redis（TTL 24h，key = embedding:{sha256(text)[:16]}）。
    """

    def __init__(
        self,
        redis_client: Any,
        settings: Settings | None = None,
    ) -> None:
        self._redis = redis_client
        self._settings = settings or get_settings()

    async def embed(self, text: str) -> EmbeddingResult:
        """单条文本向量化（先查缓存）"""
        key = _cache_key(text)
        cached = await self._redis.get(key)
        if cached is not None:
            vector: list[float] = json.loads(cached)
            logger.debug("embedding cache hit", key=key, text_len=len(text))
            return EmbeddingResult(text=text, vector=vector, model=_EMBEDDING_MODEL)

        results = await self._call_api([text])
        vector = results[0]
        ttl = self._settings.embedding_cache_ttl
        await self._redis.set(key, json.dumps(vector), ex=ttl)
        logger.debug(
            "embedding generated and cached",
            key=key,
            text_len=len(text),
            ttl=ttl,
        )
        return EmbeddingResult(text=text, vector=vector, model=_EMBEDDING_MODEL)

    async def embed_batch(self, texts: list[str]) -> list[EmbeddingResult]:
        """批量向量化（未命中缓存的批量请求，每批最多 25 条）"""
        results: list[EmbeddingResult | None] = [None] * len(texts)
        missing_indices: list[int] = []
        missing_texts: list[str] = []

        # 先查缓存
        for idx, text in enumerate(texts):
            key = _cache_key(text)
            cached = await self._redis.get(key)
            if cached is not None:
                vector = json.loads(cached)
                results[idx] = EmbeddingResult(
                    text=text, vector=vector, model=_EMBEDDING_MODEL
                )
                logger.debug("embedding cache hit (batch)", key=key)
            else:
                missing_indices.append(idx)
                missing_texts.append(text)

        if not missing_texts:
            return results  # type: ignore[return-value]

        # 分批请求 API
        ttl = self._settings.embedding_cache_ttl
        for batch_start in range(0, len(missing_texts), _BATCH_SIZE):
            batch_texts = missing_texts[batch_start : batch_start + _BATCH_SIZE]
            batch_vectors = await self._call_api(batch_texts)
            for offset, (text, vector) in enumerate(zip(batch_texts, batch_vectors)):
                global_idx = missing_indices[batch_start + offset]
                results[global_idx] = EmbeddingResult(
                    text=text, vector=vector, model=_EMBEDDING_MODEL
                )
                key = _cache_key(text)
                await self._redis.set(key, json.dumps(vector), ex=ttl)
            logger.debug(
                "embedding batch generated",
                batch_size=len(batch_texts),
                batch_start=batch_start,
            )

        return results  # type: ignore[return-value]

    async def _call_api(self, texts: list[str]) -> list[list[float]]:
        """调用通义千问 Embeddings API，返回与 texts 等长的向量列表"""
        headers = {
            "Authorization": f"Bearer {self._settings.qianwen_api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": _EMBEDDING_MODEL, "input": texts}

        try:
            async with httpx.AsyncClient(
                timeout=self._settings.llm_timeout_seconds
            ) as client:
                response = await client.post(
                    _EMBEDDING_API_URL, headers=headers, json=payload
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            logger.error(
                "embedding API timeout",
                url=_EMBEDDING_API_URL,
                batch_size=len(texts),
                error=str(exc),
            )
            raise LLMAPIError(
                f"向量化 API 超时（{self._settings.llm_timeout_seconds}s）"
            ) from exc
        except httpx.HTTPStatusError as exc:
            logger.error(
                "embedding API HTTP error",
                status_code=exc.response.status_code,
                body=exc.response.text,
                error=str(exc),
            )
            raise LLMAPIError(
                f"向量化 API 返回错误状态码：{exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            logger.error(
                "embedding API network error",
                url=_EMBEDDING_API_URL,
                error=str(exc),
            )
            raise LLMAPIError(f"向量化 API 网络错误：{exc}") from exc

        try:
            items: list[dict[str, Any]] = sorted(
                data["data"], key=lambda item: item["index"]
            )
            vectors: list[list[float]] = [item["embedding"] for item in items]
        except (KeyError, TypeError) as exc:
            logger.error(
                "embedding API unexpected response format",
                response_keys=list(data.keys()) if isinstance(data, dict) else None,
                error=str(exc),
            )
            raise LLMAPIError("向量化 API 返回格式异常") from exc

        if len(vectors) != len(texts):
            raise LLMAPIError(
                f"向量化 API 返回数量不匹配：期望 {len(texts)}，实际 {len(vectors)}"
            )

        return vectors
