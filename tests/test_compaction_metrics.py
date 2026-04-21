"""Tests that ConversationManager emits compaction metrics (PR #60)."""


from agent_orchestrator.core.conversation import (
    ConversationManager,
    ConversationMessage,
    SummarizationConfig,
    SummarizationTrigger,
)
from agent_orchestrator.core.metrics import MetricsRegistry


def _messages(n: int, size_chars: int = 200) -> list[ConversationMessage]:
    body = "x" * size_chars
    return [
        ConversationMessage(role="user" if i % 2 == 0 else "assistant", content=body)
        for i in range(n)
    ]


async def _summarise(msgs: list[dict]) -> str:
    # 20 chars ~= 5 tokens; huge savings relative to the input.
    return "summary"


class TestCompactionMetrics:
    async def test_summarization_records_counter(self):
        reg = MetricsRegistry()
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.MESSAGE_COUNT,
            threshold=10,
            retain_last=2,
        )
        mgr = ConversationManager(
            summarization_config=cfg,
            summarize_func=_summarise,
            metrics=reg,
        )
        msgs = _messages(10)
        await mgr.summarize_thread(msgs)

        count = reg.counter("conversation_summarization_total", "").get()
        assert count == 1

    async def test_tokens_saved_gauge_updated(self):
        reg = MetricsRegistry()
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.MESSAGE_COUNT, threshold=10, retain_last=2
        )
        mgr = ConversationManager(
            summarization_config=cfg, summarize_func=_summarise, metrics=reg
        )
        await mgr.summarize_thread(_messages(10))
        saved = reg.gauge("conversation_tokens_saved", "").get()
        assert saved > 0
        assert saved == mgr.tokens_saved

    async def test_compaction_ratio_between_0_and_1(self):
        reg = MetricsRegistry()
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.MESSAGE_COUNT, threshold=10, retain_last=2
        )
        mgr = ConversationManager(
            summarization_config=cfg, summarize_func=_summarise, metrics=reg
        )
        await mgr.summarize_thread(_messages(10))

        ratio_gauge = reg.gauge("conversation_compaction_ratio", "").get()
        assert 0.0 <= ratio_gauge < 1.0
        assert mgr.last_compaction_ratio == ratio_gauge

    async def test_messages_compacted_counter(self):
        reg = MetricsRegistry()
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.MESSAGE_COUNT, threshold=10, retain_last=2
        )
        mgr = ConversationManager(
            summarization_config=cfg, summarize_func=_summarise, metrics=reg
        )
        await mgr.summarize_thread(_messages(10))

        compacted = reg.counter("conversation_messages_compacted_total", "").get()
        # 10 messages - 2 retained = 8 folded into summary
        assert compacted == 8

    async def test_latency_histogram_records(self):
        reg = MetricsRegistry()
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.MESSAGE_COUNT, threshold=10, retain_last=2
        )
        mgr = ConversationManager(
            summarization_config=cfg, summarize_func=_summarise, metrics=reg
        )
        await mgr.summarize_thread(_messages(10))

        hist = reg.histogram("conversation_summarization_duration_seconds", "")
        assert hist.get_count() == 1
        assert hist.get_sum() >= 0.0

    async def test_metrics_are_cumulative_across_runs(self):
        reg = MetricsRegistry()
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.MESSAGE_COUNT, threshold=10, retain_last=2
        )
        mgr = ConversationManager(
            summarization_config=cfg, summarize_func=_summarise, metrics=reg
        )
        await mgr.summarize_thread(_messages(10))
        await mgr.summarize_thread(_messages(12))

        count = reg.counter("conversation_summarization_total", "").get()
        assert count == 2

    async def test_no_metrics_when_registry_absent(self):
        # Ensure code path without a registry still updates attributes.
        cfg = SummarizationConfig(
            trigger=SummarizationTrigger.MESSAGE_COUNT, threshold=10, retain_last=2
        )
        mgr = ConversationManager(
            summarization_config=cfg, summarize_func=_summarise, metrics=None
        )
        await mgr.summarize_thread(_messages(10))
        assert mgr.summarization_count == 1
        assert mgr.tokens_saved > 0
