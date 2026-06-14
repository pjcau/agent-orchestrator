"""Tests for the context-compaction benchmark (``evals.context_benchmark``).

These exercise the benchmark harness itself — that it drives the real agent
loop, that compaction measurably bounds the context, and that the metrics are
deterministic (the property the whole benchmark relies on).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from agent_orchestrator.core.evaluator import EvalCase, EvalRun, EvalScore, Evaluator
from agent_orchestrator.core.provider import (
    Completion,
    Message,
    ModelCapabilities,
    Provider,
    Role,
    StreamChunk,
    ToolCall,
    ToolDefinition,
    Usage,
)
from evals.context_benchmark import (
    DEFAULT_STRATEGIES,
    NEEDLE_EARLY,
    NEEDLE_LATE,
    BenchResult,
    RealResult,
    build_live_provider,
    format_real_table,
    format_sim_table,
    format_table,
    run_benchmark,
    run_one,
    run_real_one,
    run_sim_one,
    run_sweep,
)


async def test_compaction_lowers_peak_and_total_context():
    """Aggressive compaction must reduce both the peak and the cumulative
    context relative to no compaction — while still completing the run."""
    no = await run_one("no", {"compaction_token_threshold": 0}, rounds=30, read_chars=12_000)
    yes = await run_one(
        "yes",
        {"compaction_token_threshold": 8_000, "compaction_target_ratio": 0.6},
        rounds=30,
        read_chars=12_000,
    )
    assert no.completed and yes.completed  # correctness preserved
    assert yes.peak_input_tokens < no.peak_input_tokens
    assert yes.total_input_tokens < no.total_input_tokens
    assert yes.cost_usd < no.cost_usd


async def test_metrics_are_deterministic():
    """Token/step/cost figures are exact and identical run to run — without
    this the benchmark would be measuring noise, not the strategy."""
    a = await run_one("x", {"compaction_token_threshold": 8_000}, rounds=20, read_chars=12_000)
    b = await run_one("x", {"compaction_token_threshold": 8_000}, rounds=20, read_chars=12_000)
    assert (a.steps, a.peak_input_tokens, a.total_input_tokens, a.cost_usd) == (
        b.steps,
        b.peak_input_tokens,
        b.total_input_tokens,
        b.cost_usd,
    )


async def test_no_compaction_total_grows_superlinearly():
    """The premise of the whole exercise: with no compaction the cumulative
    context climbs faster than the step count (each step re-sends the growing
    history). Doubling the rounds should more than double total input tokens."""
    short = await run_one("s", {"compaction_token_threshold": 0}, rounds=15, read_chars=12_000)
    long = await run_one("l", {"compaction_token_threshold": 0}, rounds=30, read_chars=12_000)
    assert long.total_input_tokens > 2 * short.total_input_tokens


async def test_run_benchmark_renders_table():
    rows = await run_benchmark(rounds=12, read_chars=9_000, trials=1)
    assert len(rows) == len(DEFAULT_STRATEGIES)
    assert all(isinstance(r, BenchResult) for r in rows)
    assert all(r.completed for r in rows)
    table = format_table(rows)
    assert "strategy" in table
    assert "no-compaction" in table


# --- real-mode plumbing (mock provider + fake judge; no live calls) ---------


class _CorrectMockProvider(Provider):
    """Drives the agent: reads f1..fN via file_read, then answers with both
    needle codes — a deterministic stand-in for a competent live model."""

    def __init__(self, n_files: int) -> None:
        self._n = n_files

    @property
    def model_id(self) -> str:
        return "mock-correct"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(max_context=100_000, supports_tools=True)

    @property
    def input_cost_per_million(self) -> float:
        return 1.0

    @property
    def output_cost_per_million(self) -> float:
        return 2.0

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        **kwargs: object,
    ) -> Completion:
        # Call index within THIS run = assistant turns already taken + 1, so the
        # mock is stateless across trials (mirrors a real provider being reused).
        n = sum(1 for m in messages if m.role == Role.ASSISTANT) + 1
        usage = Usage(input_tokens=100, output_tokens=10, cost_usd=0.0001)
        if n <= self._n:
            return Completion(
                content="reading",
                tool_calls=[
                    ToolCall(
                        id=f"c{n}",
                        name="file_read",
                        arguments={"path": f"f{n}.txt"},
                    )
                ],
                usage=usage,
            )
        return Completion(
            content=f"EARLY={NEEDLE_EARLY} LATE={NEEDLE_LATE}",
            tool_calls=[],
            usage=usage,
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        **kwargs: object,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="done", is_final=True)


class _ContainsJudge(Evaluator):
    """Deterministic judge: pass iff both needle codes appear in the output."""

    @property
    def name(self) -> str:
        return "contains-judge"

    async def evaluate(self, case: EvalCase, run: EvalRun) -> EvalScore:
        ok = NEEDLE_EARLY in run.agent_output and NEEDLE_LATE in run.agent_output
        return EvalScore(passed=ok, score=1.0 if ok else 0.0, detail="", evaluator=self.name)


async def test_real_mode_plumbing_scores_a_correct_run():
    res = await run_real_one(
        "no-compaction",
        {"compaction_token_threshold": 0},
        provider=_CorrectMockProvider(n_files=5),
        judge=_ContainsJudge(),
        n_files=5,
        filler_chars=400,
        trials=2,
    )
    assert isinstance(res, RealResult)
    assert res.trials == 2
    assert res.completed_rate == 1.0
    assert res.pass_rate == 1.0
    assert res.mean_correctness == 1.0
    assert res.mean_steps >= 5  # it read every file before answering
    table = format_real_table([res])
    assert "correct" in table and "no-compaction" in table


def test_build_live_provider_requires_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        build_live_provider("openrouter", "vendor/model")


# --- sweep / information-retention (the basis for the recommended default) ---


async def test_default_keep_head_retains_early_fact_under_compaction():
    """With the (new) default keep_head, the fact planted in the FIRST tool
    result survives even when compaction fires — the keep_head=4 fix."""
    r = await run_sim_one(
        "default",
        {"compaction_token_threshold": 8_000},  # low → forces compaction
        scenario="x",
        rounds=25,
        read_chars=12_000,
    )
    assert r.early_retained
    assert r.peak_tokens < 40_000  # sanity: compaction actually fired


async def test_keep_head_2_drops_early_fact_regression():
    """The old default (keep_head=2) dropped the first tool result on the first
    compaction — the regression this change fixes."""
    r = await run_sim_one(
        "head2",
        {"compaction_token_threshold": 8_000, "compaction_keep_head": 2},
        scenario="x",
        rounds=25,
        read_chars=12_000,
    )
    assert not r.early_retained


async def test_no_compaction_retains_every_fact():
    r = await run_sim_one(
        "none",
        {"compaction_token_threshold": 0},
        scenario="x",
        rounds=25,
        read_chars=12_000,
    )
    assert r.early_retained and r.mid_retained


async def test_run_sweep_grid_and_table():
    rows = await run_sweep(
        scenarios=[("s", 8, 6_000)],
        strategies=[
            ("none", {"compaction_token_threshold": 0}),
            ("agg", {"compaction_token_threshold": 4_000}),
        ],
    )
    assert len(rows) == 2
    assert rows[0].early_retained and rows[0].mid_retained  # no-compaction keeps all
    table = format_sim_table(rows)
    assert "early" in table and "[s]" in table
