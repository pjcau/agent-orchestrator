"""Graph execution for the dashboard prompt.

Builds and runs StateGraph pipelines using Ollama/local models.
Emits events to the EventBus so the dashboard shows real-time progress.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable

import httpx

from ..core.graph import END, START, StateGraph
from ..core.llm_nodes import llm_node
from ..core.provider import Provider
from ..providers.local import LocalProvider
from ..providers.openrouter import OpenRouterProvider
from .events import Event, EventBus, EventType


async def list_openrouter_models(api_key: str) -> list[dict[str, str]]:
    """Return available OpenRouter models (curated list with pricing)."""
    if not api_key:
        return []

    # Curated free models from big brands (sorted by brand, best first)
    return [
        # Google
        {
            "name": "google/gemma-3-27b-it:free",
            "size": "Free · 27B · 131K",
            "provider": "openrouter",
        },
        {
            "name": "google/gemma-3-12b-it:free",
            "size": "Free · 12B · 32K",
            "provider": "openrouter",
        },
        # Meta
        {
            "name": "meta-llama/llama-3.3-70b-instruct:free",
            "size": "Free · 70B · 128K",
            "provider": "openrouter",
        },
        {
            "name": "meta-llama/llama-3.2-3b-instruct:free",
            "size": "Free · 3B · 131K",
            "provider": "openrouter",
        },
        # Qwen (Alibaba)
        {
            "name": "qwen/qwen3-coder:free",
            "size": "Free · 480B MoE · 262K",
            "provider": "openrouter",
        },
        {
            "name": "qwen/qwen3-235b-a22b-thinking-2507",
            "size": "Free · 235B MoE · 131K",
            "provider": "openrouter",
        },
        {
            "name": "qwen/qwen3-next-80b-a3b-instruct:free",
            "size": "Free · 80B MoE · 262K",
            "provider": "openrouter",
        },
        {"name": "qwen/qwen3-4b:free", "size": "Free · 4B · 41K", "provider": "openrouter"},
        # OpenAI
        {
            "name": "openai/gpt-oss-120b:free",
            "size": "Free · 120B · 131K",
            "provider": "openrouter",
        },
        {"name": "openai/gpt-oss-20b:free", "size": "Free · 20B · 131K", "provider": "openrouter"},
        # Mistral
        {
            "name": "mistralai/mistral-small-3.1-24b-instruct:free",
            "size": "Free · 24B · 128K",
            "provider": "openrouter",
        },
        # NVIDIA
        {
            "name": "nvidia/nemotron-3-nano-30b-a3b:free",
            "size": "Free · 30B MoE · 256K",
            "provider": "openrouter",
        },
        {
            "name": "nvidia/nemotron-nano-9b-v2:free",
            "size": "Free · 9B · 128K",
            "provider": "openrouter",
        },
        # Nous Research (Meta 405B)
        {
            "name": "nousresearch/hermes-3-llama-3.1-405b:free",
            "size": "Free · 405B · 131K",
            "provider": "openrouter",
        },
    ]


async def list_ollama_models(ollama_url: str) -> list[dict[str, str]]:
    """Fetch available models from Ollama API."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = []
            for m in data.get("models", []):
                name = m.get("name", "")
                size_bytes = m.get("size", 0)
                if size_bytes > 1_000_000_000:
                    size = f"{size_bytes / 1_000_000_000:.1f}GB"
                elif size_bytes > 1_000_000:
                    size = f"{size_bytes / 1_000_000:.0f}MB"
                else:
                    size = f"{size_bytes}B"
                models.append({"name": name, "size": size})
            return models
    except Exception:
        return []


def _make_provider(
    model: str,
    provider_type: str = "ollama",
    ollama_url: str = "",
    openrouter_key: str = "",
) -> Provider:
    """Create a provider based on type."""
    if provider_type == "openrouter":
        return OpenRouterProvider(model=model, api_key=openrouter_key)
    return LocalProvider(
        model=model,
        base_url=f"{ollama_url}/v1",
        context_size=32_768,
    )


# Last run context for replay functionality
_last_run: dict[str, Any] = {
    "compiled": None,
    "graph": None,
    "provider": None,
    "result": None,
    "model": "",
    "provider_type": "",
    "graph_type": "",
    "prompt": "",
    "ollama_url": "",
    "openrouter_key": "",
}


