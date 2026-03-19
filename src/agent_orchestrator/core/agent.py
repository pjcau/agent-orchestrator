"""Agent — an autonomous unit that receives tasks, uses skills, returns results."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .clarification import ClarificationManager
from .provider import Message, Provider, Role, ToolDefinition
from .skill import SkillRegistry

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
    total_cost_usd: float = 0.0
    error: str | None = None
    provider_used: str | None = None
    escalated: bool = False


class Agent:
    """Provider-agnostic agent that executes tasks using skills."""

    def __init__(
        self,
        config: AgentConfig,
        provider: Provider,
        skill_registry: SkillRegistry,
        escalation_provider: Provider | None = None,
        clarification_manager: ClarificationManager | None = None,
    ):
        self.config = config
        self.provider = provider
        self.skills = skill_registry
        self.escalation_provider = escalation_provider
        self.clarification_manager = clarification_manager
        self._messages: list[Message] = []
        self._status: TaskStatus = TaskStatus.PENDING

    async def execute(
        self,
        task: Task,
        conversation_history: list[Message] | None = None,
    ) -> TaskResult:
        """Run the agent on a task with anti-stall enforcement and escalation.

        Args:
            task: The task to execute.
            conversation_history: Optional list of previous user/assistant
                messages to prepend for multi-turn context.
        """
        result = await self._execute_with_provider(
            task,
            self.provider,
            conversation_history=conversation_history,
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

            completion = await provider.traced_complete(
                messages=self._messages,
                tools=tool_defs if tool_defs else None,
                system=self.config.role,
            )

            total_tokens += completion.usage.input_tokens + completion.usage.output_tokens
            total_cost += completion.usage.cost_usd
            steps += 1

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
                        content=str(skill_result),
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
