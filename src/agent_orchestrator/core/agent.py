"""Agent — an autonomous unit that receives tasks, uses skills, returns results."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .provider import Completion, Message, Provider, Role, ToolCall, ToolDefinition
from .skill import SkillRegistry


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STALLED = "stalled"


@dataclass
class AgentConfig:
    name: str
    role: str  # system prompt
    provider_key: str  # key into provider registry
    tools: list[str] = field(default_factory=list)  # allowed skill names
    max_steps: int = 10
    max_retries_per_approach: int = 3
    timeout_seconds: float = 300.0


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


class Agent:
    """Provider-agnostic agent that executes tasks using skills."""

    def __init__(
        self,
        config: AgentConfig,
        provider: Provider,
        skill_registry: SkillRegistry,
    ):
        self.config = config
        self.provider = provider
        self.skills = skill_registry
        self._messages: list[Message] = []

    async def execute(self, task: Task) -> TaskResult:
        """Run the agent on a task with anti-stall enforcement."""
        self._messages = [
            Message(role=Role.USER, content=task.description),
        ]

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
                return TaskResult(
                    status=TaskStatus.STALLED,
                    output="Agent timed out",
                    steps_taken=steps,
                    total_tokens=total_tokens,
                    total_cost_usd=total_cost,
                    error=f"Timeout after {elapsed:.0f}s",
                )

            completion = await self.provider.complete(
                messages=self._messages,
                tools=tool_defs if tool_defs else None,
                system=self.config.role,
            )

            total_tokens += completion.usage.input_tokens + completion.usage.output_tokens
            total_cost += completion.usage.cost_usd
            steps += 1

            # No tool calls — agent is done
            if not completion.tool_calls:
                return TaskResult(
                    status=TaskStatus.COMPLETED,
                    output=completion.content,
                    steps_taken=steps,
                    total_tokens=total_tokens,
                    total_cost_usd=total_cost,
                )

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
                    return TaskResult(
                        status=TaskStatus.STALLED,
                        output=f"Stalled: too many retries on {tool_call.name}",
                        steps_taken=steps,
                        total_tokens=total_tokens,
                        total_cost_usd=total_cost,
                        error=f"Max retries exceeded for approach: {approach_key}",
                    )

                result = await self.skills.execute(tool_call.name, tool_call.arguments)
                self._messages.append(
                    Message(
                        role=Role.TOOL,
                        content=str(result),
                        tool_call_id=tool_call.id,
                    )
                )

        return TaskResult(
            status=TaskStatus.STALLED,
            output="Agent reached max steps without completing",
            steps_taken=steps,
            total_tokens=total_tokens,
            total_cost_usd=total_cost,
            error=f"Max steps ({self.config.max_steps}) reached",
        )

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
