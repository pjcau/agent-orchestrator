"""Tests for per-provider hard call caps with auto-disable (UsageTracker)."""

from __future__ import annotations

import pytest

from agent_orchestrator.core.usage import (
    ProviderCallCapExceeded,
    UsageRecord,
    UsageTracker,
)


def _rec(provider: str, cost: float = 0.001) -> UsageRecord:
    return UsageRecord(
        provider=provider, model="m", input_tokens=10, output_tokens=5, cost_usd=cost
    )


class TestProviderCallCaps:
    def test_no_caps_never_disables(self):
        tracker = UsageTracker()
        for _ in range(100):
            tracker.record(_rec("openai"))
        assert not tracker.is_provider_disabled("openai")
        tracker.check_call_cap("openai")  # must not raise

    def test_provider_disabled_at_cap(self):
        tracker = UsageTracker(call_caps={"openai": 3})
        for _ in range(2):
            tracker.record(_rec("openai"))
        assert not tracker.is_provider_disabled("openai")
        tracker.record(_rec("openai"))  # third call hits the cap
        assert tracker.is_provider_disabled("openai")
        assert tracker.disabled_providers == frozenset({"openai"})

    def test_check_call_cap_raises_when_disabled(self):
        tracker = UsageTracker(call_caps={"openai": 1})
        tracker.record(_rec("openai"))
        with pytest.raises(ProviderCallCapExceeded) as exc:
            tracker.check_call_cap("openai")
        assert exc.value.provider == "openai"
        assert exc.value.cap == 1

    def test_cap_is_per_provider(self):
        tracker = UsageTracker(call_caps={"openai": 1})
        tracker.record(_rec("openai"))
        tracker.record(_rec("anthropic"))
        assert tracker.is_provider_disabled("openai")
        assert not tracker.is_provider_disabled("anthropic")
        tracker.check_call_cap("anthropic")  # unaffected

    def test_call_counts_tracked(self):
        tracker = UsageTracker()
        tracker.record(_rec("a"))
        tracker.record(_rec("a"))
        tracker.record(_rec("b"))
        assert tracker.get_call_count("a") == 2
        assert tracker.get_call_count("b") == 1
        assert tracker.get_call_count("unknown") == 0

    def test_reset_provider_rearms(self):
        tracker = UsageTracker(call_caps={"openai": 1})
        tracker.record(_rec("openai"))
        assert tracker.is_provider_disabled("openai")
        tracker.reset_provider("openai")
        assert not tracker.is_provider_disabled("openai")
        assert tracker.get_call_count("openai") == 0
        tracker.check_call_cap("openai")  # must not raise

    def test_disable_logs_warning(self, caplog):
        tracker = UsageTracker(call_caps={"openai": 1})
        with caplog.at_level("WARNING"):
            tracker.record(_rec("openai"))
        assert any("auto-disabled" in m for m in caplog.messages)

    def test_cost_accounting_unaffected_by_caps(self):
        tracker = UsageTracker(call_caps={"openai": 1})
        tracker.record(_rec("openai", cost=0.5))
        tracker.record(_rec("openai", cost=0.5))  # still recorded after disable
        assert tracker.get_session_cost() == pytest.approx(1.0)
        assert tracker.get_cost_by_provider()["openai"] == pytest.approx(1.0)
