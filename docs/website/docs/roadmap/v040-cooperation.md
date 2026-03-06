---
sidebar_position: 2
title: "v0.4.0: Multi-Agent Cooperation"
---

# v0.4.0 — Multi-Agent Cooperation

Multiple agents working together on a single task.

## Local (Ollama)

| ID | Feature | Files | Detail |
|----|---------|-------|--------|
| COOP-01 | Team-lead delegation | `core/orchestrator.py`, `core/cooperation.py` | Team-lead on qwen2.5-coder decomposes tasks and delegates to sub-agents |
| COOP-02 | Parallel agent execution | `core/orchestrator.py`, `core/agent.py` | Backend + frontend agents run on separate Ollama models simultaneously via `asyncio.gather` |
| COOP-03 | Shared context store | `core/context_store.py` (new) | Agents publish artifacts (code, specs, API contracts) that others can read |
| COOP-04 | Agent-to-agent messages | `core/cooperation.py`, `dashboard/events.py` | Message passing visible in the inter-agent communication panel |
| COOP-05 | Dependency graph | `core/orchestrator.py` | Orchestrator respects ordering (e.g., backend API must complete before frontend uses it) |

## Cloud (OpenRouter)

| ID | Feature | Files | Detail |
|----|---------|-------|--------|
| COOP-06 | Hybrid cooperation | `core/orchestrator.py` | Team-lead on cloud (Qwen 3.5 Plus), sub-agents on local Ollama |
| COOP-07 | Cloud escalation | `core/orchestrator.py`, `core/agent.py` | If local agent stalls (max retries), auto-escalate to cloud model |
| COOP-08 | Cross-provider artifacts | `core/context_store.py` | Local and cloud agents share the same context store |
| COOP-09 | Conflict resolution | `core/cooperation.py` | When 2 agents (local + cloud) modify same file, team-lead resolves |
| COOP-10 | Progress tracking | `dashboard/static/`, `dashboard/events.py` | Real-time progress bar per agent with provider badge (local/cloud) |

## Implementation Notes

**COOP-03 (Shared context store)** is the key dependency — most other features build on it.

```
core/context_store.py:
  class ContextStore:
      async def publish(agent: str, key: str, artifact: Any)
      async def get(key: str) -> Any
      async def list_artifacts() -> list[str]
      async def subscribe(key_pattern: str) -> AsyncIterator[Artifact]
```

**COOP-07 (Cloud escalation)** requires changes to `Agent.execute()`:
- After `max_retries_per_approach` failures, check if an escalation provider is configured
- If yes, swap provider and retry
- Emit `agent.escalated` event for dashboard visibility
