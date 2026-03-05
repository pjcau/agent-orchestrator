"""Agent runner for the dashboard.

Wraps Agent.execute() with real-time EventBus emissions so the dashboard
can show agent spawning, tool calls, tool results, and completion live.
"""

from __future__ import annotations

import time
from typing import Any

from ..core.agent import AgentConfig, Task, TaskResult, TaskStatus
from ..core.provider import Message, Provider, Role, ToolDefinition
from ..core.skill import SkillRegistry
from ..skills import FileReadSkill, FileWriteSkill, GlobSkill, ShellExecSkill
from .events import Event, EventBus, EventType


def create_skill_registry(
    allowed_commands: list[str] | None = None,
) -> SkillRegistry:
    """Create a skill registry with all built-in skills."""
    registry = SkillRegistry()
    registry.register(FileReadSkill())
    registry.register(FileWriteSkill())
    registry.register(GlobSkill())
    registry.register(ShellExecSkill(allowed_commands=allowed_commands))
    return registry


async def run_agent(
    agent_name: str,
    task_description: str,
    provider: Provider,
    role: str = "",
    tools: list[str] | None = None,
    max_steps: int = 10,
    event_bus: EventBus | None = None,
) -> dict[str, Any]:
    """Run an agent on a task with real-time event emissions.

    Returns a dict with success, output, steps, usage, etc.
    """
    bus = event_bus or EventBus.get()

    # Build skill registry
    skill_registry = create_skill_registry(
        allowed_commands=[
            "ls",
            "cat",
            "head",
            "tail",
            "wc",
            "grep",
            "find",
            "python",
            "python3",
            "pytest",
            "ruff",
            "git",
        ]
    )

    # Default tools if none specified
    if tools is None:
        tools = ["file_read", "file_write", "glob_search", "shell_exec"]

    # Default role
    if not role:
        role = f"You are {agent_name}. Be concise and practical."

    config = AgentConfig(
        name=agent_name,
        role=role,
        provider_key="dashboard",
        tools=tools,
        max_steps=max_steps,
    )

    # Emit agent spawn
    await bus.emit(
        Event(
            event_type=EventType.AGENT_SPAWN,
            agent_name=agent_name,
            data={
                "provider": provider.model_id,
                "role": role[:100],
                "tools": tools,
            },
        )
    )

    # Run the agent with instrumented execution
    start_time = time.time()
    result = await _instrumented_execute(
        config=config,
        provider=provider,
        skill_registry=skill_registry,
        task=Task(description=task_description),
        event_bus=bus,
    )
    elapsed = time.time() - start_time

    # Emit agent complete/error
    if result.status == TaskStatus.COMPLETED:
        await bus.emit(
            Event(
                event_type=EventType.AGENT_COMPLETE,
                agent_name=agent_name,
                data={
                    "output": result.output[:200],
                    "steps": result.steps_taken,
                    "tokens": result.total_tokens,
                    "cost_usd": result.total_cost_usd,
                },
            )
        )
    elif result.status == TaskStatus.STALLED:
        await bus.emit(
            Event(
                event_type=EventType.AGENT_STALLED,
                agent_name=agent_name,
                data={"error": result.error or "Stalled"},
            )
        )
    else:
        await bus.emit(
            Event(
                event_type=EventType.AGENT_ERROR,
                agent_name=agent_name,
                data={"error": result.error or "Failed"},
            )
        )

    # Emit token/cost update
    await bus.emit(
        Event(
            event_type=EventType.TOKEN_UPDATE,
            agent_name=agent_name,
            data={
                "total_tokens": result.total_tokens,
                "agent_tokens": result.total_tokens,
                "agent_cost_usd": result.total_cost_usd,
            },
        )
    )

    return {
        "success": result.status == TaskStatus.COMPLETED,
        "status": result.status.value,
        "output": result.output,
        "steps_taken": result.steps_taken,
        "total_tokens": result.total_tokens,
        "total_cost_usd": result.total_cost_usd,
        "elapsed_s": round(elapsed, 2),
        "error": result.error,
    }


async def _instrumented_execute(
    config: AgentConfig,
    provider: Provider,
    skill_registry: SkillRegistry,
    task: Task,
    event_bus: EventBus,
) -> TaskResult:
    """Execute agent loop with real-time event emissions for each step."""
    messages: list[Message] = [
        Message(role=Role.USER, content=task.description),
    ]

    tool_defs = [
        ToolDefinition(
            name=skill.name,
            description=skill.description,
            parameters=skill.parameters,
        )
        for name in config.tools
        if (skill := skill_registry.get(name)) is not None
    ]

    steps = 0
    retry_counts: dict[str, int] = {}
    total_cost = 0.0
    total_tokens = 0
    start_time = time.monotonic()

    while steps < config.max_steps:
        # Timeout check
        elapsed = time.monotonic() - start_time
        if elapsed > config.timeout_seconds:
            return TaskResult(
                status=TaskStatus.STALLED,
                output="Agent timed out",
                steps_taken=steps,
                total_tokens=total_tokens,
                total_cost_usd=total_cost,
                error=f"Timeout after {elapsed:.0f}s",
            )

        # Emit step event
        await event_bus.emit(
            Event(
                event_type=EventType.AGENT_STEP,
                agent_name=config.name,
                data={"step": steps + 1, "model": provider.model_id},
            )
        )

        completion = await provider.complete(
            messages=messages,
            tools=tool_defs if tool_defs else None,
            system=config.role,
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
        messages.append(
            Message(
                role=Role.ASSISTANT,
                content=completion.content,
                tool_calls=completion.tool_calls,
            )
        )

        for tool_call in completion.tool_calls:
            # Anti-stall
            approach_key = f"{tool_call.name}:{hash(str(tool_call.arguments))}"
            retry_counts[approach_key] = retry_counts.get(approach_key, 0) + 1

            if retry_counts[approach_key] > config.max_retries_per_approach:
                return TaskResult(
                    status=TaskStatus.STALLED,
                    output=f"Stalled: too many retries on {tool_call.name}",
                    steps_taken=steps,
                    total_tokens=total_tokens,
                    total_cost_usd=total_cost,
                    error=f"Max retries exceeded for: {approach_key}",
                )

            # Emit tool call event
            await event_bus.emit(
                Event(
                    event_type=EventType.AGENT_TOOL_CALL,
                    agent_name=config.name,
                    data={
                        "tool_name": tool_call.name,
                        "tool_call_id": tool_call.id,
                        "arguments": _safe_truncate(tool_call.arguments),
                    },
                )
            )

            result = await skill_registry.execute(tool_call.name, tool_call.arguments)

            # Emit tool result event
            await event_bus.emit(
                Event(
                    event_type=EventType.AGENT_TOOL_RESULT,
                    agent_name=config.name,
                    data={
                        "tool_name": tool_call.name,
                        "tool_call_id": tool_call.id,
                        "success": result.success,
                        "output": str(result)[:500],
                    },
                )
            )

            messages.append(
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
        error=f"Max steps ({config.max_steps}) reached",
    )


def _safe_truncate(args: dict) -> dict:
    """Truncate large argument values for event display."""
    result = {}
    for k, v in args.items():
        sv = str(v)
        result[k] = sv[:200] + "..." if len(sv) > 200 else sv
    return result
