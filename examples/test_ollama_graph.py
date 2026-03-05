"""Test the StateGraph with Ollama (Qwen2.5-Coder) running locally.

Usage:
    python examples/test_ollama_graph.py

Requires:
    - Ollama running locally (ollama serve)
    - Model pulled: ollama pull qwen2.5-coder:7b-instruct
    - pip install openai
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_orchestrator.core.graph import END, START, StateGraph  # noqa: E402
from agent_orchestrator.core.llm_nodes import llm_node  # noqa: E402
from agent_orchestrator.core.checkpoint import InMemoryCheckpointer  # noqa: E402
from agent_orchestrator.providers.local import LocalProvider  # noqa: E402


def make_provider(model: str = "qwen2.5-coder:7b-instruct", ctx: int = 32_768):
    return LocalProvider(model=model, context_size=ctx)


async def example_1_simple():
    """Simple: one node calls Qwen and returns the response."""
    print("\n=== Example 1: Simple LLM call (Ollama/Qwen) ===\n")

    qwen = make_provider()

    analyze = llm_node(
        provider=qwen,
        system="You are a concise code reviewer. Reply in 2-3 sentences max.",
        prompt_key="code",
        output_key="review",
    )

    graph = StateGraph()
    graph.add_node("review", analyze)
    graph.add_edge(START, "review")
    graph.add_edge("review", END)

    result = await graph.compile().invoke({
        "code": "def fibonacci(n): return n if n <= 1 else fibonacci(n-1) + fibonacci(n-2)"
    })

    if not result.success:
        print(f"ERROR: {result.error}")
        return

    print(f"Review: {result.state['review']}")
    print(f"Tokens: {result.state['_usage']['input_tokens']} in, {result.state['_usage']['output_tokens']} out")
    print("Cost: $0.00 (local)")


async def example_2_chain():
    """Chain: analyze -> fix, each node calls Qwen."""
    print("\n=== Example 2: Multi-step chain (Ollama/Qwen) ===\n")

    qwen = make_provider()

    analyze = llm_node(
        provider=qwen,
        system="Analyze the code and list issues. Be concise, max 3 bullet points.",
        prompt_key="code",
        output_key="analysis",
    )

    fix = llm_node(
        provider=qwen,
        system="You fix code based on analysis. Return ONLY the fixed code, no explanation.",
        prompt_template=lambda s: f"Analysis:\n{s['analysis']}\n\nOriginal code:\n{s['code']}\n\nFix it:",
        output_key="fixed_code",
    )

    graph = StateGraph()
    graph.add_node("analyze", analyze)
    graph.add_node("fix", fix)
    graph.add_edge(START, "analyze")
    graph.add_edge("analyze", "fix")
    graph.add_edge("fix", END)

    cp = InMemoryCheckpointer()
    result = await graph.compile(checkpointer=cp).invoke(
        {"code": "def avg(lst): return sum(lst) / len(lst)"},
        thread_id="fix-session",
    )

    if not result.success:
        print(f"ERROR: {result.error}")
        return

    print(f"Analysis:\n{result.state['analysis']}\n")
    print(f"Fixed code:\n{result.state['fixed_code']}\n")
    print(f"Steps: {len(result.steps)}")


async def example_3_parallel():
    """Parallel: two Qwen calls run simultaneously, then merge."""
    print("\n=== Example 3: Parallel LLM calls (Ollama/Qwen) ===\n")

    qwen = make_provider()

    security_review = llm_node(
        provider=qwen,
        system="You are a security auditor. Find security issues. Max 3 bullet points.",
        prompt_key="code",
        output_key="security",
    )

    perf_review = llm_node(
        provider=qwen,
        system="You are a performance expert. Find performance issues. Max 3 bullet points.",
        prompt_key="code",
        output_key="performance",
    )

    summarize = llm_node(
        provider=qwen,
        system="Combine the security and performance reviews into a final summary. Max 5 lines.",
        prompt_template=lambda s: f"Security:\n{s.get('security','')}\n\nPerformance:\n{s.get('performance','')}\n\nSummarize:",
        output_key="summary",
    )

    graph = StateGraph()
    graph.add_node("security", security_review)
    graph.add_node("performance", perf_review)
    graph.add_node("summarize", summarize)
    graph.add_edge(START, "security")
    graph.add_edge(START, "performance")
    graph.add_edge("security", "summarize")
    graph.add_edge("performance", "summarize")
    graph.add_edge("summarize", END)

    result = await graph.compile().invoke({
        "code": """
