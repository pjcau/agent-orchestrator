# Agent Orchestrator

Provider-agnostic AI agent orchestration framework. Abstracts skill, agent, subagent, and inter-agent cooperation away from any single LLM vendor (Claude, GPT, Gemini, Llama, Mistral, etc.).

**For any detail beyond the essentials below, follow the pointers in [Where to look next](#where-to-look-next).**

---

## Non-Negotiable Rules

### Language
All code, comments, commit messages, documentation, and any written content in this project MUST be in **English**.

### Mandatory: Tests & Documentation
Every code change (new feature, bug fix, refactor) **MUST** include:

1. **Tests** — Add or update tests covering the change. Run `pytest` to verify.
2. **Documentation** — Update the relevant doc (`docs/<area>.md`, `README.md`, inline comments). If the change introduces a new abstraction, also update `docs/abstractions.md`.

Do NOT skip these steps. They are required for every modification.

### Import Boundary (Harness / App)
The codebase is split into two layers to support library distribution:

| Layer | Directories | Purpose |
|-------|-------------|---------|
| **HARNESS** (library) | `core/`, `providers/`, `skills/`, `client.py` | Publishable pip package — no dashboard deps |
| **APP** (application) | `dashboard/`, `integrations/` | FastAPI app, UI, external integrations |

**Rule**: Files in `core/`, `providers/`, `skills/`, `client.py` MUST NEVER import from `dashboard/` or `integrations/`.

Enforced by `tests/test_import_boundary.py` (AST-based, runs in CI). Use events or dependency injection to communicate from harness to app layer.

Install only the library: `pip install agent-orchestrator[harness]`
Install everything: `pip install agent-orchestrator[all]`

### Container Runtime: OrbStack Only
Every container/service (app, tests, lint, dashboard, postgres, future services) MUST run on **OrbStack** — never Docker Desktop. Same `docker` / `docker compose` CLI. See [deployment.md § Container Runtime](docs/deployment.md#container-runtime-orbstack).

---

## Project Layout (one screen)

```
agent-orchestrator/
├── frontend/                    # React + Vite + TypeScript (Zustand, react-query, @xyflow/react)
├── rust/                        # Rust core engine (PyO3 + maturin) — optional acceleration
├── terraform/                   # AWS infra (EC2, S3, IAM, networking)
├── docker/                      # Dockerfiles + nginx, prometheus, grafana, tempo configs
├── scripts/                     # archive_jobs, fetch_github_stars, run_research_scout, …
├── analysis/                    # Deep-dive analyses of external repos
├── docs/                        # All long-form documentation (see below)
├── src/agent_orchestrator/
│   ├── core/                    # Harness: provider, agent, skill, orchestrator, graph, store, …
│   ├── providers/               # anthropic, openai, google, openrouter, local
│   ├── skills/                  # filesystem, shell, github_skill, web_reader, …
│   ├── dashboard/               # FastAPI app, SSE, WebSocket, routers, job logger
│   ├── integrations/            # slack_bot, telegram_bot
│   └── client.py                # Embedded Python client (no server needed)
├── tests/
├── orchestrator.yaml.example
└── pyproject.toml
```

Full component-level tree and per-file description: [architecture.md](docs/architecture.md) and [abstractions.md](docs/abstractions.md).

---

## Hybrid Architecture (React + Rust/PyO3 + Python)

| Layer | Directory | Technology |
|-------|-----------|-----------|
| Frontend | `frontend/` | React 19 + Vite + TypeScript |
| Core Engine | `rust/` | Rust + PyO3 + maturin (optional) |
| Backend | `src/agent_orchestrator/` | Python 3.11+ (FastAPI) |

Rust acceleration is opt-in. Every core module has a pure-Python fallback:

```python
try:
    from _agent_orchestrator_rust import RustClassifier
    _HAS_RUST = True
except ImportError:
    _HAS_RUST = False
```

Modules ported: `graph_engine`, `router`, `task_queue`, `rate_limiter`, `metrics`. Docker multi-stage build handles React + Rust + Python automatically.

---

## Development

```bash
# Setup (once)
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,dashboard]"

# Optional: OpenTelemetry + Rust acceleration
pip install -e ".[dev,dashboard,otel]"
cd rust && maturin develop --release && cd ..

# React frontend
cd frontend && npm install && npm run dev    # http://localhost:5173 (proxied)
cd frontend && npm run build                 # builds to frontend/dist/

# Tests & linting (local venv)
pytest
ruff check src/ tests/
ruff format src/ tests/

# Dashboard & docs (Docker — OrbStack)
docker compose up dashboard     # https://localhost:5005
docker compose up docs          # http://localhost:3000
```

---

## Hooks (auto-guards)

| Trigger | Matcher | Action |
|---------|---------|--------|
| UserPromptSubmit | (all prompts) | Suggests relevant skills based on keyword matching |
| PreToolUse | `Bash` | Safety guard (prevents dangerous operations) |
| PostToolUse | `Edit` (project source files) | Reminds to run tests |

Config: `.claude/settings.json` · Scripts: `.claude/hooks/`

---

## Where to look next

Everything detailed lives under `docs/`. Use this map to jump straight to the right file.

### Architecture & Reference
- **Core abstractions, design rationale, mapping from Claude Code** → [docs/architecture.md](docs/architecture.md)
- **Exhaustive catalog of every abstraction (Provider, Agent, Skill, Router, Store, Graph, Sandbox, MCP, prompt registry, middleware, …)** → [docs/abstractions.md](docs/abstractions.md)
- **Docs navigation index** → [docs/README.md](docs/README.md)

### Agents, Skills, Dashboard
- **30 agents by category, cross-dependencies, skills map, research scout workflow** → [docs/agents.md](docs/agents.md)
- **Dashboard UI: routing, conversation persistence, MCP server/client, SSE streaming, async team run, session explorer, memory, metrics, modular architecture** → [docs/dashboard.md](docs/dashboard.md)

### Build, Deploy, Operate
- **Production deployment (EC2, SSL, Nginx), CI/CD pipeline, OrbStack runtime, secrets, troubleshooting** → [docs/deployment.md](docs/deployment.md)
- **Cloud vs on-prem decision framework** → [docs/infrastructure.md](docs/infrastructure.md)
- **Provider cost comparison & routing strategies** → [docs/cost-analysis.md](docs/cost-analysis.md)
- **Migrating existing Claude Code configs** → [docs/migration-from-claude.md](docs/migration-from-claude.md)

### Security, Observability, Monitoring
- **Auth (OAuth2, JWT, API keys), RBAC, secrets, network, sandbox isolation, AWS checklist, CI security scanning** → [docs/security.md](docs/security.md)
- **Alert pipeline (Grafana → GitHub issues → LLM RCA), uptime probes, emergency restart, job log archiving to S3** → [docs/monitoring.md](docs/monitoring.md)
- **Prometheus, Grafana, Tempo, OpenTelemetry setup** → [docs/observability-upgrade.md](docs/observability-upgrade.md)

### Engineering Practices
- **Marker-based prompt injection, PromptRegistry** → [docs/prompt-engineering.md](docs/prompt-engineering.md)
- **LLM cache, tool cache, compaction** → [docs/cache-strategy.md](docs/cache-strategy.md)
- **Frontend React component map** → [docs/components.md](docs/components.md)
- **Roadmap** → [docs/roadmap.md](docs/roadmap.md)
- **Phase 2 (verification gate, atomic task validator)** → [docs/phase2.md](docs/phase2.md)
- **Phase 3 (modality detection)** → [docs/phase3.md](docs/phase3.md)

### Learning Path
- **Dated test logs from `/orchestrator-learning-path-test` with confidence scores** → [docs/learning-path-tests/](docs/learning-path-tests/)

---

## Rule of Thumb for Agents

When in doubt:

1. **Modifying `core/`, `providers/`, `skills/`, `client.py`?** Respect the import boundary. Read [docs/architecture.md](docs/architecture.md) first.
2. **Adding a new abstraction?** Register it in [docs/abstractions.md](docs/abstractions.md) under the right section.
3. **Touching the dashboard?** Read [docs/dashboard.md](docs/dashboard.md) to identify whether it belongs in `gateway_api.py` (REST management) or `agent_runtime_router.py` (execution + streaming).
4. **Adding an agent or skill?** Update [docs/agents.md](docs/agents.md).
5. **Deploy / CI / infra change?** [docs/deployment.md](docs/deployment.md).
6. **Any change?** Add a test and update the relevant doc — both are mandatory.
