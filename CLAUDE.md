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
│       │   └── cooperation.py   # Inter-agent communication protocols
│       ├── providers/
│       │   ├── anthropic.py     # Claude provider
│       │   ├── openai.py        # GPT provider
│       │   ├── google.py        # Gemini provider
│       │   └── local.py         # Local models (Ollama, vLLM)
│       ├── dashboard/
│       │   ├── app.py           # FastAPI dashboard (REST + WebSocket)
│       │   ├── events.py        # EventBus, Event types
│       │   ├── instrument.py    # Monkey-patches core classes to emit events
│       │   ├── server.py        # CLI entrypoint (uvicorn)
│       │   └── static/          # HTML/CSS/JS dashboard UI
│       └── skills/
│           ├── doc_sync.py      # Documentation sync checker
│           ├── filesystem.py    # File read/write/search
│           ├── shell.py         # Shell command execution
│           └── testing.py       # Test runner skill
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

### Skills Map (8 total)

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
