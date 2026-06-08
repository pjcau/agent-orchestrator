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
        total_cost = 0.0
        total_tokens = 0
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

                # Track clarification status for pause/resume
                if tool_call.name == "ask_clarification":
                    self._status = TaskStatus.WAITING_FOR_CLARIFICATION

                skill_result = await self.skills.execute(tool_call.name, tool_call.arguments)

                if tool_call.name == "ask_clarification":
                    self._status = TaskStatus.RUNNING

                self._messages.append(
                    Message(
                        role=Role.TOOL,
                        content=cap_tool_result_content(
                            str(skill_result), self.config.max_tool_result_chars
                        ),
                        tool_call_id=tool_call.id,
                    )
                )

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