async def run_graph(
    prompt: str,
    model: str,
    provider_type: str = "ollama",
    graph_type: str = "auto",
    ollama_url: str = "",
    openrouter_key: str = "",
    event_bus: EventBus | None = None,
) -> dict[str, Any]:
    """Build and execute a graph, returning the result."""
    provider = _make_provider(model, provider_type, ollama_url, openrouter_key)
    if event_bus is None:
        event_bus = EventBus.get()

    builders = {
        "auto": _build_auto_graph,
        "review": _build_review_graph,
        "chat": _build_chat_graph,
        "chain": _build_chain_graph,
        "parallel": _build_parallel_graph,
        "team": _build_team_graph,
    }

    builder = builders.get(graph_type, _build_chat_graph)
    graph, initial_state = builder(provider, prompt)

    # Emit graph start event
    compiled = graph.compile()
    graph_info = compiled.get_graph_info()
    await event_bus.emit(
        Event(
            event_type=EventType.GRAPH_START,
            data={"nodes": graph_info["nodes"], "edges": graph_info["edges"]},
        )
    )

    start_time = time.time()
    result = await compiled.invoke(initial_state)
    elapsed = time.time() - start_time

    # Store last run for replay
    _last_run["compiled"] = compiled
    _last_run["graph"] = graph
    _last_run["provider"] = provider
    _last_run["result"] = result
    _last_run["model"] = model
    _last_run["provider_type"] = provider_type
    _last_run["graph_type"] = graph_type
    _last_run["prompt"] = prompt
    _last_run["ollama_url"] = ollama_url
    _last_run["openrouter_key"] = openrouter_key

    # Emit graph end event
    await event_bus.emit(
        Event(
            event_type=EventType.GRAPH_END,
            data={"success": result.success, "elapsed_s": round(elapsed, 2)},
        )
    )

    if not result.success:
        return {"success": False, "error": result.error}

    # Build step outputs for the response
    steps = _extract_steps(result)

    # Aggregate usage
    usage = _aggregate_usage(result, model)

    # Emit token update
    await event_bus.emit(
        Event(
            event_type=EventType.TOKEN_UPDATE,
            data={"total_tokens": usage["input_tokens"] + usage["output_tokens"]},
        )
    )

    return {
        "success": True,
        "steps": steps,
        "output": steps[-1]["output"] if steps else "",
        "usage": usage,
        "elapsed_s": round(elapsed, 2),
    }


async def replay_node(
    node_name: str,
    event_bus: EventBus | None = None,
) -> dict[str, Any]:
    """Re-run a single node from the last graph execution."""
    if event_bus is None:
        event_bus = EventBus.get()

    result = _last_run.get("result")
    compiled = _last_run.get("compiled")

    if not result or not compiled:
        return {"success": False, "error": "No previous run to replay from"}

    # Find the step for this node to get its state_before
    # Parallel nodes are stored as "node_a,node_b" in step.node
    target_step = None
    for step in result.steps:
        step_nodes = [n.strip() for n in step.node.split(",")]
        if node_name in step_nodes:
            target_step = step
            break

    if not target_step:
        return {"success": False, "error": f"Node '{node_name}' not found in last run"}

    # Get the node function (NodeConfig has .func attribute)
    node_config = compiled._nodes.get(node_name)
    if not node_config:
        return {"success": False, "error": f"Node function '{node_name}' not found"}
    node_fn = node_config.func

    # Emit replay events
    graph_info = compiled.get_graph_info()
    await event_bus.emit(
        Event(
            event_type=EventType.GRAPH_START,
            data={"nodes": graph_info["nodes"], "edges": graph_info["edges"]},
        )
    )
    await event_bus.emit(
        Event(
            event_type=EventType.GRAPH_NODE_ENTER,
            node_name=node_name,
            data={"replay": True},
        )
    )

    # Re-run the node with its original input state
    start_time = time.time()
    try:
        state_before = dict(target_step.state_before)
        new_output = await node_fn(state_before)
        elapsed = time.time() - start_time

        await event_bus.emit(
            Event(
                event_type=EventType.GRAPH_NODE_EXIT,
                node_name=node_name,
                data={"replay": True},
            )
        )
        await event_bus.emit(
            Event(
                event_type=EventType.GRAPH_END,
                data={"success": True, "elapsed_s": round(elapsed, 2), "replay": True},
            )
        )

        # Build output diff
        diff = {}
        for k, v in new_output.items():
            if not k.startswith("_"):
                diff[k] = v
        output_text = "\n".join(str(v) for v in diff.values()) if diff else ""

        return {
            "success": True,
            "node": node_name,
            "output": output_text,
            "elapsed_s": round(elapsed, 2),
            "replay": True,
        }
    except Exception as e:
        elapsed = time.time() - start_time
        await event_bus.emit(
            Event(
                event_type=EventType.GRAPH_END,
                data={
                    "success": False,
                    "elapsed_s": round(elapsed, 2),
                    "error": str(e),
                },
            )
        )
        return {"success": False, "error": str(e)}


