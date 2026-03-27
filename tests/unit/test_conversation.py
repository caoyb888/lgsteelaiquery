"""
tests/unit/test_conversation.py

ConversationManager 单元测试。
全程使用 AsyncMock 模拟 Redis 和元数据库；不依赖真实服务。
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.conversation import ConversationManager, Turn, _conv_redis_key


# ─── Helpers ────────────────────────────────────────────────────────────────


def _make_settings(max_turns: int = 10, ttl: int = 7200) -> MagicMock:
    s = MagicMock()
    s.max_conversation_turns = max_turns
    s.conversation_ttl_seconds = ttl
    return s


def _make_redis(stored: dict | None = None) -> AsyncMock:
    """stored: {key: json_bytes}"""
    data: dict[str, bytes] = {}
    if stored:
        for k, v in stored.items():
            if isinstance(v, (list, dict)):
                data[k] = json.dumps(v, ensure_ascii=False).encode()
            else:
                data[k] = v

    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=lambda key: data.get(key))
    redis.set = AsyncMock(side_effect=lambda key, val, ex=None: data.__setitem__(key, val))
    return redis


@asynccontextmanager
async def _noop_meta_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    yield session


def _meta_factory():
    return _noop_meta_session()


def _make_manager(
    stored_turns: list[dict] | None = None,
    max_turns: int = 10,
    ttl: int = 7200,
    conv_id: str = "test-conv",
) -> ConversationManager:
    if stored_turns is not None:
        stored = {_conv_redis_key(conv_id): stored_turns}
    else:
        stored = None
    redis = _make_redis(stored)
    settings = _make_settings(max_turns=max_turns, ttl=ttl)
    return ConversationManager(
        redis_client=redis,
        meta_session_factory=_meta_factory,
        settings=settings,
    )


def _sample_turn_dict(index: int = 0) -> dict:
    return {
        "turn_index": index,
        "question": f"问题{index}",
        "generated_sql": f"SELECT {index}",
        "answer_summary": f"答案{index}",
        "created_at": datetime(2026, 3, 27, 12, index, 0, tzinfo=timezone.utc).isoformat(),
    }


# ─── _conv_redis_key ─────────────────────────────────────────────────────────


def test_conv_redis_key_format():
    key = _conv_redis_key("abc-123")
    assert key == "conv:abc-123"


def test_conv_redis_key_different_ids():
    assert _conv_redis_key("a") != _conv_redis_key("b")


# ─── get_history ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_history_empty_when_no_data():
    mgr = _make_manager()
    history = await mgr.get_history("nonexistent")
    assert history == []


@pytest.mark.asyncio
async def test_get_history_returns_turns():
    turns_data = [_sample_turn_dict(0), _sample_turn_dict(1)]
    mgr = _make_manager(stored_turns=turns_data)
    history = await mgr.get_history("test-conv")
    assert len(history) == 2
    assert isinstance(history[0], Turn)
    assert history[0].turn_index == 0
    assert history[1].turn_index == 1


@pytest.mark.asyncio
async def test_get_history_turn_fields():
    turns_data = [_sample_turn_dict(0)]
    mgr = _make_manager(stored_turns=turns_data)
    history = await mgr.get_history("test-conv")
    t = history[0]
    assert t.question == "问题0"
    assert t.generated_sql == "SELECT 0"
    assert t.answer_summary == "答案0"
    assert "2026" in t.created_at


@pytest.mark.asyncio
async def test_get_history_invalid_json_returns_empty():
    redis = _make_redis()
    redis.get = AsyncMock(return_value=b"invalid json {{{")
    mgr = ConversationManager(
        redis_client=redis,
        meta_session_factory=_meta_factory,
        settings=_make_settings(),
    )
    history = await mgr.get_history("test-conv")
    assert history == []


@pytest.mark.asyncio
async def test_get_history_missing_key_returns_empty():
    redis = _make_redis()
    redis.get = AsyncMock(return_value=b'[{"turn_index": 0, "other_field": "x"}]')
    mgr = ConversationManager(
        redis_client=redis,
        meta_session_factory=_meta_factory,
        settings=_make_settings(),
    )
    history = await mgr.get_history("test-conv")
    assert history == []


# ─── add_turn ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_turn_to_empty_conversation():
    mgr = _make_manager()
    await mgr.add_turn(
        conversation_id="test-conv",
        question="月销售额是多少",
        generated_sql="SELECT SUM(amount) FROM sales_orders",
        answer_summary="本月销售额为 1000 万元",
    )
    history = await mgr.get_history("test-conv")
    assert len(history) == 1
    assert history[0].turn_index == 0
    assert history[0].question == "月销售额是多少"


@pytest.mark.asyncio
async def test_add_turn_increments_index():
    turns_data = [_sample_turn_dict(0)]
    mgr = _make_manager(stored_turns=turns_data)
    await mgr.add_turn("test-conv", "问题1", "SELECT 1", "答案1")
    history = await mgr.get_history("test-conv")
    assert len(history) == 2
    assert history[1].turn_index == 1


@pytest.mark.asyncio
async def test_add_turn_writes_to_redis():
    redis = _make_redis()
    settings = _make_settings()
    mgr = ConversationManager(
        redis_client=redis,
        meta_session_factory=_meta_factory,
        settings=settings,
    )
    await mgr.add_turn("conv-1", "q", "SELECT 1", "a")
    redis.set.assert_awaited()


@pytest.mark.asyncio
async def test_add_turn_sets_ttl():
    redis = _make_redis()
    settings = _make_settings(ttl=3600)
    mgr = ConversationManager(
        redis_client=redis,
        meta_session_factory=_meta_factory,
        settings=settings,
    )
    await mgr.add_turn("conv-1", "q", "SELECT 1", "a")
    call_kwargs = redis.set.call_args.kwargs
    assert call_kwargs.get("ex") == 3600


# ─── 滑动窗口 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sliding_window_drops_oldest_turns():
    """max_turns=3，已有 3 轮，再加 1 轮 → 旧的第 0 轮被丢弃。"""
    turns_data = [_sample_turn_dict(i) for i in range(3)]
    mgr = _make_manager(stored_turns=turns_data, max_turns=3)
    await mgr.add_turn("test-conv", "新问题", "SELECT NEW", "新答案")
    history = await mgr.get_history("test-conv")
    assert len(history) == 3
    # 旧的第 0 轮（question="问题0"）应已被丢弃
    assert history[0].question == "问题1"
    assert history[2].question == "新问题"


@pytest.mark.asyncio
async def test_sliding_window_reindexes():
    """滑动窗口后 turn_index 应重新从 0 开始。"""
    turns_data = [_sample_turn_dict(i) for i in range(3)]
    mgr = _make_manager(stored_turns=turns_data, max_turns=3)
    await mgr.add_turn("test-conv", "新问题", "SELECT NEW", "新答案")
    history = await mgr.get_history("test-conv")
    for expected_idx, turn in enumerate(history):
        assert turn.turn_index == expected_idx


@pytest.mark.asyncio
async def test_no_sliding_window_within_limit():
    turns_data = [_sample_turn_dict(i) for i in range(5)]
    mgr = _make_manager(stored_turns=turns_data, max_turns=10)
    await mgr.add_turn("test-conv", "新问题", "SELECT NEW", "新答案")
    history = await mgr.get_history("test-conv")
    assert len(history) == 6


# ─── 持久化 ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_turn_called_with_valid_uuid():
    """合法 UUID 作为 conversation_id 时应调用 meta session 写入。"""
    conv_id = str(uuid.uuid4())
    persist_called = []

    @asynccontextmanager
    async def meta_factory():
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=lambda stmt: persist_called.append(stmt))
        session.commit = AsyncMock()
        yield session

    redis = _make_redis()
    mgr = ConversationManager(
        redis_client=redis,
        meta_session_factory=meta_factory,
        settings=_make_settings(),
    )
    await mgr.add_turn(conv_id, "q", "SELECT 1", "a")
    assert len(persist_called) == 1


@pytest.mark.asyncio
async def test_persist_skipped_for_non_uuid():
    """非 UUID 的 conversation_id 应跳过持久化，不抛出异常。"""
    persist_called = []

    @asynccontextmanager
    async def meta_factory():
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=lambda stmt: persist_called.append(stmt))
        session.commit = AsyncMock()
        yield session

    redis = _make_redis()
    mgr = ConversationManager(
        redis_client=redis,
        meta_session_factory=meta_factory,
        settings=_make_settings(),
    )
    await mgr.add_turn("not-a-uuid", "q", "SELECT 1", "a")
    assert len(persist_called) == 0


@pytest.mark.asyncio
async def test_persist_failure_does_not_raise():
    """持久化失败时，add_turn 不应抛出异常（静默失败）。"""
    @asynccontextmanager
    async def failing_meta():
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=Exception("DB down"))
        session.commit = AsyncMock()
        yield session

    redis = _make_redis()
    mgr = ConversationManager(
        redis_client=redis,
        meta_session_factory=failing_meta,
        settings=_make_settings(),
    )
    conv_id = str(uuid.uuid4())
    # 不应抛出异常
    await mgr.add_turn(conv_id, "q", "SELECT 1", "a")


# ─── build_contextual_question ───────────────────────────────────────────────


def test_contextual_question_no_history():
    mgr = _make_manager()
    q = mgr.build_contextual_question([], "本月销售额是多少")
    assert q == "本月销售额是多少"


def test_contextual_question_no_reference_words():
    history = [
        Turn(0, "上月销售额", "SELECT SUM(amount)", "100万", "2026-03-27T00:00:00+00:00"),
    ]
    mgr = _make_manager()
    q = mgr.build_contextual_question(history, "本月销售额是多少")
    # 没有指代词，直接返回原问题
    assert q == "本月销售额是多少"


def test_contextual_question_with_reference_word_它():
    history = [
        Turn(0, "上月销售额", "SELECT SUM(amount)", "100万元", "2026-03-27T00:00:00+00:00"),
    ]
    mgr = _make_manager()
    q = mgr.build_contextual_question(history, "它比上上月增长了多少")
    assert "历史对话" in q
    assert "它比上上月增长了多少" in q
    assert "100万元" in q


def test_contextual_question_with_reference_word_这个():
    history = [
        Turn(0, "最大客户是谁", "SELECT client FROM ...", "客户A", "2026-03-27T00:00:00+00:00"),
    ]
    mgr = _make_manager()
    q = mgr.build_contextual_question(history, "这个客户的订单量")
    assert "历史对话" in q
    assert "这个客户的订单量" in q


def test_contextual_question_with_reference_word_该():
    history = [Turn(0, "Q", "SQL", "A", "2026-03-27T00:00:00+00:00")]
    mgr = _make_manager()
    q = mgr.build_contextual_question(history, "该数据的详细信息")
    assert "历史对话" in q


def test_contextual_question_with_reference_word_刚才():
    history = [Turn(0, "Q", "SQL", "A", "2026-03-27T00:00:00+00:00")]
    mgr = _make_manager()
    q = mgr.build_contextual_question(history, "刚才的结果是什么意思")
    assert "历史对话" in q


def test_contextual_question_uses_at_most_3_recent_turns():
    """有 5 轮历史，应只取最近 3 轮附加上下文。"""
    history = [
        Turn(i, f"问题{i}", f"SQL{i}", f"答案{i}", "2026-03-27T00:00:00+00:00")
        for i in range(5)
    ]
    mgr = _make_manager()
    q = mgr.build_contextual_question(history, "它是什么")
    # 问题0、1 是旧的轮，应该不在输出中
    assert "问题0" not in q
    assert "问题1" not in q
    # 最近 3 轮（2、3、4）应该在输出中
    assert "问题2" in q
    assert "问题3" in q
    assert "问题4" in q


def test_contextual_question_format():
    """验证输出格式：【历史对话】...【当前问题】..."""
    history = [Turn(0, "前一个问题", "SELECT 1", "答案是1", "2026-03-27T00:00:00+00:00")]
    mgr = _make_manager()
    q = mgr.build_contextual_question(history, "它的原因是什么")
    assert q.startswith("【历史对话】")
    assert "【当前问题】它的原因是什么" in q
    assert "问：前一个问题" in q
    assert "答摘要：答案是1" in q


def test_all_reference_words_trigger_context():
    """验证所有指代词都能触发上下文附加。"""
    ref_words = ["它", "这个", "该", "上面", "刚才", "此", "这些", "那个"]
    history = [Turn(0, "Q", "SQL", "A", "2026-03-27T00:00:00+00:00")]
    mgr = _make_manager()
    for word in ref_words:
        question = f"查询{word}相关的数据"
        q = mgr.build_contextual_question(history, question)
        assert "历史对话" in q, f"指代词 '{word}' 应触发上下文附加"
