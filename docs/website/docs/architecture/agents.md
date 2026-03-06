---
sidebar_position: 3
title: Agents
---

# Agents

An agent is a stateless unit that receives a task, uses tools, and returns a result.

```python
@dataclass
class AgentConfig:
    name: str
    role: str                          # system prompt / persona
    provider: str                      # provider key
    tools: list[str]                   # allowed tool names
    max_steps: int = 10                # anti-stall: hard step limit
    max_retries_per_approach: int = 3  # anti-stall: retry cap
```

Agents are **provider-parameterized** — the same agent definition can run on Claude, GPT, or a local model by swapping the provider.

## Agent Team

```
team-lead (sonnet) ──── orchestrator, 0 skills
  ├── backend (sonnet) ──────── API, database, server logic
  ├── frontend (sonnet) ─────── UI, state management, styling
  ├── devops (sonnet) ───────── Docker/OrbStack, CI/CD, infra
  ├── platform-engineer (sonnet) system design, scalability
  └── ai-engineer (opus) ────── LLM integration, prompts

scout (opus) ── GitHub pattern discovery
```

## Cross-Agent Dependencies

```
Backend  ↔ Frontend:  API contracts, data models
Backend  ↔ Platform:  database, caching, queues
DevOps   ↔ All:       Docker, CI/CD, deployment
AI-Eng   ↔ Backend:   provider implementations
Scout    →  All:       discovers patterns, creates PRs
```
