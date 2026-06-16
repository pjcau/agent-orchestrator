"""Tests for the P1 context-side tool-result cap in ``core.agent``.

See ``docs/ago-cli-improvements.md`` (P1 — "Tool outputs are not capped
before re-entering context"). The cap is applied where a tool result is
folded into the conversation, independent of the agent-host transport cap.
"""

import pytest

from agent_orchestrator.core.agent import (
    Agent,
    AgentConfig,
    Task,
    cap_tool_result_content,
    compact_messages,
)
from agent_orchestrator.core.provider import (
    Completion,
    Message,
    ModelCapabilities,
    Provider,
    Role,
    StreamChunk,
    ToolCall,
    Usage,
)
from agent_orchestrator.core.skill import Skill, SkillRegistry, SkillResult
from agent_orchestrator.core.tool_recovery import recover_dangling_tool_calls

# TaskStatus lives alongside the agent types.
from agent_orchestrator.core.agent import TaskStatus


class _ReadFileSkill(Skill):
    """Minimal skill so the agent loop has a tool to call."""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read a file"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    async def execute(self, params: dict) -> SkillResult:
        return SkillResult(success=True, output=f"contents of {params.get('path', '/')}")


def test_short_text_passes_through_unchanged():
    text = "small output"
    assert cap_tool_result_content(text, 8000) == text


def test_exactly_at_limit_is_unchanged():
    text = "x" * 100
    assert cap_tool_result_content(text, 100) == text


def test_zero_or_negative_limit_disables_cap():
    text = "y" * 50_000
    assert cap_tool_result_content(text, 0) == text
    assert cap_tool_result_content(text, -1) == text


def test_long_text_is_truncated_with_marker():
    text = "A" * 9000 + "B" * 9000  # 18 KB, distinct head/tail
    out = cap_tool_result_content(text, 8000)
    assert len(out) < len(text)
    assert "[truncated" in out
    # Head and tail are both preserved.
    assert out.startswith("A")
    assert out.rstrip().endswith("B")


def test_marker_reports_dropped_count_accurately():
    text = "Z" * 20_000
    limit = 5000
    out = cap_tool_result_content(text, limit)
    # The visible characters + the dropped count must reconstruct the total.
    visible = out.count("Z")
    # Marker format: "…[truncated {n} chars]…"
    import re

    m = re.search(r"truncated (\d+) chars", out)
    assert m is not None
    dropped = int(m.group(1))
    assert visible + dropped == len(text)


def test_capped_output_stays_near_limit():
    text = "Q" * 100_000
    limit = 4000
    out = cap_tool_result_content(text, limit)
    # Never exceeds the requested budget (marker reserve is subtracted up front).
    assert len(out) <= limit


# --- P0: mid-run context compaction ---------------------------------------


def _round(i: int) -> list[Message]:
    """A full tool round: assistant tool_call + its tool result."""
    call_id = f"call{i}"
    return [
        Message(
            role=Role.ASSISTANT,
            content="",
            tool_calls=[ToolCall(id=call_id, name="file_read", arguments={"n": i})],
        ),
        Message(role=Role.TOOL, content=f"result {i}", tool_call_id=call_id),
    ]


def _conversation(rounds: int) -> list[Message]:
    msgs = [Message(role=Role.USER, content="the task")]
    for i in range(rounds):
        msgs.extend(_round(i))
    return msgs


def test_compaction_noop_when_short():
    msgs = _conversation(2)  # 1 + 4 messages
    out, dropped = compact_messages(msgs, keep_head=2, keep_tail=20)
    assert dropped == 0
    assert out is msgs


def test_compaction_drops_middle_and_inserts_marker():
    msgs = _conversation(30)  # 61 messages
    out, dropped = compact_messages(msgs, keep_head=1, keep_tail=10)
    assert dropped > 0
    assert len(out) < len(msgs)
    # The task (head) survives and a marker is present.
    assert out[0].content == "the task"
    assert any("context compacted" in (m.content or "") for m in out)


