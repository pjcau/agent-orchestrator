"""Graph execution for the dashboard prompt.

Builds and runs StateGraph pipelines using Ollama/local models.
Emits events to the EventBus so the dashboard shows real-time progress.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable

import httpx

from ..core.cache import CachePolicy
from ..core.conversation import ConversationManager
from ..core.graph import END, START, StateGraph
from ..core.llm_nodes import llm_node
from ..core.provider import Provider
from ..providers.local import LocalProvider
from ..providers.openrouter import OpenRouterProvider
from .events import Event, EventBus, EventType

# Default cache policy for graph LLM nodes (5 min TTL)
_GRAPH_CACHE_POLICY = CachePolicy(ttl_seconds=300, max_entries=500)

logger = logging.getLogger(__name__)


async def list_openrouter_models(api_key: str) -> list[dict[str, str]]:
    """Return available OpenRouter models (curated list with pricing).

    The curated list is always returned regardless of whether an API key
    is configured — the key is only needed for actual LLM calls, not for
    browsing the model catalog.
    """
    # Curated models: paid first, then free
    return [
        # --- Paid models ---
        {
            "name": "qwen/qwen3.5-flash-02-23",
            "size": "$0.06/$0.30 · Flash · 262K",
            "provider": "openrouter",
        },
        {
            "name": "qwen/qwen3-coder-next",
            "size": "$0.12/$0.75 · Coder Next · 262K",
            "provider": "openrouter",
        },
        {
            "name": "qwen/qwen3.5-397b-a17b",
            "size": "$0.39/$2.34 · 397B MoE · 262K",
            "provider": "openrouter",
        },
        # --- Free models ---
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
        {
            "name": "openai/gpt-oss-120b:free",
            "size": "Free · 120B · 131K",
            "provider": "openrouter",
        },
        {"name": "openai/gpt-oss-20b:free", "size": "Free · 20B · 131K", "provider": "openrouter"},
        {
            "name": "nousresearch/hermes-3-llama-3.1-405b:free",
            "size": "Free · 405B · 131K",
            "provider": "openrouter",
        },
        {
            "name": "meta-llama/llama-3.3-70b-instruct:free",
            "size": "Free · 70B · 128K",
            "provider": "openrouter",
        },
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
        {
            "name": "meta-llama/llama-3.2-3b-instruct:free",
            "size": "Free · 3B · 131K",
            "provider": "openrouter",
        },
        {
            "name": "mistralai/mistral-small-3.1-24b-instruct:free",
            "size": "Free · 24B · 128K",
            "provider": "openrouter",
        },
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
    conversation_id: str | None = None,
    conversation_manager: ConversationManager | None = None,
) -> dict[str, Any]:
    """Build and execute a graph, returning the result.

    Args:
        conversation_id: Optional thread ID for multi-turn memory.
        conversation_manager: If provided with conversation_id,
            previous exchanges are prepended to the prompt and
            the new exchange is persisted.
    """
    provider = _make_provider(model, provider_type, ollama_url, openrouter_key)
    if event_bus is None:
        event_bus = EventBus.get()

    # Prepend conversation history to the prompt
    enriched_prompt = prompt
    if conversation_id and conversation_manager:
        history = await conversation_manager.get_history(conversation_id)
        if history:
            history_lines = []
            for msg in history:
                label = "User" if msg.role == "user" else "Assistant"
                history_lines.append(f"{label}: {msg.content}")
            history_context = "\n".join(history_lines)
            enriched_prompt = (
                f"Previous conversation:\n{history_context}\n\nCurrent request:\n{prompt}"
            )

    builders = {
        "auto": _build_auto_graph,
        "review": _build_review_graph,
        "chat": _build_chat_graph,
        "chain": _build_chain_graph,
        "parallel": _build_parallel_graph,
        "team": _build_team_graph,
    }

    builder = builders.get(graph_type, _build_chat_graph)
    graph, initial_state = builder(provider, enriched_prompt)

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
    output_text = steps[-1]["output"] if steps else ""

    # Save to conversation memory
    if conversation_id and conversation_manager:

        async def _passthrough_graph(msgs):
            return output_text

        await conversation_manager.send(
            conversation_id,
            prompt,
            _passthrough_graph,
        )

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
        "output": output_text,
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
        logger.exception("Replay node failed")
        await event_bus.emit(
            Event(
                event_type=EventType.GRAPH_END,
                data={
                    "success": False,
                    "elapsed_s": round(elapsed, 2),
                    "error": type(e).__name__,
                },
            )
        )
        return {"success": False, "error": "Node replay failed"}


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
        cache_policy=_GRAPH_CACHE_POLICY,
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
        cache_policy=_GRAPH_CACHE_POLICY,
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
        cache_policy=_GRAPH_CACHE_POLICY,
    )

    fix = llm_node(
        provider=provider,
        system="Fix the code based on the analysis. Return ONLY the fixed code, no explanation.",
        prompt_template=lambda s: (
            f"Analysis:\n{s['analysis']}\n\nOriginal code:\n{s['code']}\n\nFix it:"
        ),
        output_key="fixed_code",
        cache_policy=_GRAPH_CACHE_POLICY,
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
        cache_policy=_GRAPH_CACHE_POLICY,
    )

    performance = llm_node(
        provider=provider,
        system="You are a performance expert. Find performance issues. Max 3 bullet points.",
        prompt_key="code",
        output_key="performance",
        cache_policy=_GRAPH_CACHE_POLICY,
    )

    summarize = llm_node(
        provider=provider,
        system="Combine the security and performance reviews into a final summary. Max 5 lines.",
        prompt_template=lambda s: (
            f"Security:\n{s.get('security', '')}\n\nPerformance:\n{s.get('performance', '')}\n\nSummarize:"
        ),
        output_key="summary",
        cache_policy=_GRAPH_CACHE_POLICY,
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
        cache_policy=_GRAPH_CACHE_POLICY,
    )

    handle_review = llm_node(
        provider=provider,
        system="You are a code reviewer. Analyze the code for issues. Be concise.",
        prompt_key="input",
        output_key="response",
        cache_policy=_GRAPH_CACHE_POLICY,
    )

    handle_bug = llm_node(
        provider=provider,
        system="You are a debugging expert. Identify the bug and suggest a fix. Be concise.",
        prompt_key="input",
        output_key="response",
        cache_policy=_GRAPH_CACHE_POLICY,
    )

    handle_question = llm_node(
        provider=provider,
        system="You are a helpful assistant. Answer the question concisely and accurately.",
        prompt_key="input",
        output_key="response",
        cache_policy=_GRAPH_CACHE_POLICY,
    )

    handle_task = llm_node(
        provider=provider,
        system="You are a task executor. Break down and address the task step by step. Be concise.",
        prompt_key="input",
        output_key="response",
        cache_policy=_GRAPH_CACHE_POLICY,
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


# --- Category detection for team graph ---

_GRAPH_CATEGORY_KEYWORDS: dict[str, list[str]] = {
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
        "kpi",
        "metrics",
        "bi",
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

# Team compositions per category: (agent_name, system_prompt, output_key, role, task_desc)
_TEAM_COMPOSITIONS: dict[str, list[tuple[str, str, str, str, str]]] = {
    "finance": [
        (
            "financial-analyst",
            "You are a financial analyst. Focus on financial modeling, valuation, "
            "forecasting, and ratio analysis. Be precise with numbers.",
            "agent_a_output",
            "Financial Analyst",
            "Handle financial analysis and modeling",
        ),
        (
            "risk-analyst",
            "You are a risk analyst. Focus on risk assessment, VaR, stress testing, "
            "and regulatory compliance. Be thorough with risk scenarios.",
            "agent_b_output",
            "Risk Analyst",
            "Handle risk assessment and compliance",
        ),
    ],
    "data-science": [
        (
            "data-analyst",
            "You are a data analyst. Focus on exploratory analysis, statistical testing, "
            "and data visualization. Be data-driven.",
            "agent_a_output",
            "Data Analyst",
            "Handle data analysis and insights",
        ),
        (
            "ml-engineer",
            "You are an ML engineer. Focus on model training, evaluation, "
            "feature engineering, and MLOps. Be practical.",
            "agent_b_output",
            "ML Engineer",
            "Handle ML modeling and evaluation",
        ),
    ],
    "marketing": [
        (
            "content-strategist",
            "You are a content strategist. Focus on content planning, brand voice, "
            "editorial calendar, and SEO copy. Be creative.",
            "agent_a_output",
            "Content Strategist",
            "Handle content strategy and planning",
        ),
        (
            "growth-hacker",
            "You are a growth hacker. Focus on acquisition funnels, A/B testing, "
            "conversion optimization, and growth loops. Be data-informed.",
            "agent_b_output",
            "Growth Hacker",
            "Handle growth experiments and optimization",
        ),
    ],
    "software-engineering": [
        (
            "backend-dev",
            "You are a backend developer. Focus on server-side logic, APIs, data models, "
            "and infrastructure. Be concise and practical.",
            "agent_a_output",
            "Backend Developer",
            "Handle backend/API aspects of the task",
        ),
        (
            "frontend-dev",
            "You are a frontend developer. Focus on UI/UX, client-side logic, "
            "and user experience. Be concise and practical.",
            "agent_b_output",
            "Frontend Developer",
            "Handle frontend/UI aspects of the task",
        ),
    ],
}


def _detect_graph_category(prompt: str) -> str:
    """Detect the most likely category from the prompt text."""
    prompt_lower = prompt.lower()
    scores: dict[str, int] = {}
    for category, keywords in _GRAPH_CATEGORY_KEYWORDS.items():
        scores[category] = sum(1 for kw in keywords if kw in prompt_lower)
    best = max(scores, key=scores.get) if scores else "software-engineering"
    return best if scores.get(best, 0) > 0 else "software-engineering"


# --- Team graph: multi-agent orchestration ---


def _build_team_graph(provider: Provider, prompt: str) -> tuple[StateGraph, dict[str, Any]]:
    """Team orchestration: team-lead classifies, delegates to category-appropriate sub-agents.

    Flow:
      team-lead (classify) -> [agent_a, agent_b] in parallel -> team-lead (summarize)

    Dynamically selects agents based on task category (finance, data-science, marketing,
    or software-engineering). Emits agent lifecycle and cooperation events.
    """
    bus = EventBus.get()
    category = _detect_graph_category(prompt)
    team = _TEAM_COMPOSITIONS.get(category, _TEAM_COMPOSITIONS["software-engineering"])
    agent_a_name, agent_a_sys, _, agent_a_role, agent_a_task = team[0]
    agent_b_name, agent_b_sys, _, agent_b_role, agent_b_task = team[1]

    # Team-lead: classify the task
    classify = _agent_node(
        agent_name="team-lead",
        provider=provider,
        system=(
            "You are the team lead. Classify this task and decide how to delegate.\n"
            f"Your team for this task: {agent_a_role} and {agent_b_role}.\n"
            "Reply with a brief task breakdown (2-3 lines):\n"
            f"- What {agent_a_name} should do\n"
            f"- What {agent_b_name} should do\n"
            "Be concise."
        ),
        prompt_key="input",
        output_key="plan",
        role="Team Lead — task decomposition",
        event_bus=bus,
    )

    # Sub-agent A
    agent_a = _agent_node(
        agent_name=agent_a_name,
        provider=provider,
        system=agent_a_sys,
        prompt_template=lambda s: (
            f"Team lead's plan:\n{s.get('plan', '')}\n\n"
            f"Original request:\n{s['input']}\n\n"
            f"Provide your analysis/solution:"
        ),
        output_key="agent_a_output",
        role=agent_a_role,
        event_bus=bus,
        parent_agent="team-lead",
        task_description=agent_a_task,
    )

    # Sub-agent B
    agent_b = _agent_node(
        agent_name=agent_b_name,
        provider=provider,
        system=agent_b_sys,
        prompt_template=lambda s: (
            f"Team lead's plan:\n{s.get('plan', '')}\n\n"
            f"Original request:\n{s['input']}\n\n"
            f"Provide your analysis/solution:"
        ),
        output_key="agent_b_output",
        role=agent_b_role,
        event_bus=bus,
        parent_agent="team-lead",
        task_description=agent_b_task,
    )

    # Team-lead: summarize results
    summarize = _agent_node(
        agent_name="team-lead",
        provider=provider,
        system=(
            f"You are the team lead. Combine the {agent_a_role} and {agent_b_role} outputs "
            "into a coherent final answer. Be concise but complete."
        ),
        prompt_template=lambda s: (
            f"Original request:\n{s['input']}\n\n"
            f"{agent_a_role}:\n{s.get('agent_a_output', '')}\n\n"
            f"{agent_b_role}:\n{s.get('agent_b_output', '')}\n\n"
            f"Provide the final combined answer:"
        ),
        output_key="response",
        role="Team Lead — synthesis",
        event_bus=bus,
    )

    graph = StateGraph()
    graph.add_node("team-lead-plan", classify)
    graph.add_node(agent_a_name, agent_a)
    graph.add_node(agent_b_name, agent_b)
    graph.add_node("team-lead-summarize", summarize)

    graph.add_edge(START, "team-lead-plan")
    graph.add_edge("team-lead-plan", agent_a_name)
    graph.add_edge("team-lead-plan", agent_b_name)
    graph.add_edge(agent_a_name, "team-lead-summarize")
    graph.add_edge(agent_b_name, "team-lead-summarize")
    graph.add_edge("team-lead-summarize", END)

    return graph, {"input": prompt}
