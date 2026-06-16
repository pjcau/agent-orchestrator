"""Agent — an autonomous unit that receives tasks, uses skills, returns results."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from .clarification import ClarificationManager
from .guardrails import GuardrailBlocked, GuardrailManager
from .loop_detection import LoopDetector, LoopStatus
from .metrics import MetricsRegistry
from .prompt_markers import inject_marker_sections
from .provider import Message, Provider, Role, ToolDefinition
from .skill import SkillRegistry
from .tool_recovery import recover_dangling_tool_calls

if TYPE_CHECKING:
    from .personalized_memory import PersonalizedMemory

logger = logging.getLogger(__name__)

# Skill / agent-host error codes that mean the tool is fundamentally
# unavailable in this environment: retrying with different arguments cannot
# fix it (a missing executable a sandbox/jail has no way to install). Used only
# to make the circuit-breaker's stop message actionable — it does not change
# WHEN the breaker fires (that is purely consecutive-failure count).
_UNRECOVERABLE_ENV_ERRORS = frozenset({"shell_spawn_failed"})


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STALLED = "stalled"
    ESCALATED = "escalated"
    WAITING_FOR_CLARIFICATION = "waiting_for_clarification"


@dataclass
class AgentConfig:
    name: str
    role: str  # system prompt
    provider_key: str  # key into provider registry
    tools: list[str] = field(default_factory=list)  # allowed skill names
    max_steps: int = 10
    max_retries_per_approach: int = 3
    timeout_seconds: float = 300.0
    escalation_provider_key: str | None = None  # cloud provider for escalation
    # Cap each tool result before it re-enters the LLM context. A single
    # large file_read / shell_exec can otherwise dominate the prompt for the
    # rest of the run (see docs/ago-cli-improvements.md, P1). 0 disables.
    max_tool_result_chars: int = 8000
    # Progressive context relief. A file_read / shell_exec result is usually only
    # needed for the next few steps; once it is older than the most recent
    # ``stale_tool_result_keep_recent`` TOOL messages the agent has already acted
    # on it, yet its verbatim bytes keep being re-sent on every later step (the
    # 962k-input / $0.34 test-fix turn on 2026-06-16 was cost-capped purely by
    # this accumulation). Each step, the content of stale tool results larger
    # than ``stale_tool_result_stub_over`` chars is replaced with a one-line stub
    # that still names the call — so the context SHRINKS as material becomes
    # irrelevant, instead of only being cut at the compaction threshold. The
    # agent can re-read deliberately if it truly needs the detail. 0 disables.
    stale_tool_result_keep_recent: int = 6
    stale_tool_result_stub_over: int = 1200
    # Mid-run context compaction (docs/ago-cli-improvements.md, P0). When the
    # context the provider just billed exceeds this many input tokens, the
    # oldest middle messages are elided — keeping task setup + recent turns —
    # so a long run stops re-sending its whole history on every step. The
    # cost of an uncompacted run grows ~quadratically. 0 disables.
    compaction_token_threshold: int = 60000
    # Keep the first N messages verbatim. 4 (not 2) so the first tool RESULT
    # survives: the head is [user task, assistant tool_call, tool result, …], so
    # keep_head=2 kept only the task + the first tool_call and dropped the first
    # result the moment compaction fired — losing early evidence at no cost
    # saving. The deterministic sweep in evals/context_benchmark.py (--sweep)
    # showed keep_head=4 retains the early fact with identical token cost.
    compaction_keep_head: int = 4
    compaction_keep_tail: int = 20  # most-recent messages to preserve verbatim
    # After compaction, retain at most this fraction of the threshold in
    # estimated tokens. The kept tail is sized *dynamically* to fit this
    # budget — a smaller threshold therefore keeps fewer recent messages, so
    # the context the next call sends scales with the threshold instead of a
    # fixed message count that can balloon far past it (the 140k-vs-60k gap in
    # docs/ago-cli-improvements.md). 0.6 leaves headroom for the next step's
    # additions before the threshold is re-crossed.
    compaction_target_ratio: float = 0.6
    # Never drop the tail below this many recent messages, even if they alone
    # exceed the token budget — the agent still needs its immediate context to
    # make progress. Tool results are already capped (max_tool_result_chars),
    # so a few recent messages stay bounded.
    compaction_min_keep_tail: int = 4
    # After an identical tool call (same name + arguments) has FAILED this
    # many times in a run, the next identical call is short-circuited with a
    # nudge instead of executed — so the agent stops re-issuing a doomed
    # command (e.g. a denied `rm`) and is steered to change approach
    # (docs/ago-cli-improvements.md, P2). 0 disables.
    max_tool_failures_per_approach: int = 2
    # Circuit breaker: stop after this many CONSECUTIVE tool failures with no
    # successful tool call in between — regardless of whether the failing calls
    # were identical. This catches the "grind" the identical-args back-off
    # above misses: the model keeps VARYING a doomed approach (e.g. trying to
    # obtain a tool the sandbox cannot provide — `cowsay` → `apt-get install` →
    # `pip install` → …), burning the whole step budget for nothing. Resets to
    # zero on any tool success, so a run making real progress is never cut.
    # 0 disables. (docs/ago-cli-improvements.md, P3 — unrecoverable jail spawn.)
    max_consecutive_tool_failures: int = 6


@dataclass
class Task:
    description: str
    context: dict[str, Any] = field(default_factory=dict)
    parent_task_id: str | None = None


@dataclass
class TaskResult:
    status: TaskStatus
    output: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    steps_taken: int = 0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost_usd: float = 0.0
    error: str | None = None
    provider_used: str | None = None
    escalated: bool = False


def cap_tool_result_content(text: str, limit: int) -> str:
    """Cap a tool result before it re-enters the LLM context.

    A single large ``file_read`` / ``shell_exec`` can otherwise dominate the
    prompt for the rest of an agent run, since every subsequent LLM call
    re-sends the whole accumulated history (see ``docs/ago-cli-improvements.md``,
    P1). This keeps a head+tail slice so both the start (headers, the command
    that ran, early errors) and the end (summaries, exit notes) survive, with
    an explicit marker naming how many characters were dropped.

    ``limit <= 0`` disables the cap. This is a *context* cap applied where the
    result is folded into the conversation — independent of the agent-host's
    10 MB transport cap.
    """
    if limit <= 0 or len(text) <= limit:
        return text
    marker = "\n…[truncated {n} chars]…\n"
    # Budget for the visible slices, leaving room for the worst-case marker.
    reserve = len(marker.format(n=len(text)))
    budget = max(limit - reserve, 0)
    # Head-heavy 2:1 split — the start of a tool result is usually the most
    # informative (command echo, first lines, error banners).
    head_len = (budget * 2) // 3
    tail_len = budget - head_len
    dropped = len(text) - head_len - tail_len
    head = text[:head_len]
    tail = text[-tail_len:] if tail_len > 0 else ""
    return f"{head}{marker.format(n=dropped)}{tail}"


def shrink_stale_tool_results(
    messages: list[Message],
    *,
    keep_recent: int,
    stub_over: int,
) -> tuple[list[Message], int]:
    """Stub the content of TOOL results older than the most recent ``keep_recent``.

    Within a run a tool result (a ``file_read`` body, a test/build log) is
    usually only needed for the next handful of steps; after the agent has acted
    on it the verbatim bytes are dead weight that every later LLM call re-sends,
    so context — and cost — grow with run length. This replaces the *content* of
    stale tool results larger than ``stub_over`` chars with a compact stub that
    still names the call and its original size, **keeping the message and its
    ``tool_call_id``** so provider tool-call pairing stays intact and the agent
    can re-read deliberately if it genuinely needs the detail.

    Unlike :func:`compact_messages` (a threshold-triggered head/tail elision),
    this is meant to run every step so the context *shrinks as material ages
    out* rather than only being cut once the threshold is crossed. The two
    compose: this trims the bulk continuously, compaction handles the rest.

    Returns ``(messages, shrunk_count)``. When nothing changes the original list
    is returned unchanged; otherwise a new list of (mostly shared) messages is
    returned and the inputs are never mutated.
    """
    if keep_recent < 0 or stub_over <= 0:
        return messages, 0
    tool_idxs = [i for i, m in enumerate(messages) if m.role == Role.TOOL]
    if len(tool_idxs) <= keep_recent:
        return messages, 0
    stale = set(tool_idxs[: len(tool_idxs) - keep_recent])
    from dataclasses import replace

    out: list[Message] = []
    shrunk = 0
    for i, m in enumerate(messages):
        content = m.content or ""
        if i in stale and len(content) > stub_over:
            first_line = content.splitlines()[0] if content else ""
            preview = first_line[:80]
            stub = f"[stale tool result elided — {len(content)} chars; began: {preview!r}]"
            out.append(replace(m, content=stub))
            shrunk += 1
        else:
            out.append(m)
    if shrunk == 0:
        return messages, 0
    return out, shrunk


def estimate_message_tokens(messages: list[Message]) -> int:
    """Cheap, provider-agnostic estimate of the tokens a message list costs.

    Used to size compaction *before* paying for an LLM call, so the context we
    send can be bounded dynamically rather than only reacting after a turn was
    already billed over the threshold. Approximates ~4 characters per token
    (close enough for byte-pair tokenizers on English/code) over message
    content plus serialized ``tool_calls`` arguments — the two fields that
    actually grow during a run. System prompt and tool schemas are excluded;
    the caller's threshold accounts for that fixed overhead via its ratio.
    """
    chars = 0
    for m in messages:
        chars += len(m.content or "")
        for tc in m.tool_calls or ():
            # Arguments are the variable part of a tool call (a pasted command,
            # a file path list); name/id are negligible.
            chars += len(str(tc.arguments))
    return chars // 4


def _dynamic_keep_tail(
    messages: list[Message],
    *,
    keep_head: int,
    keep_tail: int,
    token_budget: int,
    min_keep_tail: int,
) -> int:
    """Largest tail size in ``[min_keep_tail, keep_tail]`` that, together with
    the head, fits ``token_budget`` estimated tokens.

    Monotonic: a longer tail never costs fewer tokens, so we grow until it no
    longer fits and keep the last size that did. Falls back to ``min_keep_tail``
    when even that overflows the budget (the floor is honored over the budget —
    we never strand the agent without recent context).
    """
    n = len(messages)
    head_tokens = estimate_message_tokens(messages[:keep_head])
    max_tail = min(keep_tail, max(n - keep_head, 0))
    floor = min(min_keep_tail, max_tail)
    chosen = floor
    for size in range(floor, max_tail + 1):
        tail_tokens = estimate_message_tokens(messages[n - size :]) if size else 0
        if head_tokens + tail_tokens <= token_budget:
            chosen = size
        else:
            break
    return chosen


def compact_messages(
    messages: list[Message],
    *,
    keep_head: int,
    keep_tail: int,
    token_budget: int | None = None,
    min_keep_tail: int = 0,
) -> tuple[list[Message], int]:
    """Elide the middle of a long working context to control cost.

    Within a single agent run the message history only grows — every LLM
    call re-sends it in full, so cost/latency climb roughly quadratically
    with run length (see ``docs/ago-cli-improvements.md``, P0). This keeps
    the first ``keep_head`` messages (task description, injected context,
    any conversation history) and the last ``keep_tail`` messages (recent
    turns), replacing the span between them with a single summary marker.

    The kept tail never *starts* on a ``Role.TOOL`` message — that would
    orphan a tool response whose assistant ``tool_call`` was dropped, which
    most providers reject. (Dangling assistant tool_calls left in the head
    are repaired separately by :func:`recover_dangling_tool_calls`.)

    When ``token_budget`` is set, the tail size is chosen *dynamically* — the
    largest suffix (capped at ``keep_tail``, floored at ``min_keep_tail``)
    whose estimated tokens fit the budget — so the retained context scales with
    the budget instead of a fixed message count. This is what bounds the peak
    near the threshold: a few large recent messages trigger a smaller tail, and
    a count-based early return is skipped (few-but-huge messages still compact).

    Returns ``(new_messages, dropped_count)``; ``dropped_count == 0`` means
    nothing was elided and ``messages`` is returned unchanged.
    """
    n = len(messages)
    if keep_head < 0 or keep_tail < 0:
        return messages, 0

    if token_budget is not None and token_budget > 0:
        effective_keep_tail = _dynamic_keep_tail(
            messages,
            keep_head=keep_head,
            keep_tail=keep_tail,
            token_budget=token_budget,
            min_keep_tail=min_keep_tail,
        )
    else:
        # Count-based mode: nothing to do when the history is already short.
        if n <= keep_head + keep_tail + 1:
            return messages, 0
        effective_keep_tail = keep_tail

    # Walk the tail boundary forward off any leading TOOL messages so the
    # preserved tail begins on a USER/ASSISTANT message.
    tail_start = n - effective_keep_tail
    while tail_start < n and messages[tail_start].role == Role.TOOL:
        tail_start += 1
    if tail_start <= keep_head:
        return messages, 0

    dropped = messages[keep_head:tail_start]
    if not dropped:
        return messages, 0

    dropped_chars = sum(len(m.content or "") for m in dropped)
    marker = Message(
        role=Role.USER,
        content=(
            f"[context compacted: {len(dropped)} earlier messages "
            f"(~{dropped_chars} chars) elided to control cost]"
        ),
    )
    new_messages = messages[:keep_head] + [marker] + messages[tail_start:]
    return new_messages, len(dropped)


class Agent:
    """Provider-agnostic agent that executes tasks using skills."""

    def __init__(
        self,
        config: AgentConfig,
        provider: Provider,
        skill_registry: SkillRegistry,
        escalation_provider: Provider | None = None,
        loop_detector: LoopDetector | None = None,
        clarification_manager: ClarificationManager | None = None,
        metrics: MetricsRegistry | None = None,
        guardrails: GuardrailManager | None = None,
        emit_event: Any | None = None,
        personalized_memory: "PersonalizedMemory | None" = None,
        user_id: str | None = None,
    ):
        self.config = config
        self.provider = provider
        self.skills = skill_registry
        self.escalation_provider = escalation_provider
        self.loop_detector = loop_detector
        self.clarification_manager = clarification_manager
        self._metrics = metrics
        self._guardrails = guardrails
        self._emit_event = emit_event  # optional callable(event_type_str, data_dict)
        self._personalized_memory = personalized_memory
        self._user_id = user_id
        self._messages: list[Message] = []
        self._status: TaskStatus = TaskStatus.PENDING
        # Marker-based prompt sections. Applied to `config.role` every time
        # a system prompt is built. Use `set_prompt_section` to mutate.
        self._prompt_sections: dict[str, str] = {}

    def set_prompt_section(self, marker: str, content: str) -> None:
        """Update one named section of the system prompt.

        Markers are delimited inside the prompt by
        ``<!-- MARKER START -->`` / ``<!-- MARKER END -->`` comments. Setting
        the same marker twice replaces the block in place; no other sections
        are touched. This prevents configuration drift when multiple callers
        (agents, middlewares, humans) want to patch different parts of the
        system prompt independently.
        """
        self._prompt_sections[marker] = content
        if self._metrics is not None:
            self._metrics.counter(
                "marker_updates_total",
                "Total marker-section prompt updates",
                labels={"agent": self.config.name},
            ).inc()

    def build_system_prompt(self) -> str:
        """Return the effective system prompt with all marker sections applied.

        When both ``personalized_memory`` and ``user_id`` were supplied at
        construction time, a ``<user_profile>`` block is appended after any
        marker-injected sections.  The block is built synchronously from an
        in-process cache populated by :meth:`_refresh_user_profile_cache`.

        If the cache has not been populated yet (first call), the block is
        omitted rather than blocking on an async store read.  Call
        :meth:`prefetch_user_profile` before ``execute()`` when you need the
        block on the very first turn.
        """
        base = self.config.role
        if self._prompt_sections:
            base = inject_marker_sections(base, self._prompt_sections)

        profile_block = self._build_user_profile_block()
        if profile_block:
            base = base + "\n\n" + profile_block

        return base

    def _build_user_profile_block(self, top_n: int = 5) -> str:
        """Build the ``<user_profile>`` XML block from the in-process cache.

        Returns an empty string when no profile data is available.
        """
        if self._personalized_memory is None or not self._user_id:
            return ""
        entries = getattr(self, "_user_profile_cache", None)
        if not entries:
            return ""
        lines: list[str] = []
        for entry in entries[:top_n]:
            key = entry.get("key", "")
            value = entry.get("value", {})
            lines.append(f"  [{key}]: {value}")
        inner = "\n".join(lines)
        return f"<user_profile>\n{inner}\n</user_profile>"

    async def prefetch_user_profile(self, top_n: int = 5) -> None:
        """Pre-populate the user profile cache from the store.

        Call this once before the first ``execute()`` call when you want the
        ``<user_profile>`` block to appear on turn 1.  Subsequent calls
        refresh the cache (e.g. after a profile-extraction skill run).

        Safe to call even when ``personalized_memory`` or ``user_id`` is not
        set — in that case it is a no-op.
        """
        if self._personalized_memory is None or not self._user_id:
            return
        try:
            entries = await self._personalized_memory.list(self._user_id, limit=top_n)
            self._user_profile_cache: list[dict] = entries
        except Exception:
            logger.warning(
                "Agent '%s': failed to prefetch user profile for user '%s'",
                self.config.name,
                self._user_id,
                exc_info=True,
            )

    async def execute(
        self,
        task: Task,
        conversation_history: list[Message] | None = None,
        session_id: str | None = None,
    ) -> TaskResult:
        """Run the agent on a task with anti-stall enforcement and escalation.

        Args:
            task: The task to execute.
            conversation_history: Optional list of previous user/assistant
                messages to prepend for multi-turn context.
            session_id: Optional session identifier for loop detection.
                If not provided, loop detection is skipped.
        """
        result = await self._execute_with_provider(
            task,
            self.provider,
            conversation_history=conversation_history,
            session_id=session_id,
        )

        # Escalate to cloud if local stalled and escalation provider is available
        if result.status == TaskStatus.STALLED and self.escalation_provider:
            logger.info(
                "Agent %s stalled on %s, escalating to %s",
                self.config.name,
                self.provider.model_id,
                self.escalation_provider.model_id,
            )
            escalated_result = await self._execute_with_provider(
                task,
                self.escalation_provider,
                conversation_history=conversation_history,
                session_id=session_id,
            )
            escalated_result.escalated = True
            escalated_result.steps_taken += result.steps_taken
            escalated_result.total_tokens += result.total_tokens
            escalated_result.total_cost_usd += result.total_cost_usd
            if escalated_result.status == TaskStatus.STALLED:
                escalated_result.status = TaskStatus.STALLED
            return escalated_result

        return result

    async def _execute_with_provider(
        self,
        task: Task,
        provider: Provider,
        conversation_history: list[Message] | None = None,
        session_id: str | None = None,
    ) -> TaskResult:
        """Run the agent loop with a specific provider."""
        from .tracing import get_tracer

        tracer = get_tracer()
        span = tracer.start_span("agent.run")
        span.set_attribute("agent.name", self.config.name)
        span.set_attribute("agent.provider", provider.model_id)
        span.set_attribute("agent.max_steps", self.config.max_steps)

        self._messages: list[Message] = []
        self._status = TaskStatus.RUNNING

        # Prepend conversation history for multi-turn context
        if conversation_history:
            self._messages.extend(conversation_history)

        # Inject context from shared artifacts
        if task.context:
            context_str = "\n".join(f"[{k}]: {v}" for k, v in task.context.items())
            self._messages.append(
                Message(
                    role=Role.USER,
                    content=f"Available context:\n{context_str}",
                ),
            )

        self._messages.append(
            Message(role=Role.USER, content=task.description),
        )

        tool_defs = self._get_tool_definitions()
        steps = 0
        retry_counts: dict[str, int] = {}
        failure_counts: dict[str, int] = {}  # per-approach failures (P2 back-off)
        consecutive_failures = 0  # tool failures since the last success (breaker)
        streak_error_codes: list[str] = []  # codes seen in the current streak
        total_cost = 0.0
        total_tokens = 0
        last_input_tokens = 0  # context size the provider last billed (P0 trigger)
        compactions = 0
        start_time = time.monotonic()

        while steps < self.config.max_steps:
            # Timeout check
            elapsed = time.monotonic() - start_time
            if elapsed > self.config.timeout_seconds:
                result = TaskResult(
                    status=TaskStatus.STALLED,
                    output="Agent timed out",
                    steps_taken=steps,
                    total_tokens=total_tokens,
                    total_cost_usd=total_cost,
                    error=f"Timeout after {elapsed:.0f}s",
                    provider_used=provider.model_id,
                )
                span.set_attribute("agent.steps_taken", steps)
                span.set_attribute("agent.total_tokens", total_tokens)
                span.set_attribute("agent.total_cost_usd", total_cost)
                span.set_attribute("agent.status", result.status.value)
                span.end()
                return result

            # P0: compact the context BEFORE recovery (which then repairs any
            # tool_call left dangling by the elision) and before the next LLM
            # call so the savings land immediately. Trigger on EITHER the last
            # billed input (accurate, but one step late) OR a pre-call estimate
            # of the current history (catches a single step that ballooned past
            # the threshold before it is ever billed). Compaction then shrinks
            # the tail dynamically to a fraction of the threshold, so the next
            # call's context scales with the threshold rather than overshooting
            # to multiples of it (docs/ago-cli-improvements.md, P0 follow-up).
            threshold = self.config.compaction_token_threshold
            if threshold > 0:
                estimated_tokens = estimate_message_tokens(self._messages)
                if last_input_tokens > threshold or estimated_tokens > threshold:
                    target_budget = max(int(threshold * self.config.compaction_target_ratio), 1)
                    self._messages, dropped = compact_messages(
                        self._messages,
                        keep_head=self.config.compaction_keep_head,
                        keep_tail=self.config.compaction_keep_tail,
                        token_budget=target_budget,
                        min_keep_tail=self.config.compaction_min_keep_tail,
                    )
                    if dropped:
                        compactions += 1
                        last_input_tokens = 0  # re-measure on the next completion
                        logger.info(
                            "Context compacted: agent=%s dropped=%d messages "
                            "(trigger=%d tokens, target=%d, est_before=%d)",
                            self.config.name,
                            dropped,
                            threshold,
                            target_budget,
                            estimated_tokens,
                        )

            # Recover any dangling tool calls before sending to LLM
            self._messages = recover_dangling_tool_calls(
                self._messages,
                session_id=self.config.name,
            )

            # --- Guardrail: input check ---
            if self._guardrails:
                gr_in = await self._guardrails.run_input(self._messages)
                self._emit_guardrail_event("input", gr_in)
                if gr_in.action == "block":
                    raise GuardrailBlocked(
                        guardrail_name="GuardrailManager",
                        reason=gr_in.reason,
                        side="input",
                    )
                if gr_in.action == "redact" and gr_in.redacted_text is not None:
                    # Replace the last user message content with the redacted version.
                    # The redacted_text covers the combined content; we patch the last
                    # user message so the conversation stays structurally valid.
                    for i in range(len(self._messages) - 1, -1, -1):
                        if self._messages[i].role == Role.USER:
                            from dataclasses import replace as _dc_replace

                            self._messages[i] = _dc_replace(
                                self._messages[i], content=gr_in.redacted_text
                            )
                            break

            completion = await provider.traced_complete(
                messages=self._messages,
                tools=tool_defs if tool_defs else None,
                system=self.build_system_prompt(),
            )

            total_tokens += completion.usage.input_tokens + completion.usage.output_tokens
            total_cost += completion.usage.cost_usd
            last_input_tokens = completion.usage.input_tokens  # P0 compaction trigger
            steps += 1

            # --- Guardrail: output check ---
            if self._guardrails and completion.content:
                gr_out = await self._guardrails.run_output(completion.content)
                self._emit_guardrail_event("output", gr_out)
                if gr_out.action == "block":
                    raise GuardrailBlocked(
                        guardrail_name="GuardrailManager",
                        reason=gr_out.reason,
                        side="output",
                    )
                if gr_out.action == "redact" and gr_out.redacted_text is not None:
                    from dataclasses import replace as _dc_replace

                    completion = _dc_replace(completion, content=gr_out.redacted_text)

            # No tool calls — agent is done
            if not completion.tool_calls:
                result = TaskResult(
                    status=TaskStatus.COMPLETED,
                    output=completion.content,
                    steps_taken=steps,
                    total_tokens=total_tokens,
                    total_cost_usd=total_cost,
                    provider_used=provider.model_id,
                )
                span.set_attribute("agent.steps_taken", steps)
                span.set_attribute("agent.total_tokens", total_tokens)
                span.set_attribute("agent.total_cost_usd", total_cost)
                span.set_attribute("agent.compactions", compactions)
                span.set_attribute("agent.status", result.status.value)
                span.end()
                return result

            # Process tool calls
            self._messages.append(
                Message(
                    role=Role.ASSISTANT,
                    content=completion.content,
                    tool_calls=completion.tool_calls,
                )
            )

            for tool_call in completion.tool_calls:
                # Loop detection: check before executing
                if self.loop_detector and session_id:
                    loop_status = self.loop_detector.check(
                        session_id, tool_call.name, tool_call.arguments or {}
                    )
                    if loop_status == LoopStatus.HARD_STOP:
                        result = TaskResult(
                            status=TaskStatus.FAILED,
                            output=f"Loop detected: {tool_call.name} repeated too many times",
                            steps_taken=steps,
                            total_tokens=total_tokens,
                            total_cost_usd=total_cost,
                            error=f"Loop hard stop on {tool_call.name}",
                            provider_used=provider.model_id,
                        )
                        span.set_attribute("agent.steps_taken", steps)
                        span.set_attribute("agent.total_tokens", total_tokens)
                        span.set_attribute("agent.total_cost_usd", total_cost)
                        span.set_attribute("agent.status", result.status.value)
                        span.end()
                        return result

                # Anti-stall: check retry count
                approach_key = f"{tool_call.name}:{hash(str(tool_call.arguments))}"
                retry_counts[approach_key] = retry_counts.get(approach_key, 0) + 1

                if retry_counts[approach_key] > self.config.max_retries_per_approach:
                    result = TaskResult(
                        status=TaskStatus.STALLED,
                        output=f"Stalled: too many retries on {tool_call.name}",
                        steps_taken=steps,
                        total_tokens=total_tokens,
                        total_cost_usd=total_cost,
                        error=f"Max retries exceeded for approach: {approach_key}",
                        provider_used=provider.model_id,
                    )
                    span.set_attribute("agent.steps_taken", steps)
                    span.set_attribute("agent.total_tokens", total_tokens)
                    span.set_attribute("agent.total_cost_usd", total_cost)
                    span.set_attribute("agent.status", result.status.value)
                    span.end()
                    return result

                # P2: short-circuit a call whose identical arguments have
                # already failed enough times, instead of executing it again.
                # Re-issuing a doomed command burns steps + context; nudge the
                # model to change approach rather than stalling the whole run.
                prior_failures = failure_counts.get(approach_key, 0)
                if (
                    self.config.max_tool_failures_per_approach > 0
                    and prior_failures >= self.config.max_tool_failures_per_approach
                ):
                    self._messages.append(
                        Message(
                            role=Role.TOOL,
                            content=(
                                f"[not executed] This exact {tool_call.name} call has "
                                f"already failed {prior_failures} time(s) this run. Do "
                                f"not retry it — change the arguments or try a different "
                                f"approach."
                            ),
                            tool_call_id=tool_call.id,
                        )
                    )
                    continue

                # Track clarification status for pause/resume
                if tool_call.name == "ask_clarification":
                    self._status = TaskStatus.WAITING_FOR_CLARIFICATION

                skill_result = await self.skills.execute(tool_call.name, tool_call.arguments)

                if tool_call.name == "ask_clarification":
                    self._status = TaskStatus.RUNNING

                # Count failures per approach so the back-off above can fire,
                # and track the consecutive-failure streak for the breaker. Any
                # success means the agent is making progress — clear the streak.
                if not skill_result.success:
                    failure_counts[approach_key] = prior_failures + 1
                    consecutive_failures += 1
                    if skill_result.error:
                        streak_error_codes.append(skill_result.error)
                else:
                    consecutive_failures = 0
                    streak_error_codes.clear()

                self._messages.append(
                    Message(
                        role=Role.TOOL,
                        content=cap_tool_result_content(
                            str(skill_result), self.config.max_tool_result_chars
                        ),
                        tool_call_id=tool_call.id,
                    )
                )

                # Circuit breaker: too many failures in a row with no progress.
                # Stop instead of grinding through the rest of the step budget
                # on a doomed approach (P3). The message is actionable when the
                # failures look environmental (e.g. a tool missing from a jail
                # image) so the operator knows the real fix.
                if (
                    self.config.max_consecutive_tool_failures > 0
                    and consecutive_failures >= self.config.max_consecutive_tool_failures
                ):
                    distinct_codes = list(dict.fromkeys(streak_error_codes))
                    env_blocked = any(c in _UNRECOVERABLE_ENV_ERRORS for c in distinct_codes)
                    hint = (
                        " Some required tools appear unavailable in this "
                        "environment and cannot be installed here (e.g. a sandbox "
                        "or jail with no network/privileges); configure the "
                        "sandbox image to include them rather than retrying."
                        if env_blocked
                        else " Change strategy instead of repeating failing calls."
                    )
                    result = TaskResult(
                        status=TaskStatus.STALLED,
                        output=(
                            f"Stopped after {consecutive_failures} consecutive tool "
                            f"failures without progress.{hint}"
                        ),
                        steps_taken=steps,
                        total_tokens=total_tokens,
                        total_cost_usd=total_cost,
                        error=(
                            f"Circuit breaker: {consecutive_failures} consecutive "
                            f"tool failures (codes: {', '.join(distinct_codes) or 'n/a'})"
                        ),
                        provider_used=provider.model_id,
                    )
                    span.set_attribute("agent.steps_taken", steps)
                    span.set_attribute("agent.total_tokens", total_tokens)
                    span.set_attribute("agent.total_cost_usd", total_cost)
                    span.set_attribute("agent.compactions", compactions)
                    span.set_attribute("agent.status", result.status.value)
                    span.end()
                    return result

        result = TaskResult(
            status=TaskStatus.STALLED,
            output="Agent reached max steps without completing",
            steps_taken=steps,
            total_tokens=total_tokens,
            total_cost_usd=total_cost,
            error=f"Max steps ({self.config.max_steps}) reached",
            provider_used=provider.model_id,
        )
        span.set_attribute("agent.steps_taken", steps)
        span.set_attribute("agent.total_tokens", total_tokens)
        span.set_attribute("agent.total_cost_usd", total_cost)
        span.set_attribute("agent.status", result.status.value)
        span.end()
        return result

    def _emit_guardrail_event(self, side: str, result: Any) -> None:
        """Emit a guardrail event via the injected emitter (best-effort)."""
        if self._emit_event is None:
            return
        try:
            action = result.action
            if action == "block":
                event_type = "guardrail.blocked"
            elif action == "redact":
                event_type = "guardrail.redacted"
            else:
                event_type = "guardrail.checked"
            self._emit_event(
                event_type,
                {
                    "agent": self.config.name,
                    "side": side,
                    "action": action,
                    "reason": result.reason,
                },
            )
        except Exception:
            pass  # never let event emission break agent execution

    def _get_tool_definitions(self) -> list[ToolDefinition]:
        """Get tool definitions for allowed skills only."""
        return [
            ToolDefinition(
                name=skill.name,
                description=skill.description,
                parameters=skill.parameters,
            )
            for name in self.config.tools
            if (skill := self.skills.get(name)) is not None
        ]
