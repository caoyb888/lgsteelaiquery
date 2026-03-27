"""
单元测试：app/core/conversation.py

覆盖场景：
- get_history 从 Redis 反序列化 Turn 列表
- Redis 返回 None 时 get_history 返回空列表
- Redis 数据损坏时 get_history 安全返回空列表
- add_turn 追加新 Turn 并写回 Redis
- 超过 max_turns 时丢弃最旧的轮次
- 超过 max_turns 后 turn_index 重新编号连续
- TTL 重置（set 被调用时带 ex 参数）
- 持久化到元数据库（session.execute 被调用）
- 非法 UUID 跳过持久化（不抛异常）
- build_contextual_question 无历史时返回原问题
- 含指代词时返回带历史前缀的问题
- 无指代词时返回原问题（不附加历史）
"""
from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.config import Settings
from app.core.conversation import ConversationManager, Turn, _conv_redis_key


# ---------------------------------------------------------------------------
# 辅助：构造 Settings
# ---------------------------------------------------------------------------

def _make_settings(**overrides: Any) -> Settings:
    base = dict(
        max_conversation_turns=3,
        conversation_ttl_seconds=7200,
        meta_db_password="x",
        biz_db_password="x",
        redis_password="x",
    )
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# 辅助：构造 Turn JSON（Redis 存储格式）
# ---------------------------------------------------------------------------

def _turn_to_dict(turn: Turn) -> dict[str, Any]:
    return dataclasses.asdict(turn)


def _make_turn(
    turn_index: int = 0,
    question: str = "问题",
    generated_sql: str = "SELECT 1",
    answer_summary: str = "答案摘要",
    created_at: str | None = None,
) -> Turn:
    return Turn(
        turn_index=turn_index,
        question=question,
        generated_sql=generated_sql,
        answer_summary=answer_summary,
        created_at=created_at or datetime.now(tz=timezone.utc).isoformat(),
    )


def _turns_to_redis_payload(turns: list[Turn]) -> str:
    return json.dumps([_turn_to_dict(t) for t in turns], ensure_ascii=False)


# ---------------------------------------------------------------------------
# 辅助：Mock Redis
# ---------------------------------------------------------------------------

def _make_mock_redis(
    stored_payload: str | None = None,
) -> AsyncMock:
    redis = AsyncMock()
    redis.get.return_value = stored_payload
    redis.set.return_value = True
    return redis


# ---------------------------------------------------------------------------
# 辅助：Mock meta_session_factory
# ---------------------------------------------------------------------------

def _make_mock_meta_session_factory() -> Any:
    mock_session = AsyncMock()
    mock_session.execute.return_value = None
    mock_session.commit.return_value = None

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=mock_ctx)
    return factory, mock_session


# ---------------------------------------------------------------------------
# get_history
# ---------------------------------------------------------------------------

class TestGetHistory:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_redis_miss(self) -> None:
        redis = _make_mock_redis(stored_payload=None)
        factory, _ = _make_mock_meta_session_factory()
        mgr = ConversationManager(redis, factory, _make_settings())

        history = await mgr.get_history("conv-123")
        assert history == []

    @pytest.mark.asyncio
    async def test_deserializes_turns_from_redis(self) -> None:
        turns = [
            _make_turn(0, "第一问", "SELECT 1", "第一答"),
            _make_turn(1, "第二问", "SELECT 2", "第二答"),
        ]
        redis = _make_mock_redis(_turns_to_redis_payload(turns))
        factory, _ = _make_mock_meta_session_factory()
        mgr = ConversationManager(redis, factory, _make_settings())

        result = await mgr.get_history("conv-123")
        assert len(result) == 2
        assert result[0].turn_index == 0
        assert result[0].question == "第一问"
        assert result[1].turn_index == 1
        assert result[1].generated_sql == "SELECT 2"

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_corrupt_json(self) -> None:
        redis = _make_mock_redis("NOT_VALID_JSON")
        factory, _ = _make_mock_meta_session_factory()
        mgr = ConversationManager(redis, factory, _make_settings())

        result = await mgr.get_history("any-id")
        assert result == []

    @pytest.mark.asyncio
    async def test_correct_redis_key_used(self) -> None:
        redis = _make_mock_redis(None)
        factory, _ = _make_mock_meta_session_factory()
        mgr = ConversationManager(redis, factory, _make_settings())

        conv_id = "abc-123"
        await mgr.get_history(conv_id)

        redis.get.assert_awaited_once_with(_conv_redis_key(conv_id))


# ---------------------------------------------------------------------------
# add_turn — 基本追加
# ---------------------------------------------------------------------------

