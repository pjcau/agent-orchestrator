# 14 - Learnings & Actionable Takeaways

## Overview
Concrete improvements we can adopt from llm-use, and anti-patterns to avoid.

## Adopt: Learned Router

**What**: Store past routing decisions and use similarity matching to improve future routing.

**How to implement in our project**:
1. Add a `routing_history` table to our PostgreSQL schema
2. After each `TaskRouter` decision, record `(task_text, strategy_used, success_metrics)`
3. Before routing, check for similar past tasks using TF-IDF or embedding similarity
4. Weight learned routing alongside existing strategy scores
5. Add `router-history` endpoint to dashboard for visibility

**Effort**: Medium (2-3 days)
**Impact**: Medium -- auto-improving routing without manual rule tuning

## Adopt: Worker-Level Web Grounding

**What**: Allow agents to request and incorporate web content mid-execution.

**How to implement**:
1. Enhance `web_reader.py` skill with a "grounding" mode
2. When an agent's response contains URLs, automatically fetch and inject content
3. Agent makes a follow-up call with grounded context
4. Cache scraped content with TTL in our `BaseStore`

**Effort**: Low (1 day -- we already have `web_reader.py`)
**Impact**: Medium -- improves factual accuracy for research tasks

## Adopt: Robust JSON Extraction

**What**: Multi-strategy JSON parsing that handles real-world LLM output (code fences, embedded JSON, brace balancing).

**How to implement**:
1. Create a `json_extract.py` utility in `core/`
2. Port the `parse_orchestrator_json()` function
3. Use it in `llm_nodes.py` and anywhere we parse LLM JSON output
4. Add tests for edge cases (nested objects, escaped strings, mixed text)

**Effort**: Low (half day)
**Impact**: High -- reduces parsing failures across all LLM interactions

## Consider: CLI Frontend

**What**: A CLI interface for our orchestrator that doesn't require the dashboard/server.

**How to implement**:
1. Extend `client.py` with argparse-based CLI
2. Support `exec`, `chat`, `stats` commands
3. Use the existing `OrchestratorClient` internally

**Effort**: Medium (2 days)
**Impact**: Low-medium -- useful for scripting and CI integration

## Avoid: Single-File Monolith

**Anti-pattern**: Everything in one file seems simple but leads to:
- Duplicated code (they have 3 copies of orchestrator init)
- Untestable components (can't mock individual providers)
- Merge conflicts in team settings

**Our approach is correct**: Modular structure with clear boundaries.

## Avoid: Silent Error Swallowing

**Anti-pattern**: `try/except: pass` everywhere.

**Validation**: Our structured error handling with `AuditLog` and `agent_errors` table is the right approach. Every error should be visible somewhere.

## Avoid: No Cache Eviction

**Anti-pattern**: SQLite cache with no TTL or size limits.

**Validation**: Our `InMemoryCache` with TTL and `BaseStore` with namespace/TTL are correctly designed. Always plan for cache growth.

## Summary of Actionable Items

| # | Action | Effort | Impact | Priority |
|---|--------|--------|--------|----------|
| 1 | Learned router for TaskRouter | Medium | Medium | P2 |
| 2 | Worker-level web grounding | Low | Medium | P2 |
| 3 | Robust JSON extraction utility | Low | High | P1 |
| 4 | CLI frontend for OrchestratorClient | Medium | Low | P3 |

## Key Insight
llm-use is a good example of how far you can get with a simple design. It validates the core planner-workers-synthesis pattern that our StateGraph also implements. But its limitations (no async, no auth, no tests, no streaming) show exactly why a more structured approach like ours is necessary for production use.

## Relevance to Our Project
The highest-value takeaway is the robust JSON extraction (#3) -- it solves a real problem we face when parsing LLM outputs. The learned router (#1) is the most interesting conceptual contribution.
