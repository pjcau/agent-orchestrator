"""Benchmark in-turn context-compaction strategies on the *real* agent loop.

Answers the question "how correct and fast is a given context approach?" by
running the **same** scripted long run through ``core.Agent.execute`` under
several ``AgentConfig`` compaction settings and tabulating, per strategy:

    steps · peak input tokens · total input tokens · cost · wall time · ok

Why this lives at the core level (not in the Rust CLI): the conversation
context is assembled and grown **server-side**, inside the agent loop. The CLI
is a thin streaming client and cannot trim what it never sees, so the place to
prototype and *measure* a context strategy is here, where ``compact_messages``
runs (see ``docs/ago-cli-improvements.md``).

Deterministic and free by default: a scripted provider issues a fixed number of
tool calls (each growing the working context) and reports, as its input-token
*usage*, the actual estimated size of the message list it was handed — so the
recorded tokens/cost reflect exactly what compaction does or does not trim, with
no network spend and no flakiness. Token/step/cost figures are therefore
identical run to run; only wall time varies (averaged over ``--trials``).

``--real`` answers the correctness question deterministic mode cannot: a live
provider drives the loop and an ``LLMJudge`` scores each run on a
needle-in-a-haystack task (a code planted in the first file, another mid-run),
so a strategy that over-compacts shows up as falling correctness, not just
falling cost. Live runs are non-deterministic, hence ``--trials`` and reported
means.

Run:
    python -m evals.context_benchmark                       # deterministic, free
    python -m evals.context_benchmark --rounds 40 --json
    OPENROUTER_API_KEY=... python -m evals.context_benchmark --real --trials 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from typing import Any

from agent_orchestrator.core.agent import (
    Agent,
    AgentConfig,
    Task,
    TaskStatus,
    estimate_message_tokens,
)
from agent_orchestrator.core.evaluator import EvalCase, EvalRun, Evaluator, LLMJudge
from agent_orchestrator.core.provider import (
    Completion,
    Message,
    ModelCapabilities,
    Provider,
    StreamChunk,
    ToolCall,
    ToolDefinition,
    Usage,
)
from agent_orchestrator.core.skill import Skill, SkillRegistry, SkillResult

# Arbitrary but stable pricing so the cost column is a real, comparable number.
# Only the *relative* differences between strategies carry meaning here.
_INPUT_USD_PER_M = 1.0
_OUTPUT_USD_PER_M = 3.0


class _ScriptedProvider(Provider):
    """Deterministic, free stand-in for an LLM.

    Issues ``rounds`` tool calls (each a fresh ``file_read``) and then stops,
    reporting as its input-token usage the actual estimated size of the message
    list it was handed. That makes the recorded tokens/cost mirror precisely
    what the loop's compaction trims — no network, no non-determinism.
    """

    def __init__(self, rounds: int) -> None:
        self._rounds = rounds
        self._n = 0
        # Estimated input tokens for every LLM call, in order — the raw signal
        # the benchmark turns into peak/total context metrics.
        self.sent: list[int] = []

    @property
    def model_id(self) -> str:
        return "scripted-bench"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            max_context=1_000_000, supports_tools=True, supports_streaming=False
        )

    @property
    def input_cost_per_million(self) -> float:
        return _INPUT_USD_PER_M

    @property
    def output_cost_per_million(self) -> float:
        return _OUTPUT_USD_PER_M

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> Completion:
        sent = estimate_message_tokens(list(messages))
        self.sent.append(sent)
        self._n += 1
        usage = Usage(
            input_tokens=sent,
            output_tokens=5,
            cost_usd=self.estimate_cost(sent, 5),
        )
        if self._n <= self._rounds:
            return Completion(
                content="working",
                tool_calls=[
                    ToolCall(
                        id=f"c{self._n}",
                        name="file_read",
                        arguments={"path": f"f{self._n}.txt"},
                    )
                ],
                usage=usage,
            )
        return Completion(content="done", tool_calls=[], usage=usage)

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        yield StreamChunk(content="done", is_final=True)


class _GrowingReadSkill(Skill):
    """A ``file_read`` whose every result is ``chars`` bytes — the knob that
    grows the working context round over round (subject to the loop's own
    per-result cap, ``AgentConfig.max_tool_result_chars``)."""

    def __init__(self, chars: int) -> None:
        self._chars = chars

    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return "Read a file."

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    async def execute(self, params: dict) -> SkillResult:
        return SkillResult(success=True, output="D" * self._chars)


@dataclass
class BenchResult:
    """One strategy's measured outcome over the scripted run."""

    strategy: str
    steps: int
    peak_input_tokens: int
    total_input_tokens: int
    cost_usd: float
    wall_ms: float
    completed: bool