def test_compaction_tail_never_starts_on_tool_message():
    # keep_tail chosen to land mid-round (on a TOOL message); the boundary
    # must walk forward so no orphan tool response leads the tail.
    msgs = _conversation(30)
    out, dropped = compact_messages(msgs, keep_head=1, keep_tail=11)
    assert dropped > 0
    # First message after the marker must not be a bare TOOL response.
    marker_idx = next(i for i, m in enumerate(out) if "context compacted" in (m.content or ""))
    assert out[marker_idx + 1].role != Role.TOOL


def test_compacted_history_is_provider_valid_after_recovery():
    # The full pipeline: compact, then recover dangling calls (as the loop
    # does). Every TOOL message must reference an assistant tool_call that
    # precedes it, and every assistant tool_call must have a response.
    msgs = _conversation(40)
    out, dropped = compact_messages(msgs, keep_head=2, keep_tail=15)
    assert dropped > 0
    out = recover_dangling_tool_calls(out, session_id="t")

    open_calls: set[str] = set()
    for m in out:
        if m.role == Role.ASSISTANT and m.tool_calls:
            open_calls.update(tc.id for tc in m.tool_calls)
        elif m.role == Role.TOOL:
            # No orphan tool responses: the id was opened by an earlier
            # assistant message.
            assert m.tool_call_id in open_calls, f"orphan tool result {m.tool_call_id}"
    # Every opened call eventually got a response (recovery guarantees this).
    responded = {m.tool_call_id for m in out if m.role == Role.TOOL}
    assert open_calls <= responded


def test_compaction_negative_keeps_are_safe():
    msgs = _conversation(10)
    out, dropped = compact_messages(msgs, keep_head=-1, keep_tail=5)
    assert dropped == 0
    assert out is msgs


# --- P0 follow-up: dynamic, threshold-scaled compaction --------------------


def _big_round(i: int, size: int) -> list[Message]:
    """A tool round whose result is ``size`` chars — to drive token estimates."""
    call_id = f"call{i}"
    return [
        Message(
            role=Role.ASSISTANT,
            content="",
            tool_calls=[ToolCall(id=call_id, name="file_read", arguments={"n": i})],
        ),
        Message(role=Role.TOOL, content="x" * size, tool_call_id=call_id),
    ]


def test_estimate_message_tokens_counts_content_and_args():
    from agent_orchestrator.core.agent import estimate_message_tokens

    msgs = [
        Message(role=Role.USER, content="a" * 40),  # 40 chars
        Message(
            role=Role.ASSISTANT,
            content="b" * 8,
            tool_calls=[ToolCall(id="c", name="t", arguments={"k": "v" * 12})],
        ),
    ]
    # ~4 chars/token over content + serialized args; just assert it scales and
    # is non-trivial rather than pinning an exact tokenizer value.
    est = estimate_message_tokens(msgs)
    assert est >= (40 + 8) // 4
    assert est > 0


def test_dynamic_budget_keeps_fewer_messages_for_lower_threshold():
    # Same history; a tighter token budget must retain a smaller tail. This is
    # the core property the user asked for: fewer tokens when the threshold is
    # lower.
    msgs = [Message(role=Role.USER, content="the task")]
    for i in range(20):
        msgs.extend(_big_round(i, size=400))  # ~100 tokens per result

    out_loose, dropped_loose = compact_messages(
        msgs, keep_head=1, keep_tail=20, token_budget=2000, min_keep_tail=2
    )
    out_tight, dropped_tight = compact_messages(
        msgs, keep_head=1, keep_tail=20, token_budget=400, min_keep_tail=2
    )
    assert dropped_tight > dropped_loose > 0
    # Tighter budget => fewer messages retained.
    assert len(out_tight) < len(out_loose)


def test_dynamic_budget_bounds_retained_tokens_near_target():
    from agent_orchestrator.core.agent import estimate_message_tokens

    msgs = [Message(role=Role.USER, content="head")]
    for i in range(30):
        msgs.extend(_big_round(i, size=400))  # ~100 tokens each, ~3000 total

    budget = 600
    out, dropped = compact_messages(
        msgs, keep_head=1, keep_tail=30, token_budget=budget, min_keep_tail=2
    )
    assert dropped > 0
    # Retained estimate stays bounded by the budget plus the small marker /
    # one boundary message of slack — never the full unbounded history.
    assert estimate_message_tokens(out) <= budget + 200
    assert estimate_message_tokens(out) < estimate_message_tokens(msgs)


