"""Agent runner for the dashboard.

Wraps Agent.execute() with real-time EventBus emissions so the dashboard
can show agent spawning, tool calls, tool results, and completion live.

v1.2: Dynamic team routing — team-lead selects agents from the registry
instead of hardcoded backend-dev + frontend-dev.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from ..core.agent import AgentConfig, Task, TaskResult, TaskStatus
from ..core.cache import InMemoryCache
from ..core.conversation import ConversationManager
from ..core.store import BaseStore
from ..core.provider import Message, Provider, Role, ToolDefinition
from ..core.sandbox import Sandbox
from ..core.skill import SkillRegistry, cache_middleware
from ..skills import FileReadSkill, FileWriteSkill, GlobSkill, ShellExecSkill
from ..skills.sandboxed_shell import SandboxedShellSkill
from .events import Event, EventBus, EventType
from .sandbox_manager import SandboxManager

logger = logging.getLogger(__name__)

# Module-level shared tool cache for all agent runs
_tool_cache = InMemoryCache(max_entries=200)


def get_tool_cache() -> InMemoryCache:
    """Return the shared tool cache instance (for stats/clearing)."""
    return _tool_cache


def create_skill_registry(
    allowed_commands: list[str] | None = None,
    working_directory: str | None = None,
    sandbox: Sandbox | None = None,
) -> SkillRegistry:
    """Create a skill registry with all built-in skills.

    Includes cache middleware for idempotent tools (file_read, glob_search).
    file_write invalidates file_read cache for the written path.

    If a Sandbox instance is provided, a SandboxedShellSkill is registered
    alongside the regular ShellExecSkill for isolated code execution.
    """
    registry = SkillRegistry()
    registry.register(FileReadSkill(working_directory=working_directory))
    registry.register(FileWriteSkill(working_directory=working_directory))
    registry.register(GlobSkill(working_directory=working_directory))
    registry.register(
        ShellExecSkill(
            allowed_commands=allowed_commands,
            working_directory=working_directory,
        )
    )

    # Register sandboxed shell if a sandbox is provided
    if sandbox is not None:
        registry.register(
            SandboxedShellSkill(
                sandbox=sandbox,
                allowed_commands=allowed_commands,
            )
        )

    # Add cache middleware: cache file_read and glob_search results,
    # invalidate file_read cache when file_write succeeds
    registry.use(
        cache_middleware(
            cache=_tool_cache,
            cacheable_skills={"file_read", "glob_search"},
            ttl_seconds=60,
            invalidate_on={"file_write": "file_path"},
        )
    )

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
    usage_db: Any | None = None,
    session_id: str = "",
    conversation_id: str | None = None,
    conversation_manager: ConversationManager | None = None,
    sandbox: Sandbox | None = None,
    store: BaseStore | None = None,
) -> dict[str, Any]:
    """Run an agent on a task with real-time event emissions.

    Args:
        conversation_id: Optional thread ID for multi-turn conversation memory.
        conversation_manager: Optional ConversationManager instance. If both
            conversation_id and conversation_manager are provided, the agent
            will see previous exchanges and the new exchange will be persisted.
        sandbox: Optional started Sandbox instance. When provided, the
            sandboxed_shell skill is registered in addition to shell_exec,
            giving the agent access to an isolated execution environment.
        store: Optional BaseStore for per-agent long-term memory. When set,
            recent memories are injected into the system prompt and a summary
            is persisted after task completion.

    Returns a dict with success, output, steps, usage, etc.
    """
    bus = event_bus or EventBus.get()

    # Build skill registry (include sandboxed shell if a sandbox was supplied)
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
        sandbox=sandbox,
    )

    # Default tools if none specified
    if tools is None:
        tools = ["file_read", "file_write", "glob_search", "shell_exec"]

    # Default role
    if not role:
        role = f"You are {agent_name}. Be concise and practical."

    # Inject recent memories from the store into the system prompt.
    # Queries agent-specific and shared namespaces, caps at 2000 chars.
    if store is not None:
        memory_lines: list[str] = []
        try:
            agent_items = await store.asearch(("agent", agent_name), limit=10)
            shared_items = await store.asearch(("shared",), limit=5)
            for item in agent_items:
                snippet = str(item.value)[:300]
                memory_lines.append(f"[agent/{agent_name}] {item.key}: {snippet}")
            for item in shared_items:
                snippet = str(item.value)[:300]
                memory_lines.append(f"[shared] {item.key}: {snippet}")
        except Exception:
            pass  # Store unavailable — proceed without memory injection
        if memory_lines:
            memory_block = "<memory>\n" + "\n".join(memory_lines) + "\n</memory>"
            # Cap total memory injection at 2000 characters
            if len(memory_block) > 2000:
                memory_block = memory_block[:1997] + "..."
            role = memory_block + "\n\n" + role

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

    # Load conversation history for multi-turn context
    conversation_history: list[Message] = []
    if conversation_id and conversation_manager:
        history = await conversation_manager.get_history(conversation_id)
        for msg in history:
            conversation_history.append(Message(role=Role(msg.role), content=msg.content))

    # Run the agent with instrumented execution
    start_time = time.time()
    result = await _instrumented_execute(
        config=config,
        provider=provider,
        skill_registry=skill_registry,
        task=Task(description=task_description),
        event_bus=bus,
        usage_db=usage_db,
        session_id=session_id,
        conversation_history=conversation_history or None,
    )
    elapsed = time.time() - start_time

    # Save to conversation memory
    if conversation_id and conversation_manager:

        async def _passthrough(msgs):
            return result.output

        await conversation_manager.send(
            conversation_id,
            task_description,
            _passthrough,
        )

    # Persist a summary of what the agent did to long-term store (30-day TTL).
    if store is not None and result.status == TaskStatus.COMPLETED:
        try:
            task_summary = task_description[:500]
            result_summary = (result.output or "")[:500]
            await store.aput(
                ("agent", agent_name),
                f"task_{session_id}",
                {
                    "task": task_summary,
                    "result_summary": result_summary,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "steps": result.steps_taken,
                },
                ttl=86400 * 30,  # 30-day TTL
            )
        except Exception:
            pass  # Store write failure must not disrupt the agent result

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
    usage_db: Any | None = None,
    session_id: str = "",
    conversation_history: list[Message] | None = None,
) -> TaskResult:
    """Execute agent loop with real-time event emissions for each step."""
    messages: list[Message] = []

    # Prepend conversation history for multi-turn context
    if conversation_history:
        messages.extend(conversation_history)

    messages.append(Message(role=Role.USER, content=task.description))

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
                artifacts={
                    "files_created": files_created,
                    "step_log": step_log,
                    "fallback_log": fallback_log,
                },
            )

        # Emit step event
        await event_bus.emit(
            Event(
                event_type=EventType.AGENT_STEP,
                agent_name=config.name,
                data={"step": steps + 1, "model": provider.model_id},
            )
        )

        # Dynamic max_tokens from provider capabilities
        cap = provider.capabilities
        current_max_tokens = cap.max_output_tokens

        completion = await provider.complete(
            messages=messages,
            tools=tool_defs if tool_defs else None,
            system=config.role,
            max_tokens=current_max_tokens,
        )

        # Auto-retry with higher max_tokens if response was truncated
        if completion.stop_reason == "length" and not completion.tool_calls:
            for _retry in range(2):
                new_max = min(current_max_tokens * 2, cap.max_context // 2)
                if new_max <= current_max_tokens:
                    break
                current_max_tokens = new_max
                step_log.append(f"truncated, retrying with max_tokens={current_max_tokens}")
                try:
                    completion = await provider.complete(
                        messages=messages,
                        tools=tool_defs if tool_defs else None,
                        system=config.role,
                        max_tokens=current_max_tokens,
                    )
                except Exception:
                    break  # credits/rate limit — use what we have
                if completion.stop_reason != "length":
                    break

        # Collect fallback info from OpenRouter provider
        if hasattr(provider, "last_fallback_log") and provider.last_fallback_log:
            fallback_log.extend(provider.last_fallback_log)

        total_tokens += completion.usage.input_tokens + completion.usage.output_tokens
        total_cost += completion.usage.cost_usd
        steps += 1

        # Emit incremental token/cost update after each step
        await event_bus.emit(
            Event(
                event_type=EventType.TOKEN_UPDATE,
                agent_name=config.name,
                data={
                    "total_tokens": total_tokens,
                    "agent_tokens": total_tokens,
                    "agent_cost_usd": total_cost,
                },
            )
        )
        await event_bus.emit(
            Event(
                event_type=EventType.COST_UPDATE,
                data={"total_cost_usd": total_cost},
            )
        )

        # No tool calls — agent is done
        if not completion.tool_calls:
            return TaskResult(
                status=TaskStatus.COMPLETED,
                output=completion.content,
                steps_taken=steps,
                total_tokens=total_tokens,
                total_cost_usd=total_cost,
                artifacts={
                    "files_created": files_created,
                    "step_log": step_log,
                    "fallback_log": fallback_log,
                },
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
                    artifacts={
                        "files_created": files_created,
                        "step_log": step_log,
                        "fallback_log": fallback_log,
                    },
                )

            # Extract _description for event/audit display (before skill strips it)
            tool_description = tool_call.arguments.get("_description")

            # Emit tool call event
            tool_call_data: dict[str, Any] = {
                "tool_name": tool_call.name,
                "tool_call_id": tool_call.id,
                "arguments": _safe_truncate(tool_call.arguments),
            }
            if tool_description:
                tool_call_data["tool_description"] = tool_description
            await event_bus.emit(
                Event(
                    event_type=EventType.AGENT_TOOL_CALL,
                    agent_name=config.name,
                    data=tool_call_data,
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
            tool_result_data: dict[str, Any] = {
                "tool_name": tool_call.name,
                "tool_call_id": tool_call.id,
                "success": result.success,
                "output": str(result)[:500],
            }
            if tool_description:
                tool_result_data["tool_description"] = tool_description
            await event_bus.emit(
                Event(
                    event_type=EventType.AGENT_TOOL_RESULT,
                    agent_name=config.name,
                    data=tool_result_data,
                )
            )

            # Persist tool errors to DB for tracking
            if not result.success and usage_db is not None:
                error_msg = getattr(result, "error", "") or str(result)[:500]
                error_type = "tool_error"
                if "command not found" in error_msg.lower():
                    error_type = "command_not_found"
                elif "exit code" in error_msg.lower():
                    error_type = "exit_code_error"
                elif "timed out" in error_msg.lower():
                    error_type = "timeout"
                elif "not allowed" in error_msg.lower():
                    error_type = "not_allowed"
                await usage_db.record_error(
                    session_id=session_id,
                    agent=config.name,
                    tool_name=tool_call.name,
                    error_type=error_type,
                    error_message=error_msg,
                    step_number=steps,
                    model=getattr(provider, "model_id", ""),
                    provider=type(provider).__name__,
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
        artifacts={
            "files_created": files_created,
            "step_log": step_log,
            "fallback_log": fallback_log,
        },
    )


def _build_agent_catalog(registry: dict) -> str:
    """Build a text catalog of available agents for the team-lead prompt."""
    lines = ["Available agents:"]
    for agent in registry.get("agents", []):
        name = agent["name"]
        if name in ("team-lead", "scout", "research-scout", "skillkit-scout"):
            continue
        desc = agent.get("description", "")
        cat = agent.get("category", "")
        skills = agent.get("skills", [])
        skills_str = f" Skills: {', '.join(skills)}" if skills else ""
        lines.append(f"- {name} ({cat}): {desc}.{skills_str}")
    return "\n".join(lines)


_AGENT_ALIASES: dict[str, str] = {
    "backend-dev": "backend",
    "frontend-dev": "frontend",
    "backend-developer": "backend",
    "frontend-developer": "frontend",
    "devops-engineer": "devops",
    "ml-eng": "ml-engineer",
    "data-eng": "data-engineer",
    # finance
    "finance-analyst": "financial-analyst",
    "analyst": "financial-analyst",
    "risk": "risk-analyst",
    "quant": "quant-developer",
    "compliance": "compliance-officer",
    # data-science
    "data-science": "data-analyst",
    "nlp": "nlp-specialist",
    "bi": "bi-analyst",
    # marketing
    "content": "content-strategist",
    "seo": "seo-specialist",
    "growth": "growth-hacker",
    "social": "social-media-manager",
    "email": "email-marketer",
}

# Category keyword detection for smart fallback when team-lead parsing fails
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "finance": [
        "finance",
        "financial",
        "stock",
        "portfolio",
        "trading",
        "investment",
        "risk",
        "valuation",
        "dcf",
        "revenue",
        "forecast",
        "budget",
        "cash flow",
        "balance sheet",
        "p&l",
        "profit",
        "loss",
        "hedge",
        "option",
        "derivative",
        "bond",
        "equity",
        "market",
        "sharpe",
        "var",
        "compliance",
        "audit",
        "accounting",
        "tax",
        "gaap",
        "ifrs",
        "basel",
        "roi",
        "irr",
        "npv",
    ],
    "data-science": [
        "data",
        "dataset",
        "analysis",
        "machine learning",
        "ml",
        "model",
        "prediction",
        "classification",
        "regression",
        "clustering",
        "nlp",
        "embeddings",
        "eda",
        "visualization",
        "statistics",
        "etl",
        "pipeline",
        "dashboard",
        "kpi",
        "metrics",
        "bi",
        "report",
    ],
    "marketing": [
        "marketing",
        "seo",
        "content",
        "social media",
        "email",
        "campaign",
        "funnel",
        "conversion",
        "growth",
        "brand",
        "audience",
        "keyword",
        "engagement",
        "newsletter",
        "ad",
        "advertising",
        "copy",
        "cro",
    ],
}

# Default fallback agents per category
_CATEGORY_FALLBACK_AGENTS: dict[str, list[dict[str, str]]] = {
    "finance": [
        {"agent": "financial-analyst", "task": "Analyze the financial aspects of: {task}"},
        {"agent": "risk-analyst", "task": "Assess risks and compliance for: {task}"},
    ],
    "data-science": [
        {"agent": "data-analyst", "task": "Perform data analysis for: {task}"},
        {"agent": "ml-engineer", "task": "Handle ML/modeling aspects of: {task}"},
    ],
    "marketing": [
        {"agent": "content-strategist", "task": "Develop content strategy for: {task}"},
        {"agent": "seo-specialist", "task": "Handle SEO and discoverability for: {task}"},
    ],
    "software-engineering": [
        {"agent": "backend", "task": "Implement the backend parts of: {task}"},
        {"agent": "frontend", "task": "Implement the frontend parts of: {task}"},
    ],
}


def _detect_category(task: str) -> str:
    """Detect the most likely agent category from task text."""
    task_lower = task.lower()
    scores: dict[str, int] = {}
    for category, keywords in _CATEGORY_KEYWORDS.items():
        scores[category] = sum(1 for kw in keywords if kw in task_lower)
    best = max(scores, key=scores.get) if scores else "software-engineering"
    return best if scores.get(best, 0) > 0 else "software-engineering"


def _parse_team_plan(plan_text: str, valid_names: set[str]) -> list[dict[str, str]] | None:
    """Parse structured JSON assignments from team-lead plan output.

    Returns a list of {"agent": name, "task": description} dicts, or None on failure.
    """
    # Strip markdown code fences
    cleaned = re.sub(r"```(?:json)?\s*", "", plan_text).strip().rstrip("`")

    # Find the JSON array
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None

    if not isinstance(data, list) or len(data) == 0:
        return None

    assignments: list[dict[str, str]] = []
    for item in data[:5]:  # Cap at 5 sub-tasks
        if not isinstance(item, dict):
            continue
        agent_name = str(item.get("agent", "")).strip().lower()
        task = str(item.get("task", "")).strip()
        if not agent_name or not task:
            continue

        # Resolve aliases
        agent_name = _AGENT_ALIASES.get(agent_name, agent_name)

        if agent_name not in valid_names:
            continue  # Skip unknown agents

        assignments.append({"agent": agent_name, "task": task})

    return assignments if assignments else None


def _build_role_for_agent(agent_info: dict) -> str:
    """Build a role prompt for a sub-agent from its registry info."""
    name = agent_info.get("name", "agent")
    desc = agent_info.get("description", "")
    category = agent_info.get("category", "software-engineering")

    # Category-specific instructions
    if category == "finance":
        return (
            f"You are {name}: {desc}. "
            "Provide detailed financial analysis, calculations, and recommendations. "
            "Be precise with numbers and cite assumptions."
        )
    elif category == "data-science":
        return (
            f"You are {name}: {desc}. "
            "Provide data-driven analysis, statistical insights, and actionable recommendations. "
            "Be precise and include methodology."
        )
    elif category == "marketing":
        return (
            f"You are {name}: {desc}. "
            "Provide strategic marketing recommendations with measurable goals. "
            "Be creative and data-informed."
        )
    else:
        return (
            f"You are {name}: {desc}. "
            "Write actual code files. Use file_write to create files. "
            "Combine related content into fewer files when possible. "
            "Be practical, write working code."
        )


async def run_team(
    task_description: str,
    provider: Provider,
    event_bus: EventBus | None = None,
    working_directory: str | None = None,
    max_steps: int = 15,
    max_sub_agent_steps: int = 30,
    max_sub_agents: int = 5,
    usage_db: Any | None = None,
    session_id: str = "",
    conversation_id: str | None = None,
    conversation_manager: ConversationManager | None = None,
    sandbox_manager: SandboxManager | None = None,
    store: BaseStore | None = None,
) -> dict[str, Any]:
    """Run a multi-agent team with dynamic routing.

    Args:
        max_steps: Max LLM calls for the team-lead orchestration steps.
        max_sub_agent_steps: Max LLM calls per individual sub-agent (default 30).
            Sub-agents typically need more steps than the team-lead because each
            tool call (file_write, shell_exec, etc.) consumes one step.
        conversation_id: Optional thread ID for multi-turn memory.
        conversation_manager: Optional ConversationManager for persistence.
        sandbox_manager: Optional SandboxManager. When provided, each
            sub-agent receives a session-scoped sandbox fetched via
            ``sandbox_manager.get_or_create(session_id)``.

    Flow:
      1. team-lead analyzes the task and selects agents from the registry
      2. Selected agents execute sub-tasks in parallel (max 3 concurrent)
      3. team-lead validates outputs, optionally re-delegates
      4. team-lead writes a final summary
    """
    from .agents_registry import get_agent_registry

    bus = event_bus or EventBus.get()
    registry = get_agent_registry()
    agent_catalog = _build_agent_catalog(registry)
    agent_map = {a["name"]: a for a in registry.get("agents", [])}
    valid_names = set(agent_map.keys()) - {"team-lead", "scout", "research-scout", "skillkit-scout"}

    start_time = time.time()
    total_tokens = 0
    total_cost = 0.0
    agent_outputs: dict[str, str] = {}
    agent_costs: dict[str, dict[str, Any]] = {}
    all_fallback_logs: list[dict] = []
    agent_files: dict[str, list[str]] = {}
    agent_steps_log: dict[str, list[str]] = {}

    # Emit graph start so dashboard shows the team workflow
    await bus.emit(
        Event(
            event_type=EventType.GRAPH_START,
            data={
                "nodes": ["team-lead (plan)", "sub-agents", "team-lead (review)"],
                "edges": [
                    {"from": "team-lead (plan)", "to": "sub-agents"},
                    {"from": "sub-agents", "to": "team-lead (review)"},
                ],
            },
        )
    )

    # Load conversation history for team-lead context
    team_history_msgs: list[Message] = []
    if conversation_id and conversation_manager:
        history = await conversation_manager.get_history(conversation_id)
        for msg in history:
            team_history_msgs.append(Message(role=Role(msg.role), content=msg.content))

    # --- Step 1: Team-lead plans with agent registry ---
    await bus.emit(
        Event(
            event_type=EventType.GRAPH_NODE_ENTER,
            node_name="team-lead (plan)",
            data={"step_index": 0},
        )
    )
    await bus.emit(
        Event(
            event_type=EventType.AGENT_SPAWN,
            agent_name="team-lead",
            data={"provider": provider.model_id, "role": "Dynamic planning", "tools": []},
        )
    )
    await bus.emit(
        Event(
            event_type=EventType.AGENT_STEP,
            agent_name="team-lead",
            data={"step": 1, "model": provider.model_id},
        )
    )

    plan_messages = list(team_history_msgs)
    plan_messages.append(Message(role=Role.USER, content=task_description))

    plan_completion = await provider.complete(
        messages=plan_messages,
        system=(
            "You are a team lead. Analyze the task and select the best agents.\n\n"
            f"{agent_catalog}\n\n"
            "Respond with ONLY a JSON array of assignments (max 5). "
            'Each item must have "agent" (exact name from the list) and "task" (specific instructions).\n'
            "Examples:\n"
            'Software: [{"agent": "backend", "task": "Create REST API"}, '
            '{"agent": "devops", "task": "Write Dockerfile"}]\n'
            'Finance: [{"agent": "financial-analyst", "task": "Build DCF model"}, '
            '{"agent": "risk-analyst", "task": "Assess portfolio risk"}]\n'
            'Data: [{"agent": "data-analyst", "task": "Perform EDA"}, '
            '{"agent": "ml-engineer", "task": "Train classifier"}]\n\n'
            "IMPORTANT: Match agents to the task domain. Do NOT default to software agents "
            "for non-software tasks. Select only agents relevant to this task."
        ),
    )
    if hasattr(provider, "last_fallback_log") and provider.last_fallback_log:
        all_fallback_logs.extend(
            {"agent": "team-lead (plan)", **e} for e in provider.last_fallback_log
        )

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

    await _emit_metrics(bus, "team-lead", total_tokens, plan_tokens, plan_cost, total_cost)
    await bus.emit(
        Event(
            event_type=EventType.AGENT_COMPLETE,
            agent_name="team-lead",
            data={"output": plan[:200], "steps": 1},
        )
    )

    # --- Parse assignments (with category-aware fallback) ---
    assignments = _parse_team_plan(plan, valid_names)
    used_fallback = False
    if assignments is None:
        used_fallback = True
        detected_category = _detect_category(task_description)
        fallback_templates = _CATEGORY_FALLBACK_AGENTS.get(
            detected_category,
            _CATEGORY_FALLBACK_AGENTS["software-engineering"],
        )
        assignments = [
            {"agent": t["agent"], "task": t["task"].format(task=task_description)}
            for t in fallback_templates
        ]
        await bus.emit(
            Event(
                event_type=EventType.AGENT_STEP,
                agent_name="team-lead",
                data={
                    "step": "fallback",
                    "reason": "Could not parse structured plan",
                    "detected_category": detected_category,
                },
            )
        )

    # Cap sub-agents
    assignments = assignments[:max_sub_agents]

    # Mark plan phase as done
    await bus.emit(
        Event(
            event_type=EventType.GRAPH_NODE_EXIT,
            node_name="team-lead (plan)",
            data={"success": True, "step_index": 0},
        )
    )

    # --- Step 2: Execute sub-agents in parallel (max 3 concurrent) ---
    await bus.emit(
        Event(
            event_type=EventType.GRAPH_NODE_ENTER,
            node_name="sub-agents",
            data={"step_index": 1, "agents": [a["agent"] for a in assignments]},
        )
    )
    sem = asyncio.Semaphore(3)

    async def _run_sub_agent(assignment: dict, idx: int) -> tuple[str, dict[str, Any]]:
        agent_name = assignment["agent"]
        agent_task = assignment["task"]
        # Use index suffix for duplicate agent names
        event_key = f"{agent_name}-{idx}" if assignments.count(assignment) > 1 else agent_name

        agent_info = agent_map.get(agent_name, {})
        agent_category = agent_info.get("category", "software-engineering")
        role = (
            _build_role_for_agent(agent_info)
            if agent_info
            else (f"You are {agent_name}. Be practical and thorough.")
        )

        # Category-specific action instructions
        if agent_category in ("finance", "data-science", "marketing"):
            action_instruction = (
                "Execute your task. Provide detailed analysis, "
                "calculations, and actionable recommendations."
            )
        else:
            action_instruction = "Execute your task. Write all necessary files using file_write."

        prompt = (
            f"Team lead's plan:\n{plan}\n\n"
            f"Original request:\n{task_description}\n\n"
            f"Your specific task:\n{agent_task}\n\n"
            f"{action_instruction}"
        )

        await bus.emit(
            Event(
                event_type=EventType.TASK_ASSIGNED,
                data={
                    "task_id": event_key,
                    "from_agent": "team-lead",
                    "to_agent": agent_name,
                    "description": agent_task[:80],
                    "priority": "normal",
                },
            )
        )

        # Resolve sandbox for this sub-agent when a manager is provided.
        sub_sandbox: Sandbox | None = None
        if sandbox_manager is not None:
            try:
                sub_sandbox = await sandbox_manager.get_or_create(session_id)
            except Exception:
                logger.warning(
                    "Failed to obtain sandbox for session %s — running without sandbox",
                    session_id,
                    exc_info=True,
                )

        async with sem:
            result = await run_agent(
                agent_name=agent_name,
                task_description=prompt,
                provider=provider,
                role=role,
                max_steps=max_sub_agent_steps,
                event_bus=bus,
                working_directory=working_directory,
                usage_db=usage_db,
                session_id=session_id,
                sandbox=sub_sandbox,
                store=store,
            )

        await bus.emit(
            Event(
                event_type=EventType.TASK_COMPLETED,
                data={
                    "task_id": event_key,
                    "from_agent": agent_name,
                    "to_agent": "team-lead",
                    "success": result.get("success", False),
                    "summary": result.get("output", "")[:100],
                },
            )
        )

        return event_key, result

    # Run all sub-agents concurrently
    sub_results = await asyncio.gather(
        *[_run_sub_agent(a, i) for i, a in enumerate(assignments)],
        return_exceptions=True,
    )

    for item in sub_results:
        if isinstance(item, Exception):
            continue
        event_key, result = item
        agent_tok = result.get("total_tokens", 0)
        agent_cost_val = result.get("total_cost_usd", 0.0)
        total_tokens += agent_tok
        total_cost += agent_cost_val
        agent_outputs[event_key] = result.get("output", result.get("error", ""))
        agent_files[event_key] = result.get("files_created", [])
        agent_steps_log[event_key] = result.get("step_log", [])
        agent_costs[event_key] = {
            "tokens": agent_tok,
            "cost_usd": agent_cost_val,
            "steps": result.get("steps_taken", 0),
        }
        for fb in result.get("fallback_log", []):
            all_fallback_logs.append({"agent": event_key, **fb})

    # Mark sub-agents phase as done
    await bus.emit(
        Event(
            event_type=EventType.GRAPH_NODE_EXIT,
            node_name="sub-agents",
            data={"success": True, "step_index": 1},
        )
    )

    # --- Step 3: Team-lead validates outputs ---
    await bus.emit(
        Event(
            event_type=EventType.GRAPH_NODE_ENTER,
            node_name="team-lead (review)",
            data={"step_index": 2},
        )
    )
    evidence_parts = _build_evidence(agent_outputs, agent_files, agent_steps_log)

    await bus.emit(
        Event(
            event_type=EventType.AGENT_SPAWN,
            agent_name="team-lead",
            data={"provider": provider.model_id, "role": "Validation", "tools": []},
        )
    )

    validation_completion = await provider.complete(
        messages=[
            Message(
                role=Role.USER,
                content=(
                    f"Original request:\n{task_description}\n\n"
                    + "\n".join(evidence_parts)
                    + "\n\nReview the outputs. Are they sufficient?\n"
                    'Reply with JSON: {"sufficient": true} or '
                    '{"sufficient": false, "re_delegate": '
                    '[{"agent": "name", "task": "what to fix"}]}'
                ),
            )
        ],
        system="You are the team lead. Validate sub-agent outputs. Be strict but fair.",
    )
    if hasattr(provider, "last_fallback_log") and provider.last_fallback_log:
        all_fallback_logs.extend(
            {"agent": "team-lead (validation)", **e} for e in provider.last_fallback_log
        )

    val_tokens = (
        validation_completion.usage.input_tokens + validation_completion.usage.output_tokens
    )
    val_cost = validation_completion.usage.cost_usd
    total_tokens += val_tokens
    total_cost += val_cost
    agent_costs["team-lead (validation)"] = {
        "tokens": val_tokens,
        "cost_usd": val_cost,
        "steps": 1,
    }

    await _emit_metrics(bus, "team-lead", total_tokens, val_tokens, val_cost, total_cost)

    # One round of re-delegation if needed
    re_assignments = _parse_team_plan(validation_completion.content, valid_names)
    if re_assignments:
        re_results = await asyncio.gather(
            *[_run_sub_agent(a, i + len(assignments)) for i, a in enumerate(re_assignments[:3])],
            return_exceptions=True,
        )
        for item in re_results:
            if isinstance(item, Exception):
                continue
            event_key, result = item
            agent_tok = result.get("total_tokens", 0)
            agent_cost_val = result.get("total_cost_usd", 0.0)
            total_tokens += agent_tok
            total_cost += agent_cost_val
            agent_outputs[event_key] = result.get("output", result.get("error", ""))
            agent_files[event_key] = result.get("files_created", [])
            agent_steps_log[event_key] = result.get("step_log", [])
            agent_costs[event_key] = {
                "tokens": agent_tok,
                "cost_usd": agent_cost_val,
                "steps": result.get("steps_taken", 0),
            }

        # Rebuild evidence after re-delegation
        evidence_parts = _build_evidence(agent_outputs, agent_files, agent_steps_log)

    # --- Step 4: Team-lead summarizes ---
    await bus.emit(
        Event(
            event_type=EventType.AGENT_SPAWN,
            agent_name="team-lead",
            data={"provider": provider.model_id, "role": "Summary", "tools": []},
        )
    )

    summary_completion = await provider.complete(
        messages=[
            Message(
                role=Role.USER,
                content=(
                    f"Original request:\n{task_description}\n\n"
                    + "\n".join(evidence_parts)
                    + "\nBased on the files actually created and actions taken above, "
                    "write a final summary of what was built and how to run it. Be concise."
                ),
            )
        ],
        system=(
            "You are the team lead. Summarize the work done by your team. "
            "Focus on CONCRETE results: files created, project structure, how to build/run. "
            "If files were created, the work IS done — describe what was built."
        ),
    )
    if hasattr(provider, "last_fallback_log") and provider.last_fallback_log:
        all_fallback_logs.extend(
            {"agent": "team-lead (summary)", **e} for e in provider.last_fallback_log
        )

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

    await _emit_metrics(bus, "team-lead", total_tokens, summary_tokens, summary_cost, total_cost)
    await bus.emit(
        Event(
            event_type=EventType.AGENT_COMPLETE,
            agent_name="team-lead",
            data={"output": summary[:200], "steps": 1},
        )
    )

    # Mark review/summary phase as done
    await bus.emit(
        Event(
            event_type=EventType.GRAPH_NODE_EXIT,
            node_name="team-lead (review)",
            data={"success": True, "step_index": 2},
        )
    )

    elapsed = time.time() - start_time
    all_files = [f for files in agent_files.values() for f in files]

    # Save to conversation memory
    if conversation_id and conversation_manager:

        async def _passthrough_team(msgs):
            return summary

        await conversation_manager.send(
            conversation_id,
            task_description,
            _passthrough_team,
        )

    # Emit graph end so dashboard completes the visualization
    await bus.emit(
        Event(
            event_type=EventType.GRAPH_END,
            data={"success": True, "elapsed_s": round(elapsed, 2)},
        )
    )

    return {
        "success": True,
        "output": summary,
        "plan": plan,
        "agents_selected": [a["agent"] for a in assignments],
        "used_fallback": used_fallback,
        "agent_outputs": agent_outputs,
        "agent_costs": agent_costs,
        "fallback_log": all_fallback_logs,
        "files_created": all_files,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "elapsed_s": round(elapsed, 2),
    }


def _build_evidence(
    agent_outputs: dict[str, str],
    agent_files: dict[str, list[str]],
    agent_steps_log: dict[str, list[str]],
) -> list[str]:
    """Build evidence text from all agent outputs for team-lead review."""
    parts = []
    for aname in agent_outputs:
        part = f"**{aname}**:\n"
        files = agent_files.get(aname, [])
        steps = agent_steps_log.get(aname, [])
        output = agent_outputs.get(aname, "N/A")
        if files:
            part += f"Files created: {', '.join(files)}\n"
        if steps:
            part += f"Actions: {'; '.join(steps[:20])}\n"
        part += f"Agent message: {output[:500]}\n"
        parts.append(part)
    return parts


async def _emit_metrics(
    bus: EventBus,
    agent_name: str,
    total_tokens: int,
    step_tokens: int,
    step_cost: float,
    total_cost: float,
) -> None:
    """Emit token and cost update events."""
    await bus.emit(
        Event(
            event_type=EventType.TOKEN_UPDATE,
            agent_name=agent_name,
            data={
                "total_tokens": total_tokens,
                "agent_tokens": step_tokens,
                "agent_cost_usd": step_cost,
            },
        )
    )
    await bus.emit(
        Event(
            event_type=EventType.COST_UPDATE,
            data={"total_cost_usd": total_cost},
        )
    )


def _safe_truncate(args: dict) -> dict:
    """Truncate large argument values for event display."""
    result = {}
    for k, v in args.items():
        sv = str(v)
        result[k] = sv[:200] + "..." if len(sv) > 200 else sv
    return result
