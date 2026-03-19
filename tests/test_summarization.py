"""Tests for configurable context summarization in ConversationManager.

Covers:
- MESSAGE_COUNT trigger fires at threshold
- TOKEN_COUNT trigger fires at threshold
- FRACTION trigger fires at threshold
- retain_last keeps recent messages
- Summary replaces old messages with single SystemMessage
- Disabled config never triggers
- Token estimator approximation
"""

import pytest

from agent_orchestrator.core.conversation import (
    ConversationManager,
    ConversationMessage,
    SummarizationConfig,
    SummarizationTrigger,
    estimate_tokens,
)


# --- Mock summarize function ---


async def mock_summarize(messages: list[dict]) -> str:
    """Return a short summary string for testing."""
    count = len(messages)
    return f"[Summary of {count} messages]"


# --- Helpers ---


def _make_messages(n: int) -> list[ConversationMessage]:
    """Create n alternating user/assistant messages."""
    msgs = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append(ConversationMessage(role=role, content=f"msg-{i}"))
    return msgs


async def echo_graph(messages: list[dict]) -> str:
    last_user = [m for m in messages if m["role"] == "user"][-1]
    return f"echo: {last_user['content']}"


# --- Token estimator tests ---


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_short_string(self):
        # "hello" = 5 chars -> 5 // 4 = 1
        assert estimate_tokens("hello") == 1

    def test_longer_string(self):
        text = "a" * 100
        assert estimate_tokens(text) == 25

    def test_approximation_reasonable(self):
        # English text: roughly 4 chars per token
        text = "The quick brown fox jumps over the lazy dog"
        tokens = estimate_tokens(text)
        # 43 chars -> 10 tokens (actual GPT tokenizer gives ~10)
        assert 8 <= tokens <= 12


# --- MESSAGE_COUNT trigger tests ---


class TestMessageCountTrigger:
    @pytest.mark.asyncio
    async def test_fires_at_threshold(self):
        """Summarization should fire when message count >= threshold."""
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.MESSAGE_COUNT,
            threshold=6,
            retain_last=2,
        )
        mgr = ConversationManager(
            summarization_config=cfg,
            summarize_func=mock_summarize,
        )

        # Populate 4 messages directly (below threshold)
        await mgr._save_thread("t1", _make_messages(4))

        # Send message -> 5 messages (user appended) -> below 6
        r1 = await mgr.send("t1", "hello-4", echo_graph)
        assert r1.success
        assert mgr.summarization_count == 0

        # Send again -> 6 messages (incl new user) -> triggers at 6
        # After r1 we have 5 messages (4 + user + assistant = 6 actually)
        # Let's check: _save_thread put 4, send appends user (5), no trigger,
        # graph runs, assistant appended (6), saved.
        # Next send loads 6, appends user (7) -> triggers at >=6
        r2 = await mgr.send("t1", "hello-5", echo_graph)
        assert r2.success
        assert mgr.summarization_count == 1

    @pytest.mark.asyncio
    async def test_below_threshold_no_trigger(self):
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.MESSAGE_COUNT,
            threshold=100,
            retain_last=2,
        )
        mgr = ConversationManager(
            summarization_config=cfg,
            summarize_func=mock_summarize,
        )
        await mgr.send("t1", "hello", echo_graph)
        await mgr.send("t1", "world", echo_graph)
        assert mgr.summarization_count == 0


# --- TOKEN_COUNT trigger tests ---


class TestTokenCountTrigger:
    @pytest.mark.asyncio
    async def test_fires_at_threshold(self):
        """Summarization fires when total estimated tokens >= threshold."""
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.TOKEN_COUNT,
            threshold=20,  # 20 tokens -> 80 chars
            retain_last=2,
        )
        mgr = ConversationManager(
            summarization_config=cfg,
            summarize_func=mock_summarize,
        )

        # Each "x" * 40 = 10 tokens. 3 messages = 30 tokens
        long_msgs = [
            ConversationMessage(role="user", content="x" * 40),
            ConversationMessage(role="assistant", content="y" * 40),
            ConversationMessage(role="user", content="z" * 40),
        ]
        await mgr._save_thread("t1", long_msgs)

        # Next send: loads 3 msgs (30 tokens) + appends user msg -> >=20 tokens -> triggers
        r = await mgr.send("t1", "more content here with enough", echo_graph)
        assert r.success
        assert mgr.summarization_count == 1

    @pytest.mark.asyncio
    async def test_below_threshold_no_trigger(self):
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.TOKEN_COUNT,
            threshold=10000,
            retain_last=2,
        )
        mgr = ConversationManager(
            summarization_config=cfg,
            summarize_func=mock_summarize,
        )
        await mgr.send("t1", "short", echo_graph)
        assert mgr.summarization_count == 0


# --- FRACTION trigger tests ---


class TestFractionTrigger:
    @pytest.mark.asyncio
    async def test_fires_at_threshold(self):
        """FRACTION trigger fires when messages >= max_history * fraction."""
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.FRACTION,
            threshold=0.5,  # 50% of max_history
            retain_last=2,
        )
        mgr = ConversationManager(
            max_history=10,
            summarization_config=cfg,
            summarize_func=mock_summarize,
        )

        # Populate 4 messages, then send -> 5 messages >= 10*0.5=5 -> triggers
        await mgr._save_thread("t1", _make_messages(4))
        r = await mgr.send("t1", "trigger", echo_graph)
        assert r.success
        assert mgr.summarization_count == 1

    @pytest.mark.asyncio
    async def test_no_trigger_without_max_history(self):
        """FRACTION trigger requires max_history > 0."""
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.FRACTION,
            threshold=0.5,
            retain_last=2,
        )
        mgr = ConversationManager(
            max_history=0,  # unlimited
            summarization_config=cfg,
            summarize_func=mock_summarize,
        )
        await mgr._save_thread("t1", _make_messages(10))
        r = await mgr.send("t1", "test", echo_graph)
        assert r.success
        assert mgr.summarization_count == 0

    @pytest.mark.asyncio
    async def test_below_threshold_no_trigger(self):
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.FRACTION,
            threshold=0.9,
            retain_last=2,
        )
        mgr = ConversationManager(
            max_history=100,
            summarization_config=cfg,
            summarize_func=mock_summarize,
        )
        await mgr.send("t1", "hello", echo_graph)
        assert mgr.summarization_count == 0