class TestAddTurnBasic:
    @pytest.mark.asyncio
    async def test_turn_appended_to_empty_history(self) -> None:
        redis = _make_mock_redis(None)
        factory, mock_session = _make_mock_meta_session_factory()
        mgr = ConversationManager(redis, factory, _make_settings())

        await mgr.add_turn("conv-1", "问题1", "SELECT 1", "答案1")

        redis.set.assert_awaited_once()
        key, payload = redis.set.call_args.args[:2]
        assert key == _conv_redis_key("conv-1")
        stored = json.loads(payload)
        assert len(stored) == 1
        assert stored[0]["question"] == "问题1"
        assert stored[0]["turn_index"] == 0

    @pytest.mark.asyncio
    async def test_turn_appended_to_existing_history(self) -> None:
        existing = [_make_turn(0, "第一问", "SELECT 1", "第一答")]
        redis = _make_mock_redis(_turns_to_redis_payload(existing))
        factory, _ = _make_mock_meta_session_factory()
        mgr = ConversationManager(redis, factory, _make_settings())

        await mgr.add_turn("conv-1", "第二问", "SELECT 2", "第二答")

        _, payload = redis.set.call_args.args[:2]
        stored = json.loads(payload)
        assert len(stored) == 2
        assert stored[1]["question"] == "第二问"
        assert stored[1]["turn_index"] == 1

    @pytest.mark.asyncio
    async def test_set_called_with_correct_ttl(self) -> None:
        redis = _make_mock_redis(None)
        factory, _ = _make_mock_meta_session_factory()
        settings = _make_settings(conversation_ttl_seconds=7200)
        mgr = ConversationManager(redis, factory, settings)

        await mgr.add_turn("conv-1", "问", "SELECT 1", "答")

        ttl = redis.set.call_args.kwargs.get("ex")
        assert ttl == 7200


# ---------------------------------------------------------------------------
# add_turn — 滑动窗口
# ---------------------------------------------------------------------------

class TestAddTurnSlidingWindow:
    @pytest.mark.asyncio
    async def test_oldest_turn_discarded_when_exceeds_max(self) -> None:
        # max_turns=3，已有 3 轮，再加一轮后最旧的被丢弃
        existing = [_make_turn(i, f"问{i}", f"SQL{i}", f"答{i}") for i in range(3)]
        redis = _make_mock_redis(_turns_to_redis_payload(existing))
        factory, _ = _make_mock_meta_session_factory()
        mgr = ConversationManager(redis, factory, _make_settings(max_conversation_turns=3))

        await mgr.add_turn("conv-1", "问3", "SQL3", "答3")

        _, payload = redis.set.call_args.args[:2]
        stored = json.loads(payload)
        assert len(stored) == 3
        # 最旧的"问0"应被丢弃
        questions = [t["question"] for t in stored]
        assert "问0" not in questions
        assert "问3" in questions

    @pytest.mark.asyncio
    async def test_turn_index_renumbered_after_eviction(self) -> None:
        existing = [_make_turn(i, f"问{i}", f"SQL{i}", f"答{i}") for i in range(3)]
        redis = _make_mock_redis(_turns_to_redis_payload(existing))
        factory, _ = _make_mock_meta_session_factory()
        mgr = ConversationManager(redis, factory, _make_settings(max_conversation_turns=3))

        await mgr.add_turn("conv-1", "新问题", "SELECT x", "新答案")

        _, payload = redis.set.call_args.args[:2]
        stored = json.loads(payload)
        indices = [t["turn_index"] for t in stored]
        assert indices == list(range(len(stored)))

    @pytest.mark.asyncio
    async def test_max_turns_1_keeps_only_latest(self) -> None:
        existing = [_make_turn(0, "旧问", "SELECT old", "旧答")]
        redis = _make_mock_redis(_turns_to_redis_payload(existing))
        factory, _ = _make_mock_meta_session_factory()
        mgr = ConversationManager(redis, factory, _make_settings(max_conversation_turns=1))

        await mgr.add_turn("conv-1", "新问", "SELECT new", "新答")

        _, payload = redis.set.call_args.args[:2]
        stored = json.loads(payload)
        assert len(stored) == 1
        assert stored[0]["question"] == "新问"


# ---------------------------------------------------------------------------
# add_turn — TTL 重置
# ---------------------------------------------------------------------------

class TestAddTurnTTLReset:
    @pytest.mark.asyncio
    async def test_ttl_reset_on_every_add(self) -> None:
        redis = _make_mock_redis(None)
        factory, _ = _make_mock_meta_session_factory()
        settings = _make_settings(conversation_ttl_seconds=3600)
        mgr = ConversationManager(redis, factory, settings)

        conv_id = str(uuid.uuid4())
        await mgr.add_turn(conv_id, "问A", "SELECT 1", "答A")

        # 第二次需要先让 redis.get 返回已有内容
        _, first_payload = redis.set.call_args.args[:2]
        redis.get.return_value = first_payload
        await mgr.add_turn(conv_id, "问B", "SELECT 2", "答B")

        assert redis.set.await_count == 2
        for c in redis.set.await_args_list:
            assert c.kwargs.get("ex") == 3600 or (len(c.args) >= 3 and c.args[2] == 3600)


# ---------------------------------------------------------------------------
# add_turn — 持久化
# ---------------------------------------------------------------------------