def get_last_run_info() -> dict[str, Any]:
    """Return info about the last graph run (for the UI)."""
    result = _last_run.get("result")
    if not result:
        return {"has_run": False}

    nodes = []
    for step in result.steps:
        # Parallel nodes are stored as "node_a,node_b"
        for n in step.node.split(","):
            nodes.append({"node": n.strip(), "has_state": True})

    return {
        "has_run": True,
        "model": _last_run.get("model", ""),
        "graph_type": _last_run.get("graph_type", ""),
        "prompt": _last_run.get("prompt", "")[:100],
        "nodes": nodes,
        "success": result.success,
    }


def _extract_steps(result: Any) -> list[dict[str, str]]:
    """Extract step outputs from a graph result."""
    steps = []
    for step in result.steps:
        diff = {}
        for k, v in step.state_after.items():
            if k.startswith("_"):
                continue
            before_val = step.state_before.get(k)
            if v != before_val:
                diff[k] = v
        output_text = "\n".join(str(v) for v in diff.values()) if diff else ""
        steps.append({"node": step.node, "output": output_text})
    return steps


def _aggregate_usage(result: Any, model: str) -> dict[str, Any]:
    """Aggregate token usage from a graph result."""
    usage_info = result.state.get("_usage", {})
    usage = {
        "model": model,
        "input_tokens": usage_info.get("input_tokens", 0),
        "output_tokens": usage_info.get("output_tokens", 0),
    }

    total_in = 0
    total_out = 0
    for step in result.steps:
        step_usage = step.state_after.get("_usage", {})
        total_in += step_usage.get("input_tokens", 0)
        total_out += step_usage.get("output_tokens", 0)
    usage["input_tokens"] = max(usage["input_tokens"], total_in)
    usage["output_tokens"] = max(usage["output_tokens"], total_out)
    return usage


# --- Graph Builders ---


def _build_chat_graph(provider: Provider, prompt: str) -> tuple[StateGraph, dict[str, Any]]:
    """Simple single-node chat."""
    respond = llm_node(
        provider=provider,
        system="You are a helpful AI assistant. Be concise and direct.",
        prompt_key="input",
        output_key="response",
    )

    graph = StateGraph()
    graph.add_node("respond", respond)
    graph.add_edge(START, "respond")
    graph.add_edge("respond", END)

    return graph, {"input": prompt}


def _build_review_graph(provider: Provider, prompt: str) -> tuple[StateGraph, dict[str, Any]]:
    """Code review: analyze code quality, security, and suggest fixes."""
    review = llm_node(
        provider=provider,
        system="You are a senior code reviewer. Analyze the code for bugs, security issues, and quality. Be concise, max 5 bullet points.",
        prompt_key="code",
        output_key="review",
    )

    graph = StateGraph()
    graph.add_node("review", review)
    graph.add_edge(START, "review")
    graph.add_edge("review", END)

    return graph, {"code": prompt}


def _build_chain_graph(provider: Provider, prompt: str) -> tuple[StateGraph, dict[str, Any]]:
    """Two-step chain: analyze then fix."""
    analyze = llm_node(
        provider=provider,
        system="Analyze the code and list issues. Be concise, max 3 bullet points.",
        prompt_key="code",
        output_key="analysis",
    )

    fix = llm_node(
        provider=provider,
        system="Fix the code based on the analysis. Return ONLY the fixed code, no explanation.",
        prompt_template=lambda s: (
            f"Analysis:\n{s['analysis']}\n\nOriginal code:\n{s['code']}\n\nFix it:"
        ),
        output_key="fixed_code",
    )

    graph = StateGraph()
    graph.add_node("analyze", analyze)
    graph.add_node("fix", fix)
    graph.add_edge(START, "analyze")
    graph.add_edge("analyze", "fix")
    graph.add_edge("fix", END)

    return graph, {"code": prompt}