def test_dynamic_min_keep_tail_is_honored_over_budget():
    # Each recent message alone exceeds the budget; the floor still keeps the
    # agent's immediate context rather than stranding it.
    msgs = [Message(role=Role.USER, content="head")]
    for i in range(10):
        msgs.extend(_big_round(i, size=4000))  # ~1000 tokens each

    out, dropped = compact_messages(
        msgs, keep_head=1, keep_tail=20, token_budget=100, min_keep_tail=3
    )
    assert dropped > 0
    # The floor keeps recent context rather than stranding the agent: the most
    # recent message always survives, and roughly min_keep_tail messages are
    # retained (the no-orphan TOOL-walk may shave at most one off the floor).
    assert out[-1] is msgs[-1]
    assert len(out) >= 1 + 1 + (3 - 1)


def test_dynamic_budget_compacts_few_but_huge_messages():
    # Count-based mode would early-return (only 5 messages), but a token budget
    # must still compact when those few messages are individually enormous.
    msgs = [
        Message(role=Role.USER, content="head"),
        *_big_round(0, size=40000),  # ~10k tokens
        *_big_round(1, size=40000),
    ]
    out, dropped = compact_messages(
        msgs, keep_head=1, keep_tail=20, token_budget=2000, min_keep_tail=1
    )
    assert dropped > 0


# --- P0: compaction fires inside the real agent loop -----------------------


class _BigContextProvider(Provider):
    """Reports a huge input-token count so the compaction trigger fires, then
    finishes after ``rounds`` tool calls."""

    def __init__(self, rounds: int):
        self._rounds = rounds
        self._n = 0

    @property
    def model_id(self) -> str:
        return "fake"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(max_context=1_000_000, max_output_tokens=1024)

    @property
    def input_cost_per_million(self) -> float:
        return 1.0

    @property
    def output_cost_per_million(self) -> float:
        return 2.0

    async def complete(self, messages, tools=None, system=None, **kw):
        self._n += 1
        usage = Usage(input_tokens=100_000, output_tokens=5, cost_usd=0.001)
        if self._n <= self._rounds:
            return Completion(
                content="working",
                tool_calls=[
                    ToolCall(id=f"tc{self._n}", name="read_file", arguments={"path": f"/{self._n}"})
                ],
                usage=usage,
            )
        return Completion(content="done", tool_calls=[], usage=usage)

    async def stream(self, messages, tools=None, system=None, **kw):
        yield StreamChunk(content="done", finish_reason="stop")


@pytest.mark.asyncio
async def test_loop_triggers_compaction(caplog):
    import logging

    registry = SkillRegistry()
    registry.register(_ReadFileSkill())
    config = AgentConfig(
        name="compacting-agent",
        role="test",
        provider_key="fake",
        tools=["read_file"],
        max_steps=12,
        compaction_token_threshold=1000,  # far below the provider's 100k
        compaction_keep_head=1,
        compaction_keep_tail=2,
    )
    agent = Agent(config, _BigContextProvider(rounds=8), registry)

    with caplog.at_level(logging.INFO):
        result = await agent.execute(Task(description="do the thing"))

    assert result.status == TaskStatus.COMPLETED
    assert any("Context compacted" in r.message for r in caplog.records)
    # The accumulated context was bounded, not left to grow with every step.
    assert len(agent._messages) < 12


class _BigReadFileSkill(Skill):
    """Returns a large result every call, so the working context would grow
    unbounded without compaction (mirrors a real file_read/shell_exec)."""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read a big file"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    async def execute(self, params: dict) -> SkillResult:
        return SkillResult(success=True, output="D" * 40_000)


