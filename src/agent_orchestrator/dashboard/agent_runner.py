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
    working_directory: str | None = None,
) -> SkillRegistry:
    """Create a skill registry with all built-in skills."""
    registry = SkillRegistry()
    registry.register(FileReadSkill(working_directory=working_directory))
    registry.register(FileWriteSkill(working_directory=working_directory))
    registry.register(GlobSkill(working_directory=working_directory))
    registry.register(ShellExecSkill(
        allowed_commands=allowed_commands,
        working_directory=working_directory,
    ))
    return registry


async def run_agent(
    agent_name: str,
    task_description: str,
    provider: Provider,
    role: str = "",
    tools: list[str] | None = None,
    max_steps: int = 10,
    event_bus: EventBus | None = None,
    working_directory: str | None = None,
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
        ],
        working_directory=working_directory,
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

    artifacts = result.artifacts or {}
    return {
        "success": result.status == TaskStatus.COMPLETED,
        "status": result.status.value,
        "output": result.output,
        "steps_taken": result.steps_taken,
        "total_tokens": result.total_tokens,
        "total_cost_usd": result.total_cost_usd,
        "elapsed_s": round(elapsed, 2),
        "error": result.error,
        "files_created": artifacts.get("files_created", []),
        "step_log": artifacts.get("step_log", []),
        "fallback_log": artifacts.get("fallback_log", []),
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
    files_created: list[str] = []
    step_log: list[str] = []
    fallback_log: list[dict] = []

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
                artifacts={"files_created": files_created, "step_log": step_log, "fallback_log": fallback_log},
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

        # Collect fallback info from OpenRouter provider
        if hasattr(provider, "last_fallback_log") and provider.last_fallback_log:
            fallback_log.extend(provider.last_fallback_log)

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
                artifacts={"files_created": files_created, "step_log": step_log, "fallback_log": fallback_log},
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
                    artifacts={"files_created": files_created, "step_log": step_log, "fallback_log": fallback_log},
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

            # Track files and steps
            if tool_call.name == "file_write" and result.success:
                fpath = tool_call.arguments.get("file_path", "?")
                files_created.append(fpath)
                step_log.append(f"wrote {fpath}")
            elif tool_call.name == "shell_exec":
                cmd = tool_call.arguments.get("command", "")[:60]
                step_log.append(f"ran: {cmd}")
            elif result.success:
                step_log.append(f"{tool_call.name}: ok")

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
        artifacts={"files_created": files_created, "step_log": step_log, "fallback_log": fallback_log},
    )