def _build_parallel_graph(provider: Provider, prompt: str) -> tuple[StateGraph, dict[str, Any]]:
    """Parallel review: security + performance run simultaneously, then summarize."""
    security = llm_node(
        provider=provider,
        system="You are a security auditor. Find security issues. Max 3 bullet points.",
        prompt_key="code",
        output_key="security",
    )

    performance = llm_node(
        provider=provider,
        system="You are a performance expert. Find performance issues. Max 3 bullet points.",
        prompt_key="code",
        output_key="performance",
    )

    summarize = llm_node(
        provider=provider,
        system="Combine the security and performance reviews into a final summary. Max 5 lines.",
        prompt_template=lambda s: (
            f"Security:\n{s.get('security', '')}\n\nPerformance:\n{s.get('performance', '')}\n\nSummarize:"
        ),
        output_key="summary",
    )

    graph = StateGraph()
    graph.add_node("security", security)
    graph.add_node("performance", performance)
    graph.add_node("summarize", summarize)
    graph.add_edge(START, "security")
    graph.add_edge(START, "performance")
    graph.add_edge("security", "summarize")
    graph.add_edge("performance", "summarize")
    graph.add_edge("summarize", END)

    return graph, {"code": prompt}


def _build_auto_graph(provider: Provider, prompt: str) -> tuple[StateGraph, dict[str, Any]]:
    """Auto-routing: classify the prompt then route to the right handler."""
    classify = llm_node(
        provider=provider,
        system=(
            "Classify the user's input into exactly one category. "
            "Reply with ONLY the category name, nothing else.\n"
            "Categories: CODE_REVIEW, BUG_FIX, QUESTION, TASK"
        ),
        prompt_key="input",
        output_key="classification",
    )

    handle_review = llm_node(
        provider=provider,
        system="You are a code reviewer. Analyze the code for issues. Be concise.",
        prompt_key="input",
        output_key="response",
    )

    handle_bug = llm_node(
        provider=provider,
        system="You are a debugging expert. Identify the bug and suggest a fix. Be concise.",
        prompt_key="input",
        output_key="response",
    )

    handle_question = llm_node(
        provider=provider,
        system="You are a helpful assistant. Answer the question concisely and accurately.",
        prompt_key="input",
        output_key="response",
    )

    handle_task = llm_node(
        provider=provider,
        system="You are a task executor. Break down and address the task step by step. Be concise.",
        prompt_key="input",
        output_key="response",
    )

    def route(state):
        c = state.get("classification", "").strip().upper()
        if "CODE_REVIEW" in c or "REVIEW" in c:
            return "review"
        elif "BUG" in c or "FIX" in c:
            return "bug_fix"
        elif "QUESTION" in c:
            return "question"
        return "task"

    graph = StateGraph()
    graph.add_node("classify", classify)
    graph.add_node("review", handle_review)
    graph.add_node("bug_fix", handle_bug)
    graph.add_node("question", handle_question)
    graph.add_node("task", handle_task)

    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        route,
        {"review": "review", "bug_fix": "bug_fix", "question": "question", "task": "task"},
    )
    graph.add_edge("review", END)
    graph.add_edge("bug_fix", END)
    graph.add_edge("question", END)
    graph.add_edge("task", END)

    return graph, {"input": prompt}


# --- Agent-aware node wrapper ---


def _agent_node(
    agent_name: str,
    provider: Provider,
    system: str,
    prompt_key: str | None = None,
    prompt_template: Callable | None = None,
    output_key: str = "response",
    role: str = "",
    event_bus: EventBus | None = None,
    parent_agent: str | None = None,
    task_description: str = "",
) -> Callable:
    """Wrap an LLM call to emit agent lifecycle + cooperation events."""
    inner = llm_node(
        provider=provider,
        system=system,
        prompt_key=prompt_key,
        prompt_template=prompt_template,
        output_key=output_key,
    )

    async def wrapper(state: dict[str, Any]) -> dict[str, Any]:
        bus = event_bus or EventBus.get()
        task_id = str(uuid.uuid4())[:8]

        # Agent spawn
        await bus.emit(
            Event(
                event_type=EventType.AGENT_SPAWN,
                agent_name=agent_name,
                data={
                    "provider": provider.model_id,
                    "role": role,
                    "tools": [],
                },
            )
        )

        # Task delegation from parent
        if parent_agent:
            await bus.emit(
                Event(
                    event_type=EventType.TASK_ASSIGNED,
                    data={
                        "task_id": task_id,
                        "from_agent": parent_agent,
                        "to_agent": agent_name,
                        "description": task_description or f"Process: {output_key}",
                        "priority": "normal",
                    },
                )
            )

        # Agent step
        await bus.emit(
            Event(
                event_type=EventType.AGENT_STEP,
                agent_name=agent_name,
                data={"step": "llm_call", "model": provider.model_id},
            )
        )

        # Run the actual LLM node
        result = await inner(state)

        # Task completed
        if parent_agent:
            await bus.emit(
                Event(
                    event_type=EventType.TASK_COMPLETED,
                    data={
                        "task_id": task_id,
                        "from_agent": agent_name,
                        "to_agent": parent_agent,
                        "success": True,
                        "summary": str(result.get(output_key, ""))[:100],
                    },
                )
            )

        # Agent complete
        await bus.emit(
            Event(
                event_type=EventType.AGENT_COMPLETE,
                agent_name=agent_name,
                data={"output_key": output_key},
            )
        )

        return result

    return wrapper