class _TokenizerProvider(Provider):
    """Reports ``input_tokens`` equal to the *actual estimated size of the
    context it was handed* — a stand-in for a real tokenizer. This makes the
    per-step ``usage.input_tokens`` a faithful measure of "tokens we sent",
    so the test can assert the dynamic compaction actually bounds it."""

    def __init__(self, rounds: int):
        self._rounds = rounds
        self._n = 0
        self.reported_inputs: list[int] = []

    @property
    def model_id(self) -> str:
        return "fake-tok"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(max_context=1_000_000, max_output_tokens=1024)

    @property
    def input_cost_per_million(self) -> float:
        return 1.0

    @property
    def output_cost_per_million(self) -> float:
        return 2.0

    async def complete(self, messages, tools=None, system=None, **kw):
        from agent_orchestrator.core.agent import estimate_message_tokens

        sent = estimate_message_tokens(list(messages))
        self.reported_inputs.append(sent)
        self._n += 1
        usage = Usage(input_tokens=sent, output_tokens=5, cost_usd=0.001)
        if self._n <= self._rounds:
            return Completion(
                content="working",
                tool_calls=[
                    ToolCall(id=f"tc{self._n}", name="read_file", arguments={"path": f"/{self._n}"})
                ],
                usage=usage,
            )
        return Completion(content="done", tool_calls=[], usage=usage)

    async def stream(self, messages, tools=None, system=None, **kw):
        yield StreamChunk(content="done", finish_reason="stop")


@pytest.mark.asyncio
async def test_loop_dynamic_compaction_bounds_sent_context():
    # A long run whose every tool result is huge. Without the dynamic bound the
    # per-step context would climb without limit (the 140k→235k we saw live);
    # with it, the context the provider is handed stays near the threshold.
    registry = SkillRegistry()
    registry.register(_BigReadFileSkill())
    threshold = 4000
    config = AgentConfig(
        name="bounded-agent",
        role="test",
        provider_key="fake-tok",
        tools=["read_file"],
        max_steps=25,
        max_tool_result_chars=8000,  # P1 cap (~2k tokens per result)
        compaction_token_threshold=threshold,
        compaction_target_ratio=0.6,
        compaction_keep_head=1,
        compaction_keep_tail=20,
        compaction_min_keep_tail=2,
    )
    provider = _TokenizerProvider(rounds=20)
    agent = Agent(config, provider, registry)

    result = await agent.execute(Task(description="read many big files"))
    assert result.status == TaskStatus.COMPLETED

    peak = max(provider.reported_inputs)
    # The sent context is bounded to a small multiple of the threshold — it
    # never grows with run length. (One step's worth of fresh tool output can
    # sit on top of a target-sized retained context before the next compaction.)
    assert peak < threshold * 2.5, f"context not bounded: peak={peak}"
    # And it genuinely stayed flat: the last third of the run is no larger than
    # the first third (no monotonic climb).
    first_third = provider.reported_inputs[: len(provider.reported_inputs) // 3]
    last_third = provider.reported_inputs[-len(provider.reported_inputs) // 3 :]
    assert max(last_third) <= max(first_third) + threshold


# --- P2: back-off on repeatedly-failing commands ---------------------------


class _AlwaysFailSkill(Skill):
    """A skill that always fails, counting how many times it actually ran."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "rm"

    @property
    def description(self) -> str:
        return "remove (always denied here)"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    async def execute(self, params: dict) -> SkillResult:
        self.calls += 1
        return SkillResult(success=False, output=None, error="shell_denied")


class _RepeatSameCallProvider(Provider):
    """Always issues the identical failing tool call, never finishing."""

    @property
    def model_id(self) -> str:
        return "fake"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(max_context=4096, max_output_tokens=1024)

    @property
    def input_cost_per_million(self) -> float:
        return 1.0

    @property
    def output_cost_per_million(self) -> float:
        return 2.0

    async def complete(self, messages, tools=None, system=None, **kw):
        return Completion(
            content="deleting",
            tool_calls=[ToolCall(id="dup", name="rm", arguments={"path": "/dup"})],
            usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.001),
        )

    async def stream(self, messages, tools=None, system=None, **kw):
        yield StreamChunk(content="done", finish_reason="stop")


class _VaryingSpawnFailSkill(Skill):
    """A shell tool whose every call fails with `shell_spawn_failed` — a binary
    missing from the (jail) environment. Counts real executions."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "shell_exec"

    @property
    def description(self) -> str:
        return "run a shell command"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"argv": {"type": "array"}}}

    async def execute(self, params: dict) -> SkillResult:
        self.calls += 1
        return SkillResult(success=False, output=None, error="shell_spawn_failed")