# --- retain_last tests ---


class TestRetainLast:
    @pytest.mark.asyncio
    async def test_keeps_recent_messages(self):
        """After summarization, the last retain_last messages are kept verbatim."""
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.MESSAGE_COUNT,
            threshold=4,
            retain_last=2,
        )

        summarize_calls = []

        async def tracking_summarize(messages: list[dict]) -> str:
            summarize_calls.append(messages)
            return "[Summary]"

        mgr = ConversationManager(
            summarization_config=cfg,
            summarize_func=tracking_summarize,
        )

        # Build up 4 messages then trigger
        await mgr._save_thread("t1", _make_messages(4))
        # Next send: loads 4, appends user (5) -> >=4 threshold, triggers
        # retain_last=2 means last 2 of the 5 messages are kept
        r = await mgr.send("t1", "new-msg", echo_graph)
        assert r.success
        assert mgr.summarization_count == 1

        # The summarize func should have received the OLD messages (all except last 2)
        assert len(summarize_calls) == 1
        summarized = summarize_calls[0]
        assert len(summarized) == 3  # 5 total - 2 retained = 3 summarized

    @pytest.mark.asyncio
    async def test_retain_last_larger_than_messages_no_crash(self):
        """If retain_last > message count, no summarization occurs."""
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.MESSAGE_COUNT,
            threshold=1,
            retain_last=100,
        )
        mgr = ConversationManager(
            summarization_config=cfg,
            summarize_func=mock_summarize,
        )
        r = await mgr.send("t1", "hello", echo_graph)
        assert r.success
        assert mgr.summarization_count == 0


# --- Summary replaces old messages with SystemMessage ---


class TestSummaryReplacement:
    @pytest.mark.asyncio
    async def test_summary_is_system_message(self):
        """After summarization, old messages are replaced by a single system message."""
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.MESSAGE_COUNT,
            threshold=4,
            retain_last=2,
        )
        mgr = ConversationManager(
            summarization_config=cfg,
            summarize_func=mock_summarize,
        )

        await mgr._save_thread("t1", _make_messages(4))

        # Trigger summarization by sending
        r = await mgr.send("t1", "trigger-msg", echo_graph)
        assert r.success

        # Check saved thread: should have [system_summary, recent..., assistant]
        history = await mgr.get_history("t1")
        # First message should be the system summary
        assert history[0].role == "system"
        assert "Summary" in history[0].content
        assert history[0].metadata.get("summarized_messages") == 3

    @pytest.mark.asyncio
    async def test_tokens_saved_tracked(self):
        """tokens_saved should reflect the difference before/after summarization."""
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.MESSAGE_COUNT,
            threshold=4,
            retain_last=2,
        )
        mgr = ConversationManager(
            summarization_config=cfg,
            summarize_func=mock_summarize,
        )

        # Use messages with known content lengths
        msgs = [
            ConversationMessage(role="user", content="a" * 100),
            ConversationMessage(role="assistant", content="b" * 100),
            ConversationMessage(role="user", content="c" * 100),
            ConversationMessage(role="assistant", content="d" * 100),
        ]
        await mgr._save_thread("t1", msgs)

        await mgr.send("t1", "final", echo_graph)
        assert mgr.summarization_count == 1
        assert mgr.tokens_saved > 0


# --- Disabled config ---


class TestDisabledSummarization:
    @pytest.mark.asyncio
    async def test_disabled_never_triggers(self):
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.MESSAGE_COUNT,
            threshold=1,  # would always trigger
            retain_last=1,
            enabled=False,
        )
        mgr = ConversationManager(
            summarization_config=cfg,
            summarize_func=mock_summarize,
        )
        await mgr._save_thread("t1", _make_messages(10))
        r = await mgr.send("t1", "hello", echo_graph)
        assert r.success
        assert mgr.summarization_count == 0

    @pytest.mark.asyncio
    async def test_no_summarize_func_never_triggers(self):
        """Even with config, no summarize_func means no summarization."""
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.MESSAGE_COUNT,
            threshold=1,
            retain_last=1,
        )
        mgr = ConversationManager(
            summarization_config=cfg,
            summarize_func=None,
        )
        await mgr._save_thread("t1", _make_messages(10))
        r = await mgr.send("t1", "hello", echo_graph)
        assert r.success
        assert mgr.summarization_count == 0


# --- Metrics integration ---


class TestSummarizationMetrics:
    def test_default_metrics_include_summarization(self):
        from agent_orchestrator.core.metrics import default_metrics

        reg = default_metrics()
        all_metrics = reg.get_all()

        # Check that summarization metrics exist
        assert "conversation_summarization_total" in all_metrics
        assert "conversation_tokens_saved" in all_metrics
        assert all_metrics["conversation_summarization_total"]["type"] == "counter"
        assert all_metrics["conversation_tokens_saved"]["type"] == "gauge"
