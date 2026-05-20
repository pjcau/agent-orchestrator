"""Tests for cost_usd aggregation in dashboard.graphs._aggregate_usage.

Guards the chat footer (model · elapsed · cost) by ensuring the backend
always emits a cost_usd field — zero for free/local providers, non-zero
for priced ones — so the frontend can render it without guessing.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_orchestrator.dashboard.graphs import _aggregate_usage


class _PricedProvider:
    """Minimal provider stub exposing the cost surface used by _aggregate_usage."""

    def __init__(self, input_cpm: float, output_cpm: float) -> None:
        self._in = input_cpm
        self._out = output_cpm

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return input_tokens * self._in / 1_000_000 + output_tokens * self._out / 1_000_000


def _make_result(input_tokens: int, output_tokens: int):
    """Build a fake graph result with one step carrying a _usage dict."""
    step = SimpleNamespace(
        state_after={"_usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}
    )
    return SimpleNamespace(state={}, steps=[step])


def test_aggregate_usage_returns_cost_for_priced_provider():
    """A non-zero priced provider must produce a non-zero cost_usd field."""
    provider = _PricedProvider(input_cpm=0.01, output_cpm=0.03)  # ling-2.6-flash pricing
    result = _make_result(input_tokens=1000, output_tokens=500)

    usage = _aggregate_usage(result, "ling-2.6-flash", provider)

    assert usage["input_tokens"] == 1000
    assert usage["output_tokens"] == 500
    # 1000 * 0.01 / 1M + 500 * 0.03 / 1M = 1e-5 + 1.5e-5 = 2.5e-5
    assert usage["cost_usd"] == round(2.5e-5, 6)
    assert usage["model"] == "ling-2.6-flash"


def test_aggregate_usage_zero_cost_for_free_provider():
    """Free models report cost_usd == 0 (still present, not missing)."""
    provider = _PricedProvider(input_cpm=0.0, output_cpm=0.0)
    result = _make_result(input_tokens=200, output_tokens=80)

    usage = _aggregate_usage(result, "gemma:free", provider)

    assert usage["cost_usd"] == 0.0
    # Field must exist so the frontend can distinguish "free" from "unknown"
    assert "cost_usd" in usage


def test_aggregate_usage_no_provider_defaults_to_zero():
    """When the provider is unavailable, cost defaults to 0 instead of raising."""
    result = _make_result(input_tokens=100, output_tokens=50)
    usage = _aggregate_usage(result, "unknown", provider=None)
    assert usage["cost_usd"] == 0.0


def test_aggregate_usage_swallows_provider_errors():
    """A misbehaving provider must not break the response — cost falls back to 0."""

    class _BrokenProvider:
        def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
            raise RuntimeError("pricing lookup failed")

    result = _make_result(input_tokens=10, output_tokens=5)
    usage = _aggregate_usage(result, "broken", _BrokenProvider())
    assert usage["cost_usd"] == 0.0


def test_aggregate_usage_picks_max_of_state_and_steps():
    """Token counts merge the run-level _usage and per-step _usage (existing contract)."""
    provider = _PricedProvider(input_cpm=1.0, output_cpm=2.0)
    step = SimpleNamespace(state_after={"_usage": {"input_tokens": 30, "output_tokens": 70}})
    result = SimpleNamespace(
        state={"_usage": {"input_tokens": 50, "output_tokens": 40}},
        steps=[step],
    )
    usage = _aggregate_usage(result, "m", provider)
    # max(50, 30) input, max(40, 70) output → 50 / 70
    assert usage["input_tokens"] == 50
    assert usage["output_tokens"] == 70
    # 50*1/1M + 70*2/1M = 50e-6 + 140e-6 = 190e-6
    assert usage["cost_usd"] == round(190e-6, 6)
