"""Graph execution for the dashboard prompt.

Builds and runs StateGraph pipelines using Ollama/local models.
Emits events to the EventBus so the dashboard shows real-time progress.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..core.graph import END, START, StateGraph
from ..core.llm_nodes import llm_node
from ..providers.local import LocalProvider
from .events import Event, EventBus, EventType


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


def _make_provider(model: str, ollama_url: str) -> LocalProvider:
    """Create a LocalProvider pointing at the given Ollama instance."""
    return LocalProvider(
        model=model,
        base_url=f"{ollama_url}/v1",
        context_size=32_768,
    )


async def run_graph(
    prompt: str,
    model: str,
    graph_type: str,
    ollama_url: str,
    event_bus: EventBus,
) -> dict[str, Any]:
    """Build and execute a graph, returning the result."""
    provider = _make_provider(model, ollama_url)

    builders = {
        "auto": _build_auto_graph,
        "review": _build_review_graph,
        "chat": _build_chat_graph,
        "chain": _build_chain_graph,
        "parallel": _build_parallel_graph,
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
    steps = []
    for step in result.steps:
        node_name = step.node
        # Find the output key(s) that changed
        diff = {}
        for k, v in step.state_after.items():
            if k.startswith("_"):
                continue
            before_val = step.state_before.get(k)
            if v != before_val:
                diff[k] = v
        output_text = "\n".join(str(v) for v in diff.values()) if diff else ""
        steps.append({"node": node_name, "output": output_text})

    # Aggregate usage
    usage_info = result.state.get("_usage", {})
    usage = {
        "model": model,
        "input_tokens": usage_info.get("input_tokens", 0),
        "output_tokens": usage_info.get("output_tokens", 0),
    }

    # Also collect usage from all steps
    total_in = 0
    total_out = 0
    for step in result.steps:
        step_usage = step.state_after.get("_usage", {})
        total_in += step_usage.get("input_tokens", 0)
        total_out += step_usage.get("output_tokens", 0)
    usage["input_tokens"] = max(usage["input_tokens"], total_in)
    usage["output_tokens"] = max(usage["output_tokens"], total_out)

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


# --- Graph Builders ---


def _build_chat_graph(provider: LocalProvider, prompt: str) -> tuple[StateGraph, dict[str, Any]]:
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


def _build_review_graph(provider: LocalProvider, prompt: str) -> tuple[StateGraph, dict[str, Any]]:
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


def _build_chain_graph(provider: LocalProvider, prompt: str) -> tuple[StateGraph, dict[str, Any]]:
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


def _build_parallel_graph(
    provider: LocalProvider, prompt: str
) -> tuple[StateGraph, dict[str, Any]]:
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


def _build_auto_graph(provider: LocalProvider, prompt: str) -> tuple[StateGraph, dict[str, Any]]:
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
