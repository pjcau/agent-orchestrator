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