# (display name, AgentConfig compaction overrides). The first row is treated as
# the baseline for the Δ column, so keep "no-compaction" first.
DEFAULT_STRATEGIES: list[tuple[str, dict[str, Any]]] = [
    ("no-compaction", {"compaction_token_threshold": 0}),
    ("compact-60k", {"compaction_token_threshold": 60_000}),
    ("compact-30k", {"compaction_token_threshold": 30_000}),
    ("compact-15k", {"compaction_token_threshold": 15_000}),
    (
        "compact-15k-tight",
        {"compaction_token_threshold": 15_000, "compaction_target_ratio": 0.4},
    ),
]


async def run_one(
    name: str,
    overrides: dict[str, Any],
    *,
    rounds: int,
    read_chars: int,
) -> BenchResult:
    """Run the scripted scenario once under one compaction strategy."""
    provider = _ScriptedProvider(rounds=rounds)
    registry = SkillRegistry()
    registry.register(_GrowingReadSkill(read_chars))
    config = AgentConfig(
        name="bench",
        role="You are a benchmark agent. Keep reading files until told to stop.",
        provider_key="scripted",
        tools=["file_read"],
        # Give the loop room to reach the scripted "done" without the step cap
        # itself ending the run — we are measuring context, not step limits.
        max_steps=rounds + 5,
        **overrides,
    )
    agent = Agent(config, provider, registry)

    t0 = time.perf_counter()
    result = await agent.execute(Task(description="Read the files."))
    wall_ms = (time.perf_counter() - t0) * 1000.0

    sent = provider.sent or [0]
    return BenchResult(
        strategy=name,
        steps=result.steps_taken,
        peak_input_tokens=max(sent),
        total_input_tokens=sum(sent),
        cost_usd=result.total_cost_usd,
        wall_ms=wall_ms,
        completed=result.status == TaskStatus.COMPLETED,
    )


async def run_benchmark(
    *,
    rounds: int,
    read_chars: int,
    trials: int,
    strategies: list[tuple[str, dict[str, Any]]] | None = None,
) -> list[BenchResult]:
    """Run every strategy and return one aggregated row each.

    Token/step/cost are deterministic, so they are taken from the first trial
    verbatim; only ``wall_ms`` is averaged across ``trials``.
    """
    strategies = strategies or DEFAULT_STRATEGIES
    rows: list[BenchResult] = []
    for name, overrides in strategies:
        runs = [
            await run_one(name, overrides, rounds=rounds, read_chars=read_chars)
            for _ in range(max(1, trials))
        ]
        base = runs[0]
        rows.append(
            BenchResult(
                strategy=base.strategy,
                steps=base.steps,
                peak_input_tokens=base.peak_input_tokens,
                total_input_tokens=base.total_input_tokens,
                cost_usd=base.cost_usd,
                wall_ms=sum(r.wall_ms for r in runs) / len(runs),
                completed=all(r.completed for r in runs),
            )
        )
    return rows


