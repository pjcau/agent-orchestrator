#!/usr/bin/env python3
"""Simulate multi-agent finance team interaction via OpenRouter + Qwen 3.5 Flash.

Demonstrates that the orchestrator correctly routes finance topics to
financial-analyst and risk-analyst agents (not backend+frontend).

Usage:
    export OPENROUTER_API_KEY=sk-or-...
    python scripts/simulate_finance_team.py

    # Dry-run (no API call, shows routing only):
    python scripts/simulate_finance_team.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent_orchestrator.dashboard.agent_runner import (
    _build_agent_catalog,
    _detect_category,
    _parse_team_plan,
    run_team,
)
from src.agent_orchestrator.dashboard.agents_registry import get_agent_registry
from src.agent_orchestrator.dashboard.events import Event, EventBus, EventType
from src.agent_orchestrator.dashboard.graphs import _detect_graph_category
from src.agent_orchestrator.providers.openrouter import OpenRouterProvider

# ── Finance test prompts ──────────────────────────────────────────────────────

FINANCE_PROMPTS = [
    (
        "Portfolio Risk Assessment",
        "Analyze a diversified equity portfolio with 60% US large-cap, 25% international "
        "developed markets, and 15% emerging markets. Calculate the expected Value at Risk "
        "(VaR) at 95% confidence level, suggest hedging strategies, and assess the impact "
        "of a 20% drawdown in emerging markets on the overall portfolio.",
    ),
    (
        "DCF Valuation",
        "Perform a DCF valuation for a SaaS company with $50M ARR growing at 35% YoY, "
        "80% gross margins, and -10% net margins. The company expects to reach profitability "
        "in 2 years. Use a WACC of 12% and a terminal growth rate of 3%. Include sensitivity "
        "analysis on growth rate and discount rate.",
    ),
    (
        "Market Stress Test",
        "Design a stress test scenario for a bank's loan portfolio considering: "
        "1) Interest rates rising 300bps in 6 months, 2) Unemployment reaching 8%, "
        "3) Commercial real estate values dropping 25%. Estimate expected credit losses "
        "and capital adequacy impact under Basel III requirements.",
    ),
]

# Non-finance control prompts (should NOT route to finance agents)
CONTROL_PROMPTS = [
    ("Software Task", "Build a REST API with FastAPI that manages user authentication with JWT tokens"),
    ("Data Science Task", "Train a random forest classifier on the Iris dataset and evaluate with cross-validation"),
    ("Marketing Task", "Create an SEO content strategy for a B2B SaaS product launch"),
]


def print_header(title: str) -> None:
    """Print a formatted section header."""
    width = 72
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}")


def print_subheader(title: str) -> None:
    """Print a formatted sub-section header."""
    print(f"\n--- {title} ---")


async def run_event_logger() -> list[dict]:
    """Collect events from the EventBus for display."""
    events: list[dict] = []
    bus = EventBus.get()

    original_emit = bus.emit

    async def capturing_emit(event: Event) -> None:
        events.append({
            "type": event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type),
            "agent": getattr(event, "agent_name", None),
            "data": event.data,
            "ts": time.time(),
        })
        await original_emit(event)

    bus.emit = capturing_emit  # type: ignore[assignment]
    return events


def test_category_detection() -> None:
    """Test that category detection works correctly for all prompt types."""
    print_header("PHASE 1: Category Detection (No API calls)")

    all_prompts = FINANCE_PROMPTS + CONTROL_PROMPTS
    for label, prompt in all_prompts:
        # Test agent_runner detection
        runner_cat = _detect_category(prompt)
        # Test graphs detection
        graph_cat = _detect_graph_category(prompt)

        status = "OK" if runner_cat == graph_cat else "MISMATCH"
        print(f"  [{status}] {label:25s} -> runner={runner_cat:20s} graph={graph_cat}")

    print()


def test_registry_has_finance_agents() -> None:
    """Verify finance agents are in the registry."""
    print_subheader("Agent Registry Check")

    registry = get_agent_registry()
    categories = registry.get("categories", {})
    finance_agents = categories.get("finance", [])

    print(f"  Total agents: {len(registry.get('agents', []))}")
    print(f"  Categories: {list(categories.keys())}")
    print(f"  Finance agents ({len(finance_agents)}):")
    for agent in finance_agents:
        print(f"    - {agent['name']:25s} model={agent.get('model', '?'):8s} desc={agent.get('description', '')[:50]}")

    # Show the catalog team-lead would see
    catalog = _build_agent_catalog(registry)
    finance_lines = [line for line in catalog.split("\n") if "finance" in line.lower()]
    print(f"\n  Finance agents in team-lead catalog ({len(finance_lines)}):")
    for line in finance_lines:
        print(f"    {line.strip()}")


async def simulate_full_team(prompt_label: str, prompt: str, api_key: str) -> dict:
    """Run the full multi-agent team on a finance prompt via OpenRouter."""
    print_subheader(f"Running: {prompt_label}")
    print(f"  Prompt: {prompt[:100]}...")

    provider = OpenRouterProvider(
        api_key=api_key,
        model="qwen/qwen3.5-flash-02-23",
    )

    start = time.time()
    result = await run_team(
        task_description=prompt,
        provider=provider,
        max_steps=5,
        max_sub_agents=3,
    )
    elapsed = time.time() - start

    # Display results
    agents_selected = result.get("agents_selected", [])
    used_fallback = result.get("used_fallback", False)
    total_tokens = result.get("total_tokens", 0)
    total_cost = result.get("total_cost_usd", 0.0)

    print(f"\n  Agents selected: {agents_selected}")
    print(f"  Used fallback:   {used_fallback}")
    print(f"  Total tokens:    {total_tokens:,}")
    print(f"  Total cost:      ${total_cost:.4f}")
    print(f"  Elapsed:         {elapsed:.1f}s")

    # Show agent costs breakdown
    agent_costs = result.get("agent_costs", {})
    if agent_costs:
        print(f"\n  Cost breakdown:")
        for agent_name, costs in agent_costs.items():
            print(f"    {agent_name:30s} tokens={costs.get('tokens', 0):>6,}  cost=${costs.get('cost_usd', 0):.4f}")

    # Show fallback logs if any
    fallback_logs = result.get("fallback_log", [])
    if fallback_logs:
        print(f"\n  Fallback events ({len(fallback_logs)}):")
        for fb in fallback_logs[:5]:
            print(f"    agent={fb.get('agent', '?'):20s} model={fb.get('model', '?'):30s} reason={fb.get('reason', '?')[:50]}")

    # Show plan from team-lead
    plan = result.get("plan", "")
    if plan:
        print(f"\n  Team-lead plan (first 300 chars):")
        print(f"    {plan[:300]}")

    # Show summary
    summary = result.get("output", "")
    if summary:
        print(f"\n  Final summary (first 500 chars):")
        for line in summary[:500].split("\n"):
            print(f"    {line}")

    # Show individual agent outputs
    agent_outputs = result.get("agent_outputs", {})
    for agent_name, output in agent_outputs.items():
        print(f"\n  [{agent_name}] output (first 300 chars):")
        for line in output[:300].split("\n"):
            print(f"    {line}")

    # Validation: check if correct agents were selected
    has_finance_agent = any(
        a in agents_selected
        for a in ["financial-analyst", "risk-analyst", "quant-developer", "compliance-officer", "accountant"]
    )
    has_sw_agent = any(
        a in agents_selected
        for a in ["backend", "frontend", "devops", "platform-engineer"]
    )

    if has_finance_agent and not has_sw_agent:
        print(f"\n  RESULT: CORRECT - Finance agents selected for finance task")
    elif has_finance_agent and has_sw_agent:
        print(f"\n  RESULT: PARTIAL - Mix of finance and software agents")
    else:
        print(f"\n  RESULT: WRONG - No finance agents selected! Got: {agents_selected}")

    return result


async def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate finance multi-agent team")
    parser.add_argument("--dry-run", action="store_true", help="Only test routing, no API calls")
    parser.add_argument("--prompt", type=int, default=0, help="Which finance prompt to use (0-2)")
    parser.add_argument("--all", action="store_true", help="Run all finance prompts")
    args = parser.parse_args()

    print_header("Finance Multi-Agent Team Simulation")
    print("  Provider: OpenRouter")
    print("  Model:    qwen/qwen3.5-flash-02-23")
    print(f"  Mode:     {'dry-run' if args.dry_run else 'live'}")

    # Phase 1: Category detection (always runs, no API needed)
    test_category_detection()
    test_registry_has_finance_agents()

    if args.dry_run:
        print_header("DRY RUN COMPLETE")
        print("  Category detection and registry verified.")
        print("  Set OPENROUTER_API_KEY and remove --dry-run to test with real LLM calls.")
        return

    # Phase 2: Live simulation with OpenRouter
    api_key = os.environ.get("OPENROUTER_API_KEY", "")

    # Try loading from .env files if not in environment
    if not api_key:
        for env_file in [".env", ".env.prod", ".env.local"]:
            env_path = os.path.join(os.path.dirname(__file__), "..", env_file)
            if os.path.isfile(env_path):
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("OPENROUTER_API_KEY="):
                            api_key = line.split("=", 1)[1].strip().strip("\"'")
                            break
                if api_key:
                    break

    if not api_key:
        print("\n  ERROR: OPENROUTER_API_KEY not set. Use --dry-run or export the key.")
        sys.exit(1)

    print_header("PHASE 2: Live Multi-Agent Simulation")

    if args.all:
        prompts_to_run = FINANCE_PROMPTS
    else:
        idx = min(args.prompt, len(FINANCE_PROMPTS) - 1)
        prompts_to_run = [FINANCE_PROMPTS[idx]]

    results = []
    for label, prompt in prompts_to_run:
        result = await simulate_full_team(label, prompt, api_key)
        results.append((label, result))

    # Summary
    print_header("SIMULATION SUMMARY")
    for label, result in results:
        agents = result.get("agents_selected", [])
        tokens = result.get("total_tokens", 0)
        cost = result.get("total_cost_usd", 0.0)
        fallback = result.get("used_fallback", False)
        print(f"  {label:30s} agents={agents}  tokens={tokens:>6,}  cost=${cost:.4f}  fallback={fallback}")

    total_cost_all = sum(r.get("total_cost_usd", 0.0) for _, r in results)
    total_tokens_all = sum(r.get("total_tokens", 0) for _, r in results)
    print(f"\n  Total: {total_tokens_all:,} tokens, ${total_cost_all:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