class _VaryingFailProvider(Provider):
    """Simulates the grind: a DIFFERENT failing command every step (cowsay →
    apt-get → pip → …), so the identical-args back-off never trips."""

    def __init__(self) -> None:
        self._n = 0

    @property
    def model_id(self) -> str:
        return "fake"

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(max_context=4096, max_output_tokens=1024)

    @property
    def input_cost_per_million(self) -> float:
        return 1.0

    @property
    def output_cost_per_million(self) -> float:
        return 2.0

    async def complete(self, messages, tools=None, system=None, **kw):
        self._n += 1
        # Each attempt is a distinct command → distinct approach_key.
        argv = [["cowsay", "hi"], ["apt-get", "install", "cowsay"], ["pip", "install", "cowsay"]][
            self._n % 3
        ] + [f"#{self._n}"]
        return Completion(
            content="trying to get the tool",
            tool_calls=[ToolCall(id=f"c{self._n}", name="shell_exec", arguments={"argv": argv})],
            usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.001),
        )

    async def stream(self, messages, tools=None, system=None, **kw):
        yield StreamChunk(content="done", finish_reason="stop")


@pytest.mark.asyncio
async def test_circuit_breaker_stops_varying_failure_grind():
    skill = _VaryingSpawnFailSkill()
    registry = SkillRegistry()
    registry.register(skill)
    config = AgentConfig(
        name="breaker-agent",
        role="test",
        provider_key="fake",
        tools=["shell_exec"],
        max_steps=40,  # the grind would otherwise burn all of these
        max_retries_per_approach=99,
        max_tool_failures_per_approach=99,  # disable identical-args back-off
        max_consecutive_tool_failures=4,
    )
    agent = Agent(config, _VaryingFailProvider(), registry)

    result = await agent.execute(Task(description="run cowsay"))

    # Stopped by the breaker after ~4 failures — NOT after 40 steps.
    assert result.status == TaskStatus.STALLED
    assert "Circuit breaker" in (result.error or "")
    assert skill.calls == 4
    assert result.steps_taken < 40
    # Message is actionable: the failures were environmental (missing binary).
    assert "sandbox" in result.output or "jail" in result.output


@pytest.mark.asyncio
async def test_circuit_breaker_resets_on_success():
    # A success between failures must reset the streak so a healthy run that
    # occasionally retries is never cut short.
    class _Skill(Skill):
        def __init__(self):
            self.n = 0

        @property
        def name(self):
            return "shell_exec"

        @property
        def description(self):
            return "x"

        @property
        def parameters(self):
            return {"type": "object", "properties": {}}

        async def execute(self, params):
            self.n += 1
            # Fail, fail, succeed, repeat — never 3 in a row.
            ok = self.n % 3 == 0
            return SkillResult(success=ok, output="ok" if ok else None, error=None if ok else "x")

    class _Prov(_VaryingFailProvider):
        async def complete(self, messages, tools=None, system=None, **kw):
            self._n += 1
            if self._n > 12:  # finish so the test terminates
                return Completion(content="done", tool_calls=[], usage=Usage(1, 1, 0.0))
            return Completion(
                content="step",
                tool_calls=[
                    ToolCall(id=f"c{self._n}", name="shell_exec", arguments={"i": self._n})
                ],
                usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.0),
            )

    registry = SkillRegistry()
    registry.register(_Skill())
    config = AgentConfig(
        name="ok-agent",
        role="test",
        provider_key="fake",
        tools=["shell_exec"],
        max_steps=20,
        max_consecutive_tool_failures=3,  # would trip if streak never reset
    )
    agent = Agent(config, _Prov(), registry)
    result = await agent.execute(Task(description="work"))
    # The periodic success keeps the streak under 3, so the run completes
    # normally instead of being broken.
    assert result.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_repeated_failure_is_short_circuited():
    skill = _AlwaysFailSkill()
    registry = SkillRegistry()
    registry.register(skill)
    config = AgentConfig(
        name="backoff-agent",
        role="test",
        provider_key="fake",
        tools=["rm"],
        max_steps=8,
        max_retries_per_approach=99,  # let the failure back-off fire first
        max_tool_failures_per_approach=2,
    )
    agent = Agent(config, _RepeatSameCallProvider(), registry)

    await agent.execute(Task(description="delete the dup"))

    # The doomed command ran at most the failure threshold, then was
    # short-circuited rather than executed again and again.
    assert skill.calls == 2
    assert any(
        m.role == Role.TOOL and "[not executed]" in (m.content or "") for m in agent._messages
    )


