# Agent Orchestrator

## Language

All code, comments, commit messages, documentation, and any written content in this project MUST be in **English**.

## Overview

Provider-agnostic AI agent orchestration framework. Abstracts the concepts of skill, agent, subagent, and inter-agent cooperation away from any single LLM vendor (Claude, GPT, Gemini, Llama, Mistral, etc.).

## Project Structure

```
agent-orchestrator/
├── docker/
│   └── app/Dockerfile           # Python dev container (OrbStack)
├── docker-compose.yml           # All services (app, test, lint, format, dashboard, postgres)
├── docs/
│   ├── architecture.md          # Core abstractions & patterns
│   ├── cost-analysis.md         # Provider comparison & cost modeling
│   ├── infrastructure.md        # Cloud vs on-prem decision framework
│   └── migration-from-claude.md # How to abstract away from Claude Code
├── src/
│   └── agent_orchestrator/
│       ├── core/
│       │   ├── provider.py      # LLM provider abstraction (interface)
│       │   ├── agent.py         # Agent base class
│       │   ├── skill.py         # Skill registry & execution
│       │   ├── orchestrator.py  # Main orchestrator (coordination)
│       │   ├── cooperation.py   # Inter-agent communication protocols
│       │   ├── router.py        # Smart task routing (6 strategies)
│       │   ├── usage.py         # Cost tracking & budget enforcement
│       │   ├── health.py        # Provider health monitoring
│       │   ├── benchmark.py     # Model benchmarking suite
│       │   ├── rate_limiter.py  # Per-provider rate limiting
│       │   ├── audit.py         # Structured audit logging
│       │   ├── task_queue.py    # Priority task queue with retries
│       │   ├── metrics.py       # Prometheus-compatible metrics
│       │   ├── alerts.py        # Spend alert rules & manager
│       │   ├── graph.py         # StateGraph engine (nodes, edges, parallel, HITL)
│       │   ├── llm_nodes.py     # LLM node factories (llm_node, multi_provider, chat)
│       │   ├── checkpoint.py    # InMemory + SQLite checkpointers
│       │   ├── checkpoint_postgres.py # Postgres checkpointer (asyncpg)
│       │   ├── reducers.py      # State reducers (append, merge, replace, etc.)
│       │   ├── graph_patterns.py # Sub-graphs, retry, loop, map-reduce
│       │   ├── graph_templates.py # Template store with versioning & JSON
│       │   ├── plugins.py       # Plugin manifest & loader
│       │   ├── webhook.py       # Webhook registry & HMAC validation
│       │   ├── mcp_server.py    # MCP tool/resource registry
│       │   ├── offline.py       # Offline mode (local-only filtering)
│       │   ├── config_manager.py # Configuration manager (JSON, validation, rollback)
│       │   ├── project.py       # Multi-project support
│       │   ├── users.py         # User management with RBAC
│       │   ├── provider_presets.py # One-click provider presets
│       │   ├── migration.py     # Import from LangGraph/CrewAI/AutoGen
│       │   ├── api.py           # Versioned REST API registry (OpenAPI 3.0)
│       │   ├── channels.py     # Typed channels (LastValue, Topic, Barrier, Ephemeral)
│       │   ├── cache.py        # Task-level result caching (InMemory, TTL, cached_node)
│       │   └── conformance.py  # Conformance test suites (Provider, Checkpointer)
│       ├── providers/
│       │   ├── anthropic.py     # Claude provider
│       │   ├── openai.py        # GPT provider
│       │   ├── google.py        # Gemini provider
│       │   ├── openrouter.py    # OpenRouter (free cloud models)
│       │   └── local.py         # Local models (Ollama, vLLM)
│       ├── dashboard/
│       │   ├── app.py           # FastAPI dashboard (REST + WebSocket + streaming)
│       │   ├── agent_runner.py  # Agent/team execution with event emissions
│       │   ├── agents_registry.py # Agent configuration registry
│       │   ├── graphs.py        # Graph builders for dashboard prompt
│       │   ├── job_logger.py    # Session-based job persistence
│       │   ├── auth.py          # API key authentication middleware
│       │   ├── events.py        # EventBus, Event types
│       │   ├── instrument.py    # Monkey-patches core classes to emit events
│       │   ├── server.py        # CLI entrypoint (uvicorn)
│       │   └── static/          # HTML/CSS/JS dashboard UI
│       └── skills/
│           ├── filesystem.py    # File read/write/search
│           ├── shell.py         # Shell command execution
│           ├── doc_sync.py      # Documentation sync checker
│           ├── github_skill.py  # GitHub integration via gh CLI
│           └── webhook_skill.py # Outgoing webhook skill
├── tests/
├── pyproject.toml
└── README.md
```

## Key Abstractions