def format_table(rows: list[BenchResult]) -> str:
    """Render the rows as an aligned table, with Δtotal vs the first row."""
    baseline = rows[0].total_input_tokens if rows else 0
    header = (
        f"{'strategy':<20} {'steps':>5} {'peak_in':>9} {'total_in':>12} "
        f"{'Δtotal':>7} {'cost$':>9} {'wall_ms':>8} {'ok':>3}"
    )
    lines = [header, "-" * len(header)]
    for r in rows:
        delta = ""
        if baseline and r.total_input_tokens:
            pct = 100.0 * (r.total_input_tokens - baseline) / baseline
            delta = f"{pct:+.0f}%"
        ok = "OK" if r.completed else "XX"
        lines.append(
            f"{r.strategy:<20} {r.steps:>5} {r.peak_input_tokens:>9,} "
            f"{r.total_input_tokens:>12,} {delta:>7} {r.cost_usd:>9.4f} "
            f"{r.wall_ms:>8.1f} {ok:>3}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Real mode — live provider drives the loop, a judge scores correctness.
#
# Deterministic mode proves a strategy is *cheap*; it cannot prove it stays
# *correct*, because the scripted provider never reads the tool output. Real
# mode closes that gap with a needle-in-a-haystack scenario: a code is planted
# in the FIRST file (which compaction's kept head should preserve) and another
# in a MIDDLE file (which aggressive compaction may drop). A strategy that
# over-compacts gets cheaper but starts losing the late needle — the trade-off
# made visible. Non-deterministic, so every strategy runs `trials` times.
# ---------------------------------------------------------------------------

NEEDLE_EARLY = "EARLY-7Q2"
NEEDLE_LATE = "LATE-9X5"

_REAL_TASK = (
    "There are {n} files named f1.txt through f{n}.txt. Read them one at a time "
    "by calling file_read with the path, in order. Exactly two files contain a "
    "line of the form NEEDLE_EARLY=<code> or NEEDLE_LATE=<code>. After reading "
    "all of them, reply with a single line exactly: EARLY=<code> LATE=<code>."
)


class _HaystackReadSkill(Skill):
    """Serves f1..fN: mostly filler, but the early needle is in f1 and the late
    needle in the middle file — so dropping the middle during compaction costs
    the late needle (a measurable correctness hit)."""

    def __init__(self, n_files: int, filler_chars: int) -> None:
        self._n = n_files
        self._filler = filler_chars
        self._mid = max(2, n_files // 2)

    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return "Read a file by path (e.g. f1.txt)."

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    async def execute(self, params: dict) -> SkillResult:
        path = str(params.get("path", ""))
        digits = "".join(c for c in path if c.isdigit())
        idx = int(digits) if digits else 0
        body = f"# {path}\n" + ("filler line\n" * max(1, self._filler // 12))
        if idx == 1:
            body += f"NEEDLE_EARLY={NEEDLE_EARLY}\n"
        elif idx == self._mid:
            body += f"NEEDLE_LATE={NEEDLE_LATE}\n"
        return SkillResult(success=True, output=body)


@dataclass
class RealResult:
    """One strategy's outcome under a live run, averaged over trials."""

    strategy: str
    trials: int
    completed_rate: float
    mean_steps: float
    mean_total_tokens: float
    mean_cost_usd: float
    mean_wall_ms: float
    mean_correctness: float
    pass_rate: float


def build_live_provider(
    provider_type: str,
    model: str,
    *,
    openrouter_key: str | None = None,
    ollama_url: str | None = None,
) -> Provider:
    """Construct a real provider from the harness ``providers`` package.

    Kept here (not imported from the dashboard) so the benchmark depends only on
    the harness layer. Raises with a clear message when creds are missing.
    """
    if provider_type == "openrouter":
        from agent_orchestrator.providers.openrouter import OpenRouterProvider

        key = openrouter_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set — required for --real with --provider openrouter."
            )
        return OpenRouterProvider(model=model, api_key=key)

    from agent_orchestrator.providers.local import LocalProvider

    base = ollama_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    return LocalProvider(model=model, base_url=f"{base}/v1", context_size=32_768)


async def run_real_one(
    name: str,
    overrides: dict[str, Any],
    *,
    provider: Provider,
    judge: Evaluator,
    n_files: int,
    filler_chars: int,
    trials: int,
) -> RealResult:
    """Run one strategy `trials` times against a live provider, scoring each
    run's output with `judge`."""
    task = _REAL_TASK.format(n=n_files)
    case = EvalCase(
        prompt=task,
        expected=f"EARLY={NEEDLE_EARLY} LATE={NEEDLE_LATE}",
        rubric=(
            "Pass only if the reply reports BOTH needle codes correctly: "
            f"{NEEDLE_EARLY} and {NEEDLE_LATE}."
        ),
    )
    completed = 0
    steps: list[int] = []
    tokens: list[int] = []
    costs: list[float] = []
    walls: list[float] = []
    scores: list[float] = []
    passes = 0

    for _ in range(max(1, trials)):
        registry = SkillRegistry()
        registry.register(_HaystackReadSkill(n_files, filler_chars))
        config = AgentConfig(
            name="bench-real",
            role="You are a careful assistant that reads files and reports facts.",
            provider_key="live",
            tools=["file_read"],
            max_steps=n_files + 8,
            **overrides,
        )
        agent = Agent(config, provider, registry)

        t0 = time.perf_counter()
        result = await agent.execute(Task(description=task))
        walls.append((time.perf_counter() - t0) * 1000.0)

        completed += int(result.status == TaskStatus.COMPLETED)
        steps.append(result.steps_taken)
        # The loop tracks combined input+output as ``total_tokens``; the split
        # fields are not populated, so we report the combined figure.
        tokens.append(result.total_tokens)
        costs.append(result.total_cost_usd)

        run = EvalRun(
            case_id=name,
            agent_output=result.output or "",
            ok=result.status == TaskStatus.COMPLETED,
        )
        score = await judge.evaluate(case, run)
        scores.append(score.score)
        passes += int(score.passed)

    n = max(1, trials)
    return RealResult(
        strategy=name,
        trials=n,
        completed_rate=completed / n,
        mean_steps=sum(steps) / n,
        mean_total_tokens=sum(tokens) / n,
        mean_cost_usd=sum(costs) / n,
        mean_wall_ms=sum(walls) / n,
        mean_correctness=sum(scores) / n,
        pass_rate=passes / n,
    )


async def run_benchmark_real(
    *,
    provider: Provider,
    judge: Evaluator,
    n_files: int,
    filler_chars: int,
    trials: int,
    strategies: list[tuple[str, dict[str, Any]]] | None = None,
) -> list[RealResult]:
    strategies = strategies or DEFAULT_STRATEGIES
    return [
        await run_real_one(
            name,
            overrides,
            provider=provider,
            judge=judge,
            n_files=n_files,
            filler_chars=filler_chars,
            trials=trials,
        )
        for name, overrides in strategies
    ]


def format_real_table(rows: list[RealResult]) -> str:
    """Render real-mode rows — correctness alongside cost, the whole point."""
    header = (
        f"{'strategy':<20} {'steps':>6} {'tokens':>10} {'cost$':>9} "
        f"{'wall_ms':>9} {'correct':>8} {'pass':>6} {'done':>6}"
    )
    lines = [header, "-" * len(header)]
    for r in rows:
        lines.append(
            f"{r.strategy:<20} {r.mean_steps:>6.1f} {r.mean_total_tokens:>10,.0f} "
            f"{r.mean_cost_usd:>9.4f} {r.mean_wall_ms:>9.0f} "
            f"{r.mean_correctness:>8.2f} {r.pass_rate:>6.0%} {r.completed_rate:>6.0%}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sweep mode — deterministic cost AND correctness across scenarios.
#
# Real mode needs a paid LLM to score correctness. The sweep gets a correctness
# signal for free by measuring *information retention*: a fact is planted in the
# first tool result (an "early" fact) and another mid-run (a "mid" fact); after
# the run we inspect the final working context to see which survived compaction.
# A strategy that over-compacts shows up as a dropped fact — cost and
# correctness, both deterministic, across short/medium/long runs.
# ---------------------------------------------------------------------------

SIM_EARLY = "EARLY-FACT-7Q2"
SIM_MID = "MID-FACT-5K8"

# (scenario name, rounds, read_chars)
DEFAULT_SCENARIOS: list[tuple[str, int, int]] = [
    ("short", 10, 6_000),
    ("medium", 25, 12_000),
    ("long", 50, 12_000),
]


class _NeedleReadSkill(Skill):
    """Like ``_GrowingReadSkill`` but plants a retrievable fact at the **start**
    of the first result (survives the per-result cap's kept head) and another in
    the middle round — so the final context reveals what compaction kept."""

    def __init__(self, chars: int, rounds: int) -> None:
        self._chars = chars
        self._mid = max(2, rounds // 2)
        self._calls = 0

    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return "Read a file."

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    async def execute(self, params: dict) -> SkillResult:
        self._calls += 1
        body = "D" * self._chars
        if self._calls == 1:
            body = f"{SIM_EARLY}\n{body}"
        elif self._calls == self._mid:
            body = f"{SIM_MID}\n{body}"
        return SkillResult(success=True, output=body)


@dataclass
class SimResult:
    """Cost + information-retention for one strategy on one scenario."""

    strategy: str
    scenario: str
    rounds: int
    total_tokens: int
    peak_tokens: int
    cost_usd: float
    early_retained: bool
    mid_retained: bool


async def run_sim_one(
    name: str,
    overrides: dict[str, Any],
    *,
    scenario: str,
    rounds: int,
    read_chars: int,
) -> SimResult:
    """Run the scripted scenario with planted facts and report cost + which
    facts survived in the final context."""
    provider = _ScriptedProvider(rounds=rounds)
    registry = SkillRegistry()
    registry.register(_NeedleReadSkill(read_chars, rounds))
    config = AgentConfig(
        name="sim",
        role="You are a benchmark agent. Keep reading files until told to stop.",
        provider_key="scripted",
        tools=["file_read"],
        max_steps=rounds + 5,
        **overrides,
    )
    agent = Agent(config, provider, registry)
    await agent.execute(Task(description="Read the files."))

    # The loop compacts ``agent._messages`` in place, so the final list is the
    # context the model would still have at the end of the turn.
    final_ctx = "\n".join(m.content or "" for m in agent._messages)
    sent = provider.sent or [0]
    return SimResult(
        strategy=name,
        scenario=scenario,
        rounds=rounds,
        total_tokens=sum(sent),
        peak_tokens=max(sent),
        cost_usd=agent_total_cost(sent),
        early_retained=SIM_EARLY in final_ctx,
        mid_retained=SIM_MID in final_ctx,
    )


def agent_total_cost(sent: list[int]) -> float:
    """Cost of a run from its per-call input sizes, at the scripted pricing
    (output is a flat 5 tokens/call, as the scripted provider emits)."""
    return sum(s * _INPUT_USD_PER_M / 1_000_000 + 5 * _OUTPUT_USD_PER_M / 1_000_000 for s in sent)


async def run_sweep(
    *,
    scenarios: list[tuple[str, int, int]] | None = None,
    strategies: list[tuple[str, dict[str, Any]]] | None = None,
) -> list[SimResult]:
    scenarios = scenarios or DEFAULT_SCENARIOS
    strategies = strategies or DEFAULT_STRATEGIES
    rows: list[SimResult] = []
    for scenario, rounds, read_chars in scenarios:
        for name, overrides in strategies:
            rows.append(
                await run_sim_one(
                    name,
                    overrides,
                    scenario=scenario,
                    rounds=rounds,
                    read_chars=read_chars,
                )
            )
    return rows


def format_sim_table(rows: list[SimResult]) -> str:
    """Render the sweep grouped by scenario, with Δcost vs that scenario's
    no-compaction baseline and which planted facts survived."""
    by_scenario: dict[str, list[SimResult]] = {}
    for r in rows:
        by_scenario.setdefault(r.scenario, []).append(r)

    header = (
        f"{'strategy':<20} {'peak_tok':>9} {'total_tok':>11} {'cost$':>9} "
        f"{'Δcost':>7} {'early':>6} {'mid':>5}"
    )
    out: list[str] = []
    for scenario, group in by_scenario.items():
        base = group[0].cost_usd  # no-compaction is first in DEFAULT_STRATEGIES
        out.append(f"\n[{scenario}] {group[0].rounds} rounds")
        out.append(header)
        out.append("-" * len(header))
        for r in group:
            delta = f"{100.0 * (r.cost_usd - base) / base:+.0f}%" if base else ""
            out.append(
                f"{r.strategy:<20} {r.peak_tokens:>9,} {r.total_tokens:>11,} "
                f"{r.cost_usd:>9.4f} {delta:>7} "
                f"{('yes' if r.early_retained else 'NO'):>6} "
                f"{('yes' if r.mid_retained else 'NO'):>5}"
            )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark context-compaction strategies on the real agent loop."
    )
    parser.add_argument(
        "--rounds", type=int, default=40, help="tool-call rounds (how long the run is)"
    )
    parser.add_argument(
        "--read-chars",
        type=int,
        default=12_000,
        help="bytes each file_read returns (the context-growth knob)",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=3,
        help="repeat each strategy; only wall time is averaged (the rest is deterministic)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="deterministic cost + information-retention across short/medium/long "
        "scenarios (free; measures which planted facts survive each strategy)",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="run against a live provider + LLM judge, scoring semantic correctness "
        "(needle-in-haystack). Non-deterministic; uses --trials for repeats.",
    )
    parser.add_argument(
        "--provider",
        default="openrouter",
        help="provider type for --real (openrouter | local). Default: openrouter",
    )
    parser.add_argument(
        "--model",
        default="deepseek/deepseek-v4-flash",
        help="model id for the agent under test in --real mode",
    )
    parser.add_argument(
        "--judge-model",
        default=None,
        help="model id for the LLM judge in --real mode (defaults to --model)",
    )
    args = parser.parse_args(argv)

    # The core loop logs an INFO line every time it repairs a tool call orphaned
    # by compaction — healthy hygiene, but it drowns the table. Quiet it so the
    # benchmark output stays readable (callers of run_benchmark are unaffected).
    logging.getLogger("agent_orchestrator").setLevel(logging.ERROR)

    if args.sweep:
        sim_rows = asyncio.run(run_sweep())
        if args.json:
            print(json.dumps([asdict(r) for r in sim_rows], indent=2))
        else:
            print(format_sim_table(sim_rows))
            print(
                "\nDeterministic. 'early'/'mid' = whether the fact planted at the "
                "first / middle read survived compaction in the final context. "
                "The best strategy is the cheapest one that still keeps the facts "
                "your task needs."
            )
        return 0

    if args.real:
        try:
            provider = build_live_provider(args.provider, args.model)
            judge_provider = build_live_provider(args.provider, args.judge_model or args.model)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        judge = LLMJudge(judge_provider)
        real_rows = asyncio.run(
            run_benchmark_real(
                provider=provider,
                judge=judge,
                n_files=args.rounds,
                filler_chars=args.read_chars,
                trials=args.trials,
            )
        )
        if args.json:
            print(json.dumps([asdict(r) for r in real_rows], indent=2))
        else:
            print(format_real_table(real_rows))
            print(
                "\nReal mode: live runs are non-deterministic — figures are means over "
                f"{args.trials} trial(s). 'correct' is the judge's mean score, 'pass' "
                "its pass rate. Watch cost fall and correctness hold (or drop when a "
                "strategy over-compacts and loses the late needle)."
            )
        return 0

    rows = asyncio.run(
        run_benchmark(rounds=args.rounds, read_chars=args.read_chars, trials=args.trials)
    )
    if args.json:
        print(json.dumps([asdict(r) for r in rows], indent=2))
    else:
        print(format_table(rows))
        print(
            "\nDeterministic mode: token/step/cost are exact and identical run-to-run; "
            "only wall_ms varies.\nSemantic correctness needs a live run (see --real)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