@pytest.mark.asyncio
async def test_backoff_disabled_lets_command_rerun():
    skill = _AlwaysFailSkill()
    registry = SkillRegistry()
    registry.register(skill)
    config = AgentConfig(
        name="no-backoff-agent",
        role="test",
        provider_key="fake",
        tools=["rm"],
        max_steps=5,
        max_retries_per_approach=99,
        max_tool_failures_per_approach=0,  # disabled
    )
    agent = Agent(config, _RepeatSameCallProvider(), registry)

    await agent.execute(Task(description="delete the dup"))

    # With back-off off, the command runs every step (no short-circuit).
    assert skill.calls == 5
    assert not any("[not executed]" in (m.content or "") for m in agent._messages)


# ---------------------------------------------------------------------------
# Progressive context relief: shrink_stale_tool_results
# ---------------------------------------------------------------------------


def _msgs_with_tool_results(n: int, body_chars: int = 4000):
    """user task + n (assistant tool_call, tool result) pairs."""
    out = [Message(role=Role.USER, content="task")]
    for i in range(n):
        out.append(Message(role=Role.ASSISTANT, content=f"calling {i}"))
        out.append(Message(role=Role.TOOL, content="X" * body_chars, tool_call_id=f"tc-{i}"))
    return out


def test_shrink_stale_keeps_recent_tool_results_verbatim():
    from agent_orchestrator.core.agent import shrink_stale_tool_results

    msgs = _msgs_with_tool_results(10, body_chars=4000)
    out, shrunk = shrink_stale_tool_results(msgs, keep_recent=6, stub_over=1200)
    # 10 tool results, keep last 6 → 4 oldest stubbed
    assert shrunk == 4
    tool_msgs = [m for m in out if m.role == Role.TOOL]
    # the last 6 stay full
    assert all(len(m.content) == 4000 for m in tool_msgs[-6:])
    # the first 4 are stubbed and much smaller
    assert all("stale tool result elided" in m.content for m in tool_msgs[:4])
    assert all(len(m.content) < 200 for m in tool_msgs[:4])


def test_shrink_stale_preserves_tool_call_id():
    from agent_orchestrator.core.agent import shrink_stale_tool_results

    msgs = _msgs_with_tool_results(10, body_chars=4000)
    out, _ = shrink_stale_tool_results(msgs, keep_recent=6, stub_over=1200)
    stubbed = [m for m in out if m.role == Role.TOOL and "elided" in m.content]
    # pairing must survive so providers don't reject an orphaned result
    assert all(m.tool_call_id is not None for m in stubbed)


def test_shrink_stale_noop_when_few_tool_results():
    from agent_orchestrator.core.agent import shrink_stale_tool_results

    msgs = _msgs_with_tool_results(5, body_chars=4000)
    out, shrunk = shrink_stale_tool_results(msgs, keep_recent=6, stub_over=1200)
    assert shrunk == 0 and out is msgs


def test_shrink_stale_skips_small_results():
    from agent_orchestrator.core.agent import shrink_stale_tool_results

    # old results are below the stub_over threshold → left alone
    msgs = _msgs_with_tool_results(10, body_chars=300)
    out, shrunk = shrink_stale_tool_results(msgs, keep_recent=6, stub_over=1200)
    assert shrunk == 0 and out is msgs


def test_shrink_stale_disabled_by_zero():
    from agent_orchestrator.core.agent import shrink_stale_tool_results

    msgs = _msgs_with_tool_results(10, body_chars=4000)
    out, shrunk = shrink_stale_tool_results(msgs, keep_recent=6, stub_over=0)
    assert shrunk == 0 and out is msgs