async def run_team(
    task_description: str,
    provider: Provider,
    event_bus: EventBus | None = None,
    working_directory: str | None = None,
    max_steps: int = 15,
) -> dict[str, Any]:
    """Run a multi-agent team: team-lead plans, sub-agents execute with tools, team-lead summarizes.

    Flow:
      1. team-lead decomposes the task into sub-tasks
      2. backend-dev and frontend-dev execute sub-tasks (with file/shell tools)
      3. team-lead writes a final summary
    """
    bus = event_bus or EventBus.get()
    start_time = time.time()
    total_tokens = 0
    total_cost = 0.0
    agent_outputs: dict[str, str] = {}
    agent_costs: dict[str, dict[str, Any]] = {}
    all_fallback_logs: list[dict] = []

    # --- Step 1: Team-lead plans ---
    await bus.emit(Event(
        event_type=EventType.AGENT_SPAWN,
        agent_name="team-lead",
        data={"provider": provider.model_id, "role": "Task decomposition", "tools": []},
    ))
    await bus.emit(Event(
        event_type=EventType.AGENT_STEP,
        agent_name="team-lead",
        data={"step": 1, "model": provider.model_id},
    ))

    plan_completion = await provider.complete(
        messages=[Message(role=Role.USER, content=task_description)],
        system=(
            "You are a team lead. Decompose this task into concrete sub-tasks.\n"
            "Reply with a brief plan (max 5 lines):\n"
            "- BACKEND: what the backend developer should build (files, logic)\n"
            "- FRONTEND: what the frontend developer should build (files, UI)\n"
            "Be specific about file names and structure. Be concise."
        ),
    )
    if hasattr(provider, "last_fallback_log") and provider.last_fallback_log:
        all_fallback_logs.extend({"agent": "team-lead (plan)", **e} for e in provider.last_fallback_log)

    plan_tokens = plan_completion.usage.input_tokens + plan_completion.usage.output_tokens
    plan_cost = plan_completion.usage.cost_usd
    total_tokens += plan_tokens
    total_cost += plan_cost
    plan = plan_completion.content
    agent_costs["team-lead (plan)"] = {
        "tokens": plan_tokens,
        "cost_usd": plan_cost,
        "steps": 1,
    }

    await bus.emit(Event(
        event_type=EventType.AGENT_COMPLETE,
        agent_name="team-lead",
        data={"output": plan[:200], "steps": 1},
    ))

    # --- Step 2: Sub-agents execute with tools ---
    sub_agents = [
        {
            "name": "backend-dev",
            "role": (
                "You are a backend developer. Write actual code files. "
                "Use file_write to create files. Be practical, write working code. "
                "Create proper project structure."
            ),
            "prompt": (
                f"Team lead's plan:\n{plan}\n\n"
                f"Original request:\n{task_description}\n\n"
                "Implement the BACKEND parts. Write all necessary files using file_write."
            ),
        },
        {
            "name": "frontend-dev",
            "role": (
                "You are a frontend developer. Write actual code files. "
                "Use file_write to create files. Be practical, write working code. "
                "Create proper UI structure."
            ),
            "prompt": (
                f"Team lead's plan:\n{plan}\n\n"
                f"Original request:\n{task_description}\n\n"
                "Implement the FRONTEND parts. Write all necessary files using file_write."
            ),
        },
    ]

    agent_files: dict[str, list[str]] = {}
    agent_steps: dict[str, list[str]] = {}

    for agent_def in sub_agents:
        # Emit task delegation
        await bus.emit(Event(
            event_type=EventType.TASK_ASSIGNED,
            data={
                "task_id": agent_def["name"],
                "from_agent": "team-lead",
                "to_agent": agent_def["name"],
                "description": f"Execute: {agent_def['prompt'][:80]}",
                "priority": "normal",
            },
        ))

        result = await run_agent(
            agent_name=agent_def["name"],
            task_description=agent_def["prompt"],
            provider=provider,
            role=agent_def["role"],
            max_steps=max_steps,
            event_bus=bus,
            working_directory=working_directory,
        )

        agent_tok = result.get("total_tokens", 0)
        agent_cost = result.get("total_cost_usd", 0.0)
        total_tokens += agent_tok
        total_cost += agent_cost
        agent_outputs[agent_def["name"]] = result.get("output", result.get("error", ""))
        agent_files[agent_def["name"]] = result.get("files_created", [])
        agent_steps[agent_def["name"]] = result.get("step_log", [])
        agent_costs[agent_def["name"]] = {
            "tokens": agent_tok,
            "cost_usd": agent_cost,
            "steps": result.get("steps_taken", 0),
        }
        for fb in result.get("fallback_log", []):
            all_fallback_logs.append({"agent": agent_def["name"], **fb})

        await bus.emit(Event(
            event_type=EventType.TASK_COMPLETED,
            data={
                "task_id": agent_def["name"],
                "from_agent": agent_def["name"],
                "to_agent": "team-lead",
                "success": result.get("success", False),
                "summary": result.get("output", "")[:100],
            },
        ))

    # --- Step 3: Team-lead summarizes ---
    await bus.emit(Event(
        event_type=EventType.AGENT_SPAWN,
        agent_name="team-lead",
        data={"provider": provider.model_id, "role": "Summary", "tools": []},
    ))

    # Build evidence of what each agent actually did
    evidence_parts = []
    for aname in ["backend-dev", "frontend-dev"]:
        part = f"**{aname}**:\n"
        files = agent_files.get(aname, [])
        steps = agent_steps.get(aname, [])
        output = agent_outputs.get(aname, "N/A")
        if files:
            part += f"Files created: {', '.join(files)}\n"
        if steps:
            part += f"Actions: {'; '.join(steps[:20])}\n"
        part += f"Agent message: {output[:500]}\n"
        evidence_parts.append(part)

    summary_completion = await provider.complete(
        messages=[Message(role=Role.USER, content=(
            f"Original request:\n{task_description}\n\n"
            + "\n".join(evidence_parts) + "\n"
            "Based on the files actually created and actions taken above, "
            "write a final summary of what was built and how to run it. Be concise."
        ))],
        system=(
            "You are the team lead. Summarize the work done by your team. "
            "Focus on CONCRETE results: files created, project structure, how to build/run. "
            "If files were created, the work IS done — describe what was built."
        ),
    )
    if hasattr(provider, "last_fallback_log") and provider.last_fallback_log:
        all_fallback_logs.extend({"agent": "team-lead (summary)", **e} for e in provider.last_fallback_log)

    summary_tokens = summary_completion.usage.input_tokens + summary_completion.usage.output_tokens
    summary_cost = summary_completion.usage.cost_usd
    total_tokens += summary_tokens
    total_cost += summary_cost
    summary = summary_completion.content
    agent_costs["team-lead (summary)"] = {
        "tokens": summary_tokens,
        "cost_usd": summary_cost,
        "steps": 1,
    }

    await bus.emit(Event(
        event_type=EventType.AGENT_COMPLETE,
        agent_name="team-lead",
        data={"output": summary[:200], "steps": 1},
    ))

    elapsed = time.time() - start_time

    # Merge all created files
    all_files = []
    for files in agent_files.values():
        all_files.extend(files)

    return {
        "success": True,
        "output": summary,
        "plan": plan,
        "agent_outputs": agent_outputs,
        "agent_costs": agent_costs,
        "fallback_log": all_fallback_logs,
        "files_created": all_files,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "elapsed_s": round(elapsed, 2),
    }


def _safe_truncate(args: dict) -> dict:
    """Truncate large argument values for event display."""
    result = {}
    for k, v in args.items():
        sv = str(v)
        result[k] = sv[:200] + "..." if len(sv) > 200 else sv
    return result
