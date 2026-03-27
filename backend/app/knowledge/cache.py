"""
语义 QA 缓存模块

基于问题语义相似度的查询缓存。
缓存命中条件：新问题与缓存中某问题的余弦相似度 ≥ 0.92。
存储：Redis（key = qa_cache:{date}，ZSet + Hash 组合）
TTL：settings.query_result_cache_ttl（5 分钟）
"""
from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

import numpy as np
from loguru import logger

from app.knowledge.embedding import EmbeddingService


def _today_key() -> str:
    return f"qa_cache:{date.today().isoformat()}"


def _entry_hash_key(date_key: str, entry_id: str) -> str:
    return f"{date_key}:entry:{entry_id}"


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    vec_a = np.array(a, dtype=np.float32)
    vec_b = np.array(b, dtype=np.float32)
    norm_a = np.linalg.norm(vec_a)
    norm_b = np.linalg.norm(vec_b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(vec_a, vec_b) / (norm_a * norm_b))


class QASemanticCache:
    """
    基于问题语义相似度的查询缓存。

    存储结构（Redis）：
      - Set  key: qa_cache:{date}
        members: entry_id（UUID 字符串）
      - Hash key: qa_cache:{date}:entry:{entry_id}
        fields:
          vector  → JSON 编码的 list[float]
          result  → JSON 编码的 result dict
          question → 原始问题文本
    """

    def __init__(
        self,
        embedding_service: EmbeddingService,
        redis_client: Any,
        threshold: float = 0.92,
    ) -> None:
        self._embedding = embedding_service
        self._redis = redis_client
        self._threshold = threshold

    async def get(self, question: str) -> dict[str, Any] | None:
        """
        查找缓存：
        1. 将 question 向量化
        2. 遍历当日缓存中的向量，计算余弦相似度
        3. 相似度 ≥ threshold → 返回缓存的 result dict
        4. 未命中返回 None
        """
        embedding_result = await self._embedding.embed(question)
        query_vector = embedding_result.vector

        date_key = _today_key()
        entry_ids_raw: set[bytes | str] = await self._redis.smembers(date_key)

        if not entry_ids_raw:
            logger.debug("QA cache empty for today", date_key=date_key)
            return None

        best_sim = -1.0
        best_result: dict[str, Any] | None = None

        for entry_id_raw in entry_ids_raw:
            entry_id = (
                entry_id_raw.decode("utf-8")
                if isinstance(entry_id_raw, bytes)
                else entry_id_raw
            )
            hash_key = _entry_hash_key(date_key, entry_id)
            entry_data = await self._redis.hgetall(hash_key)

            if not entry_data:
                # entry 已过期但 set 中仍有记录（TTL 差异），跳过
                continue

            # hgetall 可能返回 bytes key
            def _decode(v: bytes | str) -> str:
                return v.decode("utf-8") if isinstance(v, bytes) else v

            raw_vector = entry_data.get(b"vector") or entry_data.get("vector")
            raw_result = entry_data.get(b"result") or entry_data.get("result")

            if raw_vector is None or raw_result is None:
                continue

            try:
                cached_vector: list[float] = json.loads(_decode(raw_vector))
                sim = _cosine_similarity(query_vector, cached_vector)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning(
                    "QA cache: failed to decode cached vector",
                    entry_id=entry_id,
                    error=str(exc),
                )
                continue

            if sim > best_sim:
                best_sim = sim
                if sim >= self._threshold:
                    try:
                        best_result = json.loads(_decode(raw_result))
                    except json.JSONDecodeError as exc:
                        logger.warning(
                            "QA cache: failed to decode cached result",
                            entry_id=entry_id,
                            error=str(exc),
                        )
                        best_result = None

        if best_result is not None:
            logger.info(
                "QA semantic cache hit",
                similarity=round(best_sim, 4),
                threshold=self._threshold,
                question_len=len(question),
            )
            return best_result

        logger.debug(
            "QA semantic cache miss",
            best_similarity=round(best_sim, 4),
            threshold=self._threshold,
        )
        return None

    async def set(self, question: str, result: dict[str, Any]) -> None:
        """
        写入缓存：
        1. 向量化 question
        2. 将 (vector, result) 序列化存入 Redis Hash
        3. 设置 TTL
        """
        embedding_result = await self._embedding.embed(question)
        entry_id = str(uuid.uuid4())
        date_key = _today_key()
        hash_key = _entry_hash_key(date_key, entry_id)

        ttl: int = self._embedding._settings.query_result_cache_ttl  # noqa: SLF001

        pipeline = self._redis.pipeline()
        pipeline.hset(
            hash_key,
            mapping={
                "vector": json.dumps(embedding_result.vector),
                "result": json.dumps(result, ensure_ascii=False, default=str),
                "question": question,
            },
        )
        pipeline.expire(hash_key, ttl)
        pipeline.sadd(date_key, entry_id)
        # Set 本身也设 TTL，略长于 entry 避免孤立 key
        pipeline.expire(date_key, ttl + 60)
        await pipeline.execute()

        logger.info(
            "QA result cached",
            entry_id=entry_id,
            ttl=ttl,
            question_len=len(question),
        )

    async def invalidate_by_domain(self, domain: str) -> None:
        """新数据上传后清除对应域的缓存（扫描 key 匹配 domain）"""
        date_key = _today_key()
        entry_ids_raw: set[bytes | str] = await self._redis.smembers(date_key)

        if not entry_ids_raw:
            logger.debug(
                "QA cache invalidate: no entries today",
                domain=domain,
            )
            return

        removed = 0
        for entry_id_raw in entry_ids_raw:
            entry_id = (
                entry_id_raw.decode("utf-8")
                if isinstance(entry_id_raw, bytes)
                else entry_id_raw
            )
            hash_key = _entry_hash_key(date_key, entry_id)
            entry_data = await self._redis.hgetall(hash_key)

            if not entry_data:
                continue

            raw_result = entry_data.get(b"result") or entry_data.get("result")
            if raw_result is None:
                continue

            def _decode(v: bytes | str) -> str:
                return v.decode("utf-8") if isinstance(v, bytes) else v

            try:
                result_obj: dict[str, Any] = json.loads(_decode(raw_result))
            except json.JSONDecodeError:
                continue

            # result dict 约定包含 domain 字段（由 result_formatter 注入）
            entry_domain = result_obj.get("domain", "")
            if entry_domain == domain:
                pipeline = self._redis.pipeline()
                pipeline.delete(hash_key)
                pipeline.srem(date_key, entry_id)
                await pipeline.execute()
                removed += 1

        logger.info(
            "QA cache invalidated by domain",
            domain=domain,
            removed=removed,
        )