# --- Team graph: multi-agent orchestration ---


def _build_team_graph(provider: Provider, prompt: str) -> tuple[StateGraph, dict[str, Any]]:
    """Team orchestration: team-lead classifies, delegates to sub-agents, summarizes.

    Flow:
      team-lead (classify) -> [backend-dev, frontend-dev] in parallel -> team-lead (summarize)

    Emits agent.spawn, cooperation.task_assigned, agent.complete, cooperation.task_completed
    events so the dashboard shows real multi-agent interaction.
    """
    bus = EventBus.get()

    # Team-lead: classify the task
    classify = _agent_node(
        agent_name="team-lead",
        provider=provider,
        system=(
            "You are the team lead. Classify this task and decide which sub-agents should handle it.\n"
            "Reply with a brief task breakdown (2-3 lines):\n"
            "- What the backend-dev should do\n"
            "- What the frontend-dev should do\n"
            "Be concise."
        ),
        prompt_key="input",
        output_key="plan",
        role="Team Lead — task decomposition",
        event_bus=bus,
    )

    # Backend sub-agent
    backend = _agent_node(
        agent_name="backend-dev",
        provider=provider,
        system=(
            "You are a backend developer. Focus on server-side logic, APIs, data models, "
            "and infrastructure. Be concise and practical."
        ),
        prompt_template=lambda s: (
            f"Team lead's plan:\n{s.get('plan', '')}\n\n"
            f"Original request:\n{s['input']}\n\n"
            f"Provide your backend analysis/solution:"
        ),
        output_key="backend_output",
        role="Backend Developer",
        event_bus=bus,
        parent_agent="team-lead",
        task_description="Handle backend/API aspects of the task",
    )

    # Frontend sub-agent
    frontend = _agent_node(
        agent_name="frontend-dev",
        provider=provider,
        system=(
            "You are a frontend developer. Focus on UI/UX, client-side logic, "
            "and user experience. Be concise and practical."
        ),
        prompt_template=lambda s: (
            f"Team lead's plan:\n{s.get('plan', '')}\n\n"
            f"Original request:\n{s['input']}\n\n"
            f"Provide your frontend analysis/solution:"
        ),
        output_key="frontend_output",
        role="Frontend Developer",
        event_bus=bus,
        parent_agent="team-lead",
        task_description="Handle frontend/UI aspects of the task",
    )

    # Team-lead: summarize results
    summarize = _agent_node(
        agent_name="team-lead",
        provider=provider,
        system=(
            "You are the team lead. Combine the backend and frontend outputs "
            "into a coherent final answer. Be concise but complete."
        ),
        prompt_template=lambda s: (
            f"Original request:\n{s['input']}\n\n"
            f"Backend developer:\n{s.get('backend_output', '')}\n\n"
            f"Frontend developer:\n{s.get('frontend_output', '')}\n\n"
            f"Provide the final combined answer:"
        ),
        output_key="response",
        role="Team Lead — synthesis",
        event_bus=bus,
    )

    graph = StateGraph()
    graph.add_node("team-lead-plan", classify)
    graph.add_node("backend-dev", backend)
    graph.add_node("frontend-dev", frontend)
    graph.add_node("team-lead-summarize", summarize)

    graph.add_edge(START, "team-lead-plan")
    graph.add_edge("team-lead-plan", "backend-dev")
    graph.add_edge("team-lead-plan", "frontend-dev")
    graph.add_edge("backend-dev", "team-lead-summarize")
    graph.add_edge("frontend-dev", "team-lead-summarize")
    graph.add_edge("team-lead-summarize", END)

    return graph, {"input": prompt}
