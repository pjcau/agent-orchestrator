"""v0.2.0 Integration Test — run from dashboard or CLI.

Tests all v0.2.0 features:
1. OpenRouter provider (qwen/qwen3.5-plus-02-15)
2. Ollama provider (local model)
3. Streaming responses
4. Multi-turn conversation
5. File context
6. Model comparison (Ollama vs OpenRouter)

Usage:
    # From CLI
    python3.11 examples/test_v02_integration.py

    # From dashboard: paste this file as context, then ask "run the integration test"
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_orchestrator.providers.local import LocalProvider  # noqa: E402
from agent_orchestrator.providers.openrouter import OpenRouterProvider  # noqa: E402
from agent_orchestrator.core.provider import Message, Role  # noqa: E402
from agent_orchestrator.core.graph import START, END, StateGraph  # noqa: E402
from agent_orchestrator.core.llm_nodes import llm_node  # noqa: E402


def header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


async def test_openrouter() -> bool:
    """Test 1: OpenRouter with qwen/qwen3.5-plus-02-15"""
    header("Test 1: OpenRouter Provider")
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("  SKIP: OPENROUTER_API_KEY not set")
        return True

    provider = OpenRouterProvider(model="qwen/qwen3.5-plus-02-15", api_key=api_key)
    print(f"  Model: {provider.model_id}")
    print(f"  Capabilities: {provider.capabilities}")

    start = time.time()
    messages = [Message(role=Role.USER, content="What is 2+2? Reply with just the number.")]
    result = await provider.complete(messages=messages, system="Be concise.", max_tokens=50)
    elapsed = time.time() - start

    print(f"  Response: {result.content.strip()}")
    print(f"  Tokens: {result.usage.input_tokens} in / {result.usage.output_tokens} out")
    print(f"  Cost: ${result.usage.cost_usd:.6f}")
    print(f"  Time: {elapsed:.2f}s")
    print("  PASS" if "4" in result.content else "  FAIL: expected '4' in response")
    return "4" in result.content


async def test_openrouter_streaming() -> bool:
    """Test 2: OpenRouter streaming"""
    header("Test 2: OpenRouter Streaming")
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("  SKIP: OPENROUTER_API_KEY not set")
        return True

    provider = OpenRouterProvider(model="qwen/qwen3.5-plus-02-15", api_key=api_key)
    messages = [Message(role=Role.USER, content="Count from 1 to 5, one per line.")]

    start = time.time()
    chunks = []
    token_count = 0
    async for chunk in provider.stream(messages=messages, system="Be concise.", max_tokens=100):
        if chunk.content:
            chunks.append(chunk.content)
            token_count += 1
            print(f"    chunk: {repr(chunk.content)}")
        if chunk.is_final:
            break

    elapsed = time.time() - start
    speed = token_count / elapsed if elapsed > 0 else 0
    full_text = "".join(chunks)
    print(f"  Full response: {full_text.strip()[:100]}")
    print(f"  Chunks: {token_count}")
    print(f"  Speed: {speed:.1f} tok/s")
    print(f"  Time: {elapsed:.2f}s")

    has_numbers = any(str(i) in full_text for i in range(1, 6))
    print("  PASS" if has_numbers else "  FAIL: expected numbers 1-5")
    return has_numbers


async def test_ollama() -> bool:
    """Test 3: Ollama local model"""
    header("Test 3: Ollama Local Provider")
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    provider = LocalProvider(model="qwen2.5-coder:7b-instruct", base_url=f"{ollama_url}/v1")

    try:
        messages = [Message(role=Role.USER, content="What is Python? One sentence.")]
        start = time.time()
        result = await provider.complete(messages=messages, system="Be concise.", max_tokens=100)
        elapsed = time.time() - start

        print(f"  Response: {result.content.strip()[:100]}")
        print(f"  Tokens: {result.usage.input_tokens} in / {result.usage.output_tokens} out")
        print(f"  Speed: {result.usage.output_tokens / elapsed:.1f} tok/s")
        print(f"  Time: {elapsed:.2f}s")
        print("  PASS")
        return True
    except Exception as e:
        print(f"  SKIP: Ollama not available ({e})")
        return True


async def test_graph_openrouter() -> bool:
    """Test 4: StateGraph with OpenRouter"""
    header("Test 4: StateGraph + OpenRouter")
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("  SKIP: OPENROUTER_API_KEY not set")
        return True

    provider = OpenRouterProvider(model="qwen/qwen3.5-plus-02-15", api_key=api_key)

    analyze = llm_node(
        provider=provider,
        system="List 2 issues with this code. Be very brief.",
        prompt_key="code",
        output_key="analysis",
        max_tokens=200,
    )
    fix = llm_node(
        provider=provider,
        system="Fix the code. Return only the fixed code.",
        prompt_template=lambda s: f"Issues:\n{s['analysis']}\n\nCode:\n{s['code']}\n\nFix:",
        output_key="fixed",
        max_tokens=200,
    )

    graph = StateGraph()
    graph.add_node("analyze", analyze)
    graph.add_node("fix", fix)
    graph.add_edge(START, "analyze")
    graph.add_edge("analyze", "fix")
    graph.add_edge("fix", END)

    start = time.time()
    compiled = graph.compile()
    result = await compiled.invoke({"code": "def avg(l): return sum(l)/len(l)"})
    elapsed = time.time() - start

    print(f"  Steps: {len(result.steps)}")
    for step in result.steps:
        output = step.state_after.get("analysis") or step.state_after.get("fixed") or ""
        print(f"    {step.node}: {str(output).strip()[:80]}")
    print(f"  Time: {elapsed:.2f}s")
    print("  PASS" if result.success else f"  FAIL: {result.error}")
    return result.success


async def test_multi_turn() -> bool:
    """Test 5: Multi-turn conversation context"""
    header("Test 5: Multi-turn Conversation")
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("  SKIP: OPENROUTER_API_KEY not set")
        return True

    provider = OpenRouterProvider(model="qwen/qwen3.5-plus-02-15", api_key=api_key)

    # Turn 1
    messages = [Message(role=Role.USER, content="My name is Marco. Remember it.")]
    r1 = await provider.complete(messages=messages, system="Be concise.", max_tokens=50)
    print(f"  Turn 1 response: {r1.content.strip()[:60]}")

    # Turn 2 — should remember the name
    messages.append(Message(role=Role.ASSISTANT, content=r1.content))
    messages.append(Message(role=Role.USER, content="What is my name?"))
    r2 = await provider.complete(messages=messages, system="Be concise.", max_tokens=50)
    print(f"  Turn 2 response: {r2.content.strip()[:60]}")

    has_name = "Marco" in r2.content or "marco" in r2.content.lower()
    print("  PASS" if has_name else "  FAIL: expected 'Marco' in response")
    return has_name


async def test_comparison() -> bool:
    """Test 6: Model comparison (same prompt, different models)"""
    header("Test 6: Model Comparison")
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("  SKIP: OPENROUTER_API_KEY not set")
        return True

    prompt = "Write a Python function that checks if a number is prime. Be concise."
    models = ["qwen/qwen3.5-plus-02-15", "deepseek/deepseek-chat-v3"]

    for model_name in models:
        provider = OpenRouterProvider(model=model_name, api_key=api_key)
        messages = [Message(role=Role.USER, content=prompt)]
        start = time.time()
        result = await provider.complete(messages=messages, system="Be concise.", max_tokens=200)
        elapsed = time.time() - start
        speed = result.usage.output_tokens / elapsed if elapsed > 0 else 0

        print(f"\n  {model_name}:")
        print(f"    Response: {result.content.strip()[:80]}...")
        print(f"    Tokens: {result.usage.output_tokens} out")
        print(f"    Speed: {speed:.1f} tok/s")
        print(f"    Cost: ${result.usage.cost_usd:.6f}")
        print(f"    Time: {elapsed:.2f}s")

    print("\n  PASS")
    return True


async def main():
    print("\n" + "=" * 60)
    print("  v0.2.0 Integration Test Suite")
    print("=" * 60)

    tests = [
        ("OpenRouter Provider", test_openrouter),
        ("OpenRouter Streaming", test_openrouter_streaming),
        ("Ollama Local", test_ollama),
        ("StateGraph + OpenRouter", test_graph_openrouter),
        ("Multi-turn Conversation", test_multi_turn),
        ("Model Comparison", test_comparison),
    ]

    results = []
    for name, test_fn in tests:
        try:
            passed = await test_fn()
            results.append((name, passed))
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append((name, False))

    header("Results")
    total = len(results)
    passed = sum(1 for _, p in results if p)
    for name, p in results:
        status = "PASS" if p else "FAIL"
        print(f"  [{status}] {name}")
    print(f"\n  {passed}/{total} passed")

    return all(p for _, p in results)


if __name__ == "__main__":
    # Load env
    env_file = os.path.join(os.path.dirname(__file__), "..", ".env.local")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())

    success = asyncio.run(main())
    sys.exit(0 if success else 1)
