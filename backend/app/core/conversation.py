"""
对话管理器

管理多轮对话上下文：
- Redis 热缓存（TTL 2h，key = conv:{conversation_id}）
- 最多保留最近 settings.max_conversation_turns 轮（滑动窗口）
- 异步持久化到元数据库 ConversationHistory 表
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy import insert

from app.config import Settings, get_settings
from app.db.models.conversation import ConversationHistory

# 指代词列表，出现时需要附加历史上下文
_REFERENCE_WORDS: tuple[str, ...] = ("它", "这个", "该", "上面", "刚才", "此", "这些", "那个")


@dataclass
class Turn:
    """单轮对话记录"""

    turn_index: int
    question: str
    generated_sql: str
    answer_summary: str
    created_at: str  # ISO 8601 字符串，便于 JSON 序列化


def _conv_redis_key(conversation_id: str) -> str:
    return f"conv:{conversation_id}"


class ConversationManager:
    """
    多轮对话管理器。

    Redis 中以 JSON 数组存储 Turn 列表；超出 max_turns 时丢弃最旧的轮次。
    每次写入后重置 TTL；同时将新轮次异步持久化到元数据库。
    """

    def __init__(
        self,
        redis_client: Any,
        meta_session_factory: Any,
        settings: Settings | None = None,
    ) -> None:
        self._redis = redis_client
        self._meta_session_factory = meta_session_factory
        self._settings = settings or get_settings()

    async def get_history(self, conversation_id: str) -> list[Turn]:
        """
        从 Redis 获取对话历史。

        Returns:
            Turn 列表，按 turn_index 升序排列；对话不存在时返回空列表。
        """
        key = _conv_redis_key(conversation_id)
        raw = await self._redis.get(key)
        if raw is None:
            logger.debug("对话历史不存在或已过期", conversation_id=conversation_id)
            return []

        try:
            items: list[dict[str, Any]] = json.loads(raw)
            turns = [
                Turn(
                    turn_index=item["turn_index"],
                    question=item["question"],
                    generated_sql=item["generated_sql"],
                    answer_summary=item["answer_summary"],
                    created_at=item["created_at"],
                )
                for item in items
            ]
            logger.debug(
                "读取对话历史",
                conversation_id=conversation_id,
                turns=len(turns),
            )
            return turns
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning(
                "对话历史反序列化失败，返回空历史",
                conversation_id=conversation_id,
                error=str(exc),
            )
            return []

    async def add_turn(
        self,
        conversation_id: str,
        question: str,
        generated_sql: str,
        answer_summary: str,
    ) -> None:
        """
        追加新轮次，并维护滑动窗口 + 持久化。

        步骤：
          1. 从 Redis 读取现有历史
          2. 计算新轮次的 turn_index（= 历史长度）
          3. 追加新 Turn；若超出 max_turns，丢弃最旧的
          4. 序列化写回 Redis，重置 TTL
          5. 异步持久化到元数据库（不阻塞主流程）
        """
        history = await self.get_history(conversation_id)
        next_index = len(history)

        new_turn = Turn(
            turn_index=next_index,
            question=question,
            generated_sql=generated_sql,
            answer_summary=answer_summary,
            created_at=datetime.now(tz=timezone.utc).isoformat(),
        )
        history.append(new_turn)

        max_turns = self._settings.max_conversation_turns
        if len(history) > max_turns:
            # 滑动窗口：丢弃最旧的轮次
            history = history[-max_turns:]
            # 重新编号，使 turn_index 连续
            for idx, turn in enumerate(history):
                turn.turn_index = idx

        # 写回 Redis 并重置 TTL
        key = _conv_redis_key(conversation_id)
        payload = json.dumps(
            [asdict(t) for t in history],
            ensure_ascii=False,
        )
        await self._redis.set(
            key,
            payload,
            ex=self._settings.conversation_ttl_seconds,
        )

        logger.info(
            "对话轮次已追加",
            conversation_id=conversation_id,
            turn_index=new_turn.turn_index,
            total_turns=len(history),
        )

        # 异步持久化到元数据库（失败时仅记录日志，不影响主流程）
        try:
            await self._persist_turn(
                conversation_id=conversation_id,
                turn=new_turn,
            )
        except Exception as exc:
            logger.error(
                "对话历史持久化失败",
                conversation_id=conversation_id,
                turn_index=new_turn.turn_index,
                error=str(exc),
            )

    async def _persist_turn(self, conversation_id: str, turn: Turn) -> None:
        """将单轮次持久化到元数据库 conversation_history 表。"""
        try:
            conv_uuid = uuid.UUID(conversation_id)
        except ValueError:
            # conversation_id 不是合法 UUID，跳过持久化
            logger.warning(
                "conversation_id 不是合法 UUID，跳过持久化",
                conversation_id=conversation_id,
            )
            return

        stmt = insert(ConversationHistory).values(
            conversation_id=conv_uuid,
            turn_index=turn.turn_index,
            question=turn.question,
            generated_sql=turn.generated_sql,
            answer_summary=turn.answer_summary,
        )
        async with self._meta_session_factory() as session:
            await session.execute(stmt)
            await session.commit()

        logger.debug(
            "对话轮次已持久化到元数据库",
            conversation_id=conversation_id,
            turn_index=turn.turn_index,
        )

    def build_contextual_question(
        self,
        history: list[Turn],
        current_question: str,
    ) -> str:
        """
        将对话历史和当前问题融合为完整查询上下文。

        规则：
        - history == [] 时直接返回 current_question
        - 当前问题含指代词（"它"/"这个"/"该"/"上面"/"刚才" 等）时，
          在问题前加入最近若干轮的历史摘要，帮助 LLM 理解指代
        - 否则直接返回 current_question（避免无意义的上下文冗余）
        """
        if not history:
            return current_question

        # 检查是否含指代词
        has_reference = any(word in current_question for word in _REFERENCE_WORDS)
        if not has_reference:
            return current_question

        # 取最近 3 轮（避免上下文过长）
        recent = history[-3:]
        context_lines: list[str] = []
        for turn in recent:
            context_lines.append(f"问：{turn.question}")
            context_lines.append(f"答摘要：{turn.answer_summary}")

        context_str = "\n".join(context_lines)
        result = f"【历史对话】\n{context_str}\n【当前问题】{current_question}"

        logger.debug(
            "构建含上下文的查询问题",
            conversation_turns=len(recent),
            has_reference=True,
        )
        return result