import sqlite3
def get_user(name):
    conn = sqlite3.connect('db.sqlite')
    cursor = conn.execute(f"SELECT * FROM users WHERE name = '{name}'")
    results = cursor.fetchall()
    conn.close()
    return results
"""
    })

    if not result.success:
        print(f"ERROR: {result.error}")
        return

    print(f"Security:\n{result.state['security']}\n")
    print(f"Performance:\n{result.state['performance']}\n")
    print(f"Summary:\n{result.state['summary']}\n")

    parallel_steps = [s for s in result.steps if s.parallel_group]
    if parallel_steps:
        print(f"Parallel nodes: {parallel_steps[0].parallel_group}")


async def example_4_conditional():
    """Conditional routing based on Qwen's classification."""
    print("\n=== Example 4: Conditional routing (Ollama/Qwen) ===\n")

    qwen = make_provider()

    classify = llm_node(
        provider=qwen,
        system="Classify the input as exactly one of: BUG, FEATURE, REFACTOR. Reply with ONLY the classification word.",
        prompt_key="request",
        output_key="classification",
    )

    handle_bug = llm_node(
        provider=qwen,
        system="You handle bug reports. Suggest a fix in 2 sentences.",
        prompt_template=lambda s: f"Bug report: {s['request']}",
        output_key="response",
    )

    handle_feature = llm_node(
        provider=qwen,
        system="You handle feature requests. Outline the approach in 2 sentences.",
        prompt_template=lambda s: f"Feature request: {s['request']}",
        output_key="response",
    )

    handle_refactor = llm_node(
        provider=qwen,
        system="You handle refactoring tasks. Suggest improvements in 2 sentences.",
        prompt_template=lambda s: f"Refactoring task: {s['request']}",
        output_key="response",
    )

    def route_by_classification(state):
        c = state.get("classification", "").strip().upper()
        if "BUG" in c:
            return "bug"
        elif "FEATURE" in c:
            return "feature"
        return "refactor"

    graph = StateGraph()
    graph.add_node("classify", classify)
    graph.add_node("bug", handle_bug)
    graph.add_node("feature", handle_feature)
    graph.add_node("refactor", handle_refactor)
    graph.add_edge(START, "classify")
    graph.add_conditional_edges(
        "classify",
        route_by_classification,
        {"bug": "bug", "feature": "feature", "refactor": "refactor"},
    )
    graph.add_edge("bug", END)
    graph.add_edge("feature", END)
    graph.add_edge("refactor", END)

    result = await graph.compile().invoke({
        "request": "The login page crashes when the password contains a single quote character"
    })

    if not result.success:
        print(f"ERROR: {result.error}")
        return

    print(f"Classification: {result.state['classification'].strip()}")
    print(f"Response: {result.state['response']}")
    print(f"Route taken: {' -> '.join(s.node for s in result.steps)}")


async def run_example(name, fn):
    try:
        await fn()
    except Exception as e:
        print(f"  FAILED: {e}")


async def main():
    # Check Ollama is running
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key="ollama", base_url="http://localhost:11434/v1")
        models = await client.models.list()
        available = [m.id for m in models.data]
        print(f"Ollama models available: {available}")
        if "qwen2.5-coder:7b-instruct" not in available:
            print("WARNING: qwen2.5-coder:7b-instruct not found. Run: ollama pull qwen2.5-coder:7b-instruct")
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: Cannot connect to Ollama at localhost:11434 — {e}")
        print("  Start Ollama with: ollama serve")
        sys.exit(1)

    for name, fn in [
        ("simple", example_1_simple),
        ("chain", example_2_chain),
        ("parallel", example_3_parallel),
        ("conditional", example_4_conditional),
    ]:
        await run_example(name, fn)

    print("\n=== All examples completed ===")


if __name__ == "__main__":
    asyncio.run(main())