- **Provider** — LLM backend (Claude, GPT, Gemini, local). Swappable per agent.
- **Agent** — Autonomous unit with a role, tools, and a provider. Stateless between tasks.
- **Skill** — Reusable capability (test-runner, linter, deployer). Provider-independent.
- **Orchestrator** — Coordinates agents, task decomposition, anti-stall enforcement.
- **Cooperation** — Inter-agent messaging: delegation, results, conflict resolution.
- **TaskRouter** — Smart routing: 6 strategies (local-first, cost-optimized, complexity-based, etc.).
- **UsageTracker** — Cost tracking with budget enforcement (per task/session/day).
- **HealthMonitor** — Provider health: latency, error rates, availability, auto-failover.
- **AuditLog** — Structured audit trail: 11 event types, filtering, task traces.
- **MetricsRegistry** — Prometheus-compatible metrics (counters, gauges, histograms).
- **GraphTemplateStore** — Versioned graph templates with JSON serialisation and build_graph().
- **SubGraphNode** — Wrap compiled graphs as callable nodes with I/O mapping.
- **PluginLoader** — Register/load plugin manifests (skills, providers) at runtime.
- **WebhookRegistry** — Inbound webhooks with HMAC-SHA256 signature validation.
- **MCPServerRegistry** — Expose agents/skills as MCP tools and resources.
- **OfflineManager** — Filter to local-only providers when offline.
- **ConfigManager** — Load/save/validate orchestrator configuration with rollback history.
- **ProjectManager** — Multi-project support with archive/unarchive and current project.
- **UserManager** — Multi-user RBAC: admin, developer, viewer roles with API key auth.
- **ProviderPresetManager** — One-click presets: local_only, cloud_only, hybrid, high_quality.
- **MigrationManager** — Import configs from LangGraph, CrewAI, AutoGen with auto-detection.
- **APIRegistry** — Versioned REST API (/api/v1/) with OpenAPI 3.0 spec export.

## Agents (7)

```
team-lead (sonnet) ──── orchestrator, 0 skills
  ├── backend (sonnet) ──────── API, database, server logic
  ├── frontend (sonnet) ─────── UI, state management, styling
  ├── devops (sonnet) ───────── Docker/OrbStack, CI/CD, infra
  ├── platform-engineer (sonnet) system design, scalability, observability
  └── ai-engineer (opus) ────── LLM integration, prompt engineering

scout (opus) ── /scout (GitHub pattern discovery, periodic runs)
```

### Cross-Agent Dependencies

```
Backend ↔ Frontend:  API contracts, data models
Backend ↔ Platform:  database, caching, queues
DevOps  ↔ All:       Docker, CI/CD, deployment
AI-Eng  ↔ Backend:   provider implementations, LLM integration
Scout   →  All:       discovers patterns, creates PRs for integration
```

### Skills Map (10 total)

| Skill | Agent | Description |
|-------|-------|-------------|
| `/docker-build` | devops | Build and manage containers via OrbStack |
| `/test-runner` | all | Run pytest suite via Docker |
| `/lint-check` | all | Ruff linting and formatting checks |
| `/code-review` | all | Automated quality/security review |
| `/deploy` | devops | Container deployment via docker-compose |
| `/scout` | scout | GitHub pattern discovery |
| `/website-dev` | frontend | Documentation site development |
| `/doc-sync` | all | Sync docs with code (CLAUDE.md, README, docs/, website) |
| `/verify` | all | Pre-PR quality gate (tests, lint, format, security, diff review) |
| `/cost-optimization` | ai-engineer | Review LLM API costs, routing, budget, retry efficiency |

## Container Runtime: OrbStack

All containers run on **OrbStack** (not Docker Desktop). Same `docker` / `docker-compose` commands, zero code changes.

- Container startup: **0.2s** (vs 3.2s Docker Desktop) — **16x faster**
- Idle RAM: ~180 MB (vs 2+ GB) — **11x less memory**

## Dashboard

Real-time monitoring UI for the orchestrator. Shows agent interactions, technical metrics, task plan, and graph visualization.

```bash
docker compose up dashboard    # http://localhost:5005
```

## Development

```bash
# Via Docker (preferred — runs on OrbStack)
docker compose run --rm test
docker compose run --rm lint

# Dashboard
docker compose up dashboard

# Local (if deps installed)
pip install -e ".[dev,dashboard]"
pytest
```

## Hooks (auto-guards)

| Trigger | Matcher | Action |
|---------|---------|--------|
| UserPromptSubmit | (all prompts) | Suggests relevant skills based on keyword matching |
| PreToolUse | `Bash` | Safety guard (prevents dangerous operations) |
| PostToolUse | `Edit` (project source files) | Reminds to run tests |
| Pre-commit (git) | all commits | Lint, format, test, **docs check** |

The **docs check** (`.husky/check-docs.sh`) verifies that CLAUDE.md stays in sync with the actual codebase: modules, docker services, hook scripts, test coverage.

Config: `.claude/settings.json` · Scripts: `.claude/hooks/` · Git hooks: `.husky/`