class TestAddTurnPersistence:
    @pytest.mark.asyncio
    async def test_session_execute_called_for_valid_uuid(self) -> None:
        redis = _make_mock_redis(None)
        factory, mock_session = _make_mock_meta_session_factory()
        mgr = ConversationManager(redis, factory, _make_settings())

        conv_id = str(uuid.uuid4())
        await mgr.add_turn(conv_id, "问", "SELECT 1", "答")

        mock_session.execute.assert_awaited_once()
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_exception_for_invalid_uuid(self) -> None:
        redis = _make_mock_redis(None)
        factory, _ = _make_mock_meta_session_factory()
        mgr = ConversationManager(redis, factory, _make_settings())

        # 非法 UUID 不应抛异常，Redis 写入仍应完成
        await mgr.add_turn("not-a-uuid", "问", "SELECT 1", "答")
        redis.set.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_persist_failure_does_not_raise(self) -> None:
        redis = _make_mock_redis(None)
        factory, mock_session = _make_mock_meta_session_factory()
        mock_session.execute.side_effect = Exception("DB错误")

        mgr = ConversationManager(redis, factory, _make_settings())
        conv_id = str(uuid.uuid4())

        # 持久化失败但不应向外抛异常
        await mgr.add_turn(conv_id, "问", "SELECT 1", "答")
        redis.set.assert_awaited_once()


# ---------------------------------------------------------------------------
# build_contextual_question
# ---------------------------------------------------------------------------

class TestBuildContextualQuestion:
    def test_empty_history_returns_original_question(self) -> None:
        mgr = ConversationManager(AsyncMock(), MagicMock(), _make_settings())
        result = mgr.build_contextual_question([], "本月销售额多少？")
        assert result == "本月销售额多少？"

    def test_no_reference_word_returns_original_question(self) -> None:
        history = [_make_turn(0, "上月销售额？", "SELECT 1", "100万")]
        mgr = ConversationManager(AsyncMock(), MagicMock(), _make_settings())
        result = mgr.build_contextual_question(history, "本月销售额多少？")
        assert result == "本月销售额多少？"

    def test_reference_word_it_triggers_context_prefix(self) -> None:
        history = [_make_turn(0, "哪个产品卖得最好？", "SELECT x", "钢材")]
        mgr = ConversationManager(AsyncMock(), MagicMock(), _make_settings())
        result = mgr.build_contextual_question(history, "它的成本是多少？")
        assert "历史对话" in result
        assert "它的成本是多少？" in result
        assert "哪个产品卖得最好？" in result

    def test_reference_word_zhege_triggers_context_prefix(self) -> None:
        history = [_make_turn(0, "订单总量", "SELECT count(*) FROM orders", "500")]
        mgr = ConversationManager(AsyncMock(), MagicMock(), _make_settings())
        result = mgr.build_contextual_question(history, "这个比上月增加了多少？")
        assert "历史对话" in result

    def test_reference_word_gai_triggers_context_prefix(self) -> None:
        history = [_make_turn(0, "查采购订单", "SELECT * FROM p", "结果")]
        mgr = ConversationManager(AsyncMock(), MagicMock(), _make_settings())
        result = mgr.build_contextual_question(history, "该订单的金额是多少？")
        assert "历史对话" in result

    def test_reference_word_shanmian_triggers_context_prefix(self) -> None:
        history = [_make_turn(0, "查销售数据", "SELECT * FROM s", "数据")]
        mgr = ConversationManager(AsyncMock(), MagicMock(), _make_settings())
        result = mgr.build_contextual_question(history, "上面数据的平均值？")
        assert "历史对话" in result

    def test_context_includes_question_and_summary(self) -> None:
        history = [_make_turn(0, "历史问题", "SELECT 1", "历史答案摘要")]
        mgr = ConversationManager(AsyncMock(), MagicMock(), _make_settings())
        result = mgr.build_contextual_question(history, "它是什么？")
        assert "历史问题" in result
        assert "历史答案摘要" in result

    def test_only_last_3_turns_included(self) -> None:
        history = [_make_turn(i, f"问{i}", f"SQL{i}", f"答{i}") for i in range(5)]
        mgr = ConversationManager(AsyncMock(), MagicMock(), _make_settings())
        result = mgr.build_contextual_question(history, "它是什么？")
        # 最旧的两个问题不应出现
        assert "问0" not in result
        assert "问1" not in result
        # 最近三个应出现
        assert "问2" in result
        assert "问3" in result
        assert "问4" in result

    def test_current_question_always_at_end(self) -> None:
        history = [_make_turn(0, "上一问", "SELECT 1", "上一答")]
        mgr = ConversationManager(AsyncMock(), MagicMock(), _make_settings())
        q = "刚才说的是什么？"
        result = mgr.build_contextual_question(history, q)
        assert result.endswith(q)


# ---------------------------------------------------------------------------
# _conv_redis_key
# ---------------------------------------------------------------------------

class TestConvRedisKey:
    def test_key_format(self) -> None:
        assert _conv_redis_key("abc") == "conv:abc"

    def test_key_with_uuid(self) -> None:
        uid = str(uuid.uuid4())
        assert _conv_redis_key(uid) == f"conv:{uid}"
