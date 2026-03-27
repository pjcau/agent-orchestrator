# Agent Orchestrator

## Language

All code, comments, commit messages, documentation, and any written content in this project MUST be in **English**.

## Mandatory: Tests & Documentation

Every code change (new feature, bug fix, refactor) **MUST** include:

1. **Tests** — Add or update tests covering the change. Run `pytest` to verify.
2. **Documentation** — Update relevant docs (CLAUDE.md, README.md, `docs/`, inline comments) to reflect the change.

Do NOT skip these steps. They are required for every modification.

## Import Boundary (Harness / App)

The codebase is split into two layers to support library distribution:

| Layer | Directories | Purpose |
|-------|-------------|---------|
| **HARNESS** (library) | `core/`, `providers/`, `skills/`, `client.py` | Publishable pip package — no dashboard deps |
| **APP** (application) | `dashboard/`, `integrations/` | FastAPI app, UI, external integrations |

**Rule**: Files in `core/`, `providers/`, `skills/`, `client.py` MUST NEVER import from `dashboard/` or `integrations/`.

This boundary is enforced by `tests/test_import_boundary.py` (AST-based, runs in CI). Use events or dependency injection to communicate from harness to app layer.

Install only the library: `pip install agent-orchestrator[harness]`
Install everything: `pip install agent-orchestrator[all]`

## Overview

Provider-agnostic AI agent orchestration framework. Abstracts the concepts of skill, agent, subagent, and inter-agent cooperation away from any single LLM vendor (Claude, GPT, Gemini, Llama, Mistral, etc.).

## Hybrid Architecture (React + Rust/PyO3)

The project uses a hybrid architecture for optimal performance:

| Layer | Directory | Technology | Purpose |
|-------|-----------|-----------|---------|
| **Frontend** | `frontend/` | React + Vite + TypeScript | Modern reactive UI (replaces vanilla JS in `static/`) |
| **Core Engine** | `rust/` | Rust + PyO3 + maturin | Accelerated graph, router, queue, rate limiter, metrics |
| **Backend** | `src/agent_orchestrator/` | Python (FastAPI) | Agents, providers, skills, orchestration |

### React Frontend (`frontend/`)

- Built with Vite + React 19 + TypeScript
- State management: Zustand
- Data fetching: @tanstack/react-query
- Graph visualization: @xyflow/react
- Charts: Recharts
- Dev: `cd frontend && npm run dev` (proxies API to localhost:5005)
- Build: `cd frontend && npm run build` (outputs to `frontend/dist/`)
- The FastAPI backend serves `frontend/dist/` when present, falls back to `static/`

### Rust Core Engine (`rust/`)

Optional Rust acceleration via PyO3. Falls back to pure Python when not compiled.

- Build: `cd rust && maturin develop --release`
- Install: `pip install agent-orchestrator[rust]`
- Modules ported: `graph_engine`, `router`, `task_queue`, `rate_limiter`, `metrics`
- Each Python module has `try: from _agent_orchestrator_rust import ... except ImportError` fallback
- Docker multi-stage build handles React + Rust + Python automatically

### Import Pattern (Rust Fallback)

```python
try:
    from _agent_orchestrator_rust import RustClassifier
    _HAS_RUST = True
except ImportError:
    _HAS_RUST = False
```

All core modules (`graph.py`, `router.py`, `task_queue.py`, `rate_limiter.py`, `metrics.py`) follow this pattern.

## Project Structure

```
agent-orchestrator/
├── frontend/                    # React + Vite + TypeScript frontend
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── src/
│       ├── api/                 # API client, types, React Query hooks
│       ├── stores/              # Zustand state management
│       ├── hooks/               # WebSocket, SSE hooks
│       ├── components/          # React components (chat, graph, metrics, layout)
│       └── pages/               # Page-level components
├── rust/                        # Rust core engine (PyO3 + maturin)
│   ├── Cargo.toml
│   ├── pyproject.toml           # maturin build config
│   ├── src/                     # Rust source (graph_engine, router, queue, etc.)
│   └── tests/                   # Rust unit tests
├── terraform/
│   ├── backend/main.tf          # S3 + DynamoDB bootstrap (one-time)
│   ├── modules/
│   │   ├── networking/          # VPC, subnet, IGW, security group
│   │   ├── ec2/                 # EC2 instance, EIP, user_data.sh
│   │   ├── iam/                 # IAM role + instance profile
│   │   └── s3/                  # S3 jobs archive bucket (lifecycle → Glacier)
│   ├── main.tf                  # Root module (composes all modules)
│   ├── variables.tf             # Root variables
│   ├── outputs.tf               # Root outputs
│   └── terraform.tfvars.example # Example config (never commit .tfvars)
├── docker/
│   ├── dashboard/Dockerfile     # Multi-stage: Node (React) + Rust (PyO3) + Python (FastAPI)
│   ├── docs/Dockerfile          # Docusaurus docs site
│   ├── archiver/Dockerfile      # Job archiver (S3 upload + PG metadata)
│   ├── nginx/nginx.conf         # Reverse proxy (TLS, rate limiting, WebSocket)
│   ├── aws-cost-exporter/       # Custom Prometheus exporter for AWS billing
│   ├── prometheus/              # prometheus.yml + alerts.yml
│   ├── grafana/                 # Provisioning (datasources, dashboards, alerts)
│   └── tempo/tempo.yaml         # Grafana Tempo trace backend config
├── scripts/
│   ├── archive_jobs.py          # S3 job archiver (tarball + PG metadata)
│   ├── fetch_github_stars.py    # Fetch starred repos for research scout
│   ├── run_research_scout.py    # LLM analysis of starred repos
│   └── simulate_finance_team.py # Multi-agent finance simulation (OpenRouter)
├── docker-compose.yml           # Dev services (postgres, dashboard, docs)
├── docker-compose.prod.yml      # Production (nginx, redis, prometheus, grafana, archiver, tempo)
├── analysis/                     # Deep-dive analyses of external repos (langgraph, deepflow, paperclip)
├── docs/
│   ├── architecture.md          # Core abstractions & patterns
│   ├── cost-analysis.md         # Provider comparison & cost modeling
│   ├── deployment.md            # Production deployment guide (EC2, SSL, CI/CD)
│   ├── infrastructure.md        # Cloud vs on-prem decision framework
│   ├── migration-from-claude.md # How to abstract away from Claude Code
│   └── security.md             # Auth, RBAC, secrets, network, AWS checklist
├── src/
│   └── agent_orchestrator/
│       ├── core/
│       │   ├── provider.py      # LLM provider abstraction (interface)
│       │   ├── agent.py         # Agent base class
│       │   ├── skill.py         # Skill registry & execution (+ SkillSummary for progressive loading)
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
│       │   ├── tracing.py       # OpenTelemetry tracing setup (opt-in, no-op fallback)
│       │   ├── graph.py         # StateGraph engine (nodes, edges, parallel, HITL)
│       │   ├── llm_nodes.py     # LLM node factories (llm_node, multi_provider, chat)
│       │   ├── checkpoint.py    # InMemory + SQLite checkpointers
│       │   ├── checkpoint_postgres.py # Postgres checkpointer (asyncpg)
│       │   ├── reducers.py      # State reducers (append, merge, replace, etc.)
│       │   ├── graph_patterns.py # Sub-graphs, retry, loop, map-reduce
│       │   ├── graph_templates.py # Template store with versioning & JSON
│       │   ├── plugins.py       # Plugin manifest & loader
│       │   ├── webhook.py       # Webhook registry & HMAC validation
│       │   ├── mcp_server.py    # MCP tool/resource registry (server-side: expose agents/skills)
│       │   ├── mcp_client.py    # MCP client (stdio + SSE transports, MCPClientManager, tool injection)
│       │   ├── offline.py       # Offline mode (local-only filtering)
│       │   ├── config_manager.py # Configuration manager (JSON, validation, rollback)
│       │   ├── project.py       # Multi-project support
│       │   ├── users.py         # User management with RBAC
│       │   ├── provider_presets.py # One-click provider presets
│       │   ├── migration.py     # Import from LangGraph/CrewAI/AutoGen
│       │   ├── api.py           # Versioned REST API registry (OpenAPI 3.0)
│       │   ├── channels.py     # Typed channels (LastValue, Topic, Barrier, Ephemeral)
│       │   ├── cache.py        # Task-level result caching (InMemory, TTL, cached_node)
│       │   ├── conformance.py  # Conformance test suites (Provider, Checkpointer, Store)
│       │   ├── store.py        # Cross-thread persistent store (namespace, filter, TTL)
│       │   ├── store_postgres.py # PostgreSQL-backed persistent store (durable, JSONB, TTL)
│       │   ├── conversation.py # Thread-based conversation memory (multi-turn, fork, persist)
│       │   ├── sandbox.py        # Docker sandbox for isolated code execution
│       │   ├── bookmark_tracker.py # JSON-based bookmark tracking (7-day lookback)
│       │   ├── tool_recovery.py    # Dangling tool call detection & placeholder injection
│       │   ├── document_converter.py # File upload & document-to-Markdown conversion
│       │   ├── yaml_config.py     # YAML config loader (reflection, env vars, versioning)
│       │   ├── memory_filter.py   # Session-scoped file path filtering for persistent memory
│       │   ├── loop_detection.py # Loop detection middleware (sliding window, LRU eviction)
│       │   └── clarification.py # Structured clarification system (typed requests, timeout, manager)
│       ├── client.py              # Embedded Python client (no HTTP/server required)
│       ├── providers/
│       │   ├── anthropic.py     # Claude provider
│       │   ├── openai.py        # GPT provider
│       │   ├── google.py        # Gemini provider
│       │   ├── openrouter.py    # OpenRouter (free cloud models)
│       │   └── local.py         # Local models (Ollama, vLLM)
│       ├── dashboard/
│       │   ├── app.py           # Composition root — composes gateway + runtime routers
│       │   ├── gateway_api.py   # Gateway router (REST management: config, jobs, MCP, metrics)
│       │   ├── agent_runtime_router.py # Runtime router (agent/team exec, WebSocket, SSE)
│       │   ├── agent_runner.py  # Agent/team execution with event emissions
│       │   ├── agents_registry.py # Agent configuration registry (category-aware)
│       │   ├── graphs.py        # Graph builders for dashboard prompt
│       │   ├── job_logger.py    # Session-based job persistence (lazy dirs, auto-cleanup)
│       │   ├── auth.py          # OAuth2 + API key authentication middleware
│       │   ├── oauth_routes.py  # GitHub OAuth2 login/callback + admin user API
│       │   ├── user_store.py    # User store (PostgreSQL + JSON fallback)
│       │   ├── events.py        # EventBus, Event types
│       │   ├── instrument.py    # Monkey-patches core classes to emit events
│       │   ├── usage_db.py      # Persistent usage stats + agent error tracking (PostgreSQL + in-memory)
│       │   ├── tracing_metrics.py # Lightweight metrics collector for OTel spans
│       │   ├── alert_webhook.py # Grafana alert → GitHub issue pipeline
│       │   ├── sse.py           # SSE streaming: RunManager, HITLConfig, event formatting
│       │   ├── sandbox_manager.py # Session-scoped sandbox lifecycle (lazy init, LRU eviction)
│       │   ├── server.py        # CLI entrypoint (uvicorn); --mode full|gateway|runtime
│       │   └── static/          # HTML/CSS/JS dashboard UI
│       ├── integrations/
│       │   ├── __init__.py      # Integration exports (SlackBot, TelegramBot)
│       │   ├── slack_bot.py     # Slack bot (Socket Mode, thread mapping, category routing)
│       │   └── telegram_bot.py  # Telegram bot (long-polling, auth, chunking)
│       └── skills/
│           ├── filesystem.py    # File read/write/search
│           ├── shell.py         # Shell command execution
│           ├── doc_sync.py      # Documentation sync checker
│           ├── github_skill.py  # GitHub integration via gh CLI
│           ├── sandboxed_shell.py # Sandboxed shell execution (Docker/local)
│           ├── webhook_skill.py # Outgoing webhook skill
│           ├── web_reader.py   # Web content fetcher & HTML text extractor
│           ├── skill_loader.py # Meta-skill: on-demand full skill instruction loading
│           └── clarification_skill.py # Agent-human clarification skill (blocking/non-blocking)
├── tests/
├── orchestrator.yaml.example    # Example YAML configuration for the orchestrator
├── pyproject.toml
└── README.md
```

## Key Abstractions

- **Provider** — LLM backend (Claude, GPT, Gemini, local). Swappable per agent.
- **Agent** — Autonomous unit with a role, tools, and a provider. Stateless between tasks.
- **Skill** — Reusable capability with middleware chain (retry, logging, timeout). Provider-independent.
- **Orchestrator** — Coordinates agents, task decomposition, anti-stall enforcement.
- **Cooperation** — Inter-agent messaging: delegation, results, conflict resolution.
- **TaskRouter** — Smart routing: 6 strategies (local-first, cost-optimized, complexity-based, etc.). Category-aware: auto-detects task domain (finance, data-science, marketing, software) and selects appropriate agents. Fallback routing uses category-matched agents instead of defaulting to backend+frontend.
- **UsageTracker** — Cost tracking with budget enforcement (per task/session/day).
- **HealthMonitor** — Provider health: latency, error rates, availability, auto-failover.
- **AuditLog** — Structured audit trail: 11 event types, filtering, task traces.
- **MetricsRegistry** — Prometheus-compatible metrics (counters, gauges, histograms).
- **GraphTemplateStore** — Versioned graph templates with JSON serialisation and build_graph().
- **SubGraphNode** — Wrap compiled graphs as callable nodes with I/O mapping.
- **PluginLoader** — Register/load plugin manifests (skills, providers) at runtime.
- **WebhookRegistry** — Inbound webhooks with HMAC-SHA256 signature validation.
- **MCPServerRegistry** — Expose agents/skills as MCP tools and resources. `Orchestrator.register_mcp_tools()` bridges all agents and skills into the registry in one call.
- **MCPClientManager** — Connect to external MCP servers (stdio or SSE transport). Aggregates their tools (prefixed `{server}/{tool}`) and proxies `call_tool`. `SkillRegistry.register_mcp_tools()` injects all discovered tools as local skills.
- **OfflineManager** — Filter to local-only providers when offline.
- **ConfigManager** — Load/save/validate orchestrator configuration with rollback history. Supports YAML import/export via `import_yaml()`/`export_yaml()`.
- **YAMLConfigLoader** — YAML-based configuration with reflection class loading (`module:Class`), `${ENV_VAR}` substitution, config versioning with auto-upgrade, and validation. See `orchestrator.yaml.example`.
- **ProjectManager** — Multi-project support with archive/unarchive and current project.
- **UserManager** — Multi-user RBAC: admin, developer, viewer roles with API key auth.
- **ProviderPresetManager** — One-click presets: local_only, cloud_only, hybrid, high_quality.
- **MigrationManager** — Import configs from LangGraph, CrewAI, AutoGen with auto-detection.
- **APIRegistry** — Versioned REST API (/api/v1/) with OpenAPI 3.0 spec export.
- **BaseStore** — Cross-thread persistent key-value store (namespace, filter, TTL). Separate from checkpoints.
- **PostgresStore** — Durable PostgreSQL backend for BaseStore. JSONB values, lazy TTL expiry, UPSERT semantics, dot-encoded namespaces. Wired into dashboard at startup when `DATABASE_URL` is set; falls back to InMemoryStore.
- **SessionStore** — Session-scoped wrapper on BaseStore. Auto-tracks written keys, deletes all session data on close(). Async context manager.
- **StreamEvent / astream()** — Real-time graph execution streaming. `CompiledGraph.astream()` yields `StreamEvent` at each node start/end/error, with state deltas and timing.
- **SkillMiddleware** — Composable interceptors on skill execution (retry, logging, timeout, cache).
- **Tool Description (`_description`)** — Optional `_description` parameter on every tool call. Extracted before execution (never forwarded to the skill), logged, propagated via `SkillRequest.metadata["tool_description"]`, included in `AuditEntry.tool_description`, and shown in dashboard tool-call events. Injected into `to_tool_definitions()` schemas so LLMs can explain why they invoke a tool.
- **LLM Cache** — Shared `InMemoryCache` for LLM node responses. Activated via `cache_policy` param on `llm_node()`. Skips cache when `temperature > 0`. Dashboard shows hits/misses/rate in real time.
- **Tool Cache** — `cache_middleware()` on `SkillRegistry` caches idempotent skills (`file_read`, `glob_search`). Auto-invalidates on `file_write`.
- **ConversationManager** — Thread-based multi-turn memory. Accumulates messages across invocations via checkpointing. Supports fork, clear, max_history trim. Persists to PostgreSQL and survives container restarts. Sessions can be restored from job records via `POST /api/jobs/{session_id}/restore`. Supports **configurable context summarization** via `SummarizationConfig`: when threads exceed a trigger threshold (message count, token count, or fraction of max_history), older messages are replaced with a single system summary, retaining the last N messages verbatim. Metrics: `conversation_summarization_total` counter, `conversation_tokens_saved` gauge.
- **Tracing** — Optional OpenTelemetry integration. Initialized in `server.py` at startup via `setup_tracing()` + `instrument_fastapi()`. Spans on `Provider.traced_complete()`, `Agent._execute_with_provider()`, graph nodes. `instrument.py` also feeds `tracing_metrics` collectors (LLM durations, node durations, stall counts) which are exported at `/metrics` for Prometheus. Graceful no-op when OTel packages not installed. Exports via OTLP HTTP to Tempo.
- **AlertHandler** — Receives Grafana webhook alerts, collects diagnostics (recent errors, usage, metrics), creates GitHub issues with `gh` CLI. Triggers automated root-cause analysis via `.github/workflows/alert-analysis.yml`.
- **Progressive Skill Loading** — System prompts include only compact `SkillSummary` (name + description + category) instead of full instructions. Agents invoke `load_skill` to fetch detailed instructions on demand, reducing base prompt token usage. `skill_loads_total` counter tracks load frequency.
- **ToolRecovery** — Detects dangling tool calls (assistant messages with `tool_calls` that have no matching `ToolMessage` response) and injects placeholder responses. Called automatically in `Agent.execute()` before each LLM call and in `ConversationManager._load_thread()` when restoring persisted threads.
- **TelegramBot** — Telegram integration using long-polling (no public IP required). Maps Telegram chats to conversation_ids and routes free-text to agents. Commands: `/start`, `/new`, `/status`, `/agents`, `/help`. Auth via `allowed_user_ids`. Install: `pip install agent-orchestrator[telegram]`.
- **OrchestratorClient** — Embedded Python client (`client.py`). Wraps Orchestrator, Agent, SkillRegistry, and StateGraph into a single API. Supports `run_agent()`, `run_team()`, `run_graph()`, `list_agents()`, `list_skills()`, plus sync wrappers. No HTTP server required.
- **SlackBot** — Slack integration via Socket Mode (no public IP). Maps Slack threads to orchestrator conversations (`slack-{channel}-{thread_ts}`). Handles `@bot` mentions, `/agent` and `/team` commands. Auto-detects task category for agent routing. Install: `pip install agent-orchestrator[slack]`.
- **MemoryFilter** — Sanitizes session-scoped file paths (job dirs, tmp files, uploads, workspace) before persisting to conversation memory or cross-thread store. Replaces paths with `[session-file]` placeholder. Messages containing only session-file references are dropped. Integrated with `ConversationManager._save_thread()` and `InMemoryStore.aput()`.
- **LoopDetector** — Per-session sliding window loop detection for agent tool calls. Hashes tool_name+params (MD5), tracks in a `deque(maxlen=20)`. Warns at 3 repeats, hard stops at 5. LRU eviction at 500 sessions. Integrated into `Agent.execute()` via optional `loop_detector` + `session_id` params. Emits `loop.warning` / `loop.hard_stop` events; increments `loop_warnings_total` / `loop_hard_stops_total` counters.
- **DocumentConverter** — Converts uploaded files (PDF, Excel, CSV, Word, PowerPoint, HTML, text) to Markdown for LLM consumption. Graceful fallback when optional deps missing. Limits: 10 MB file size, 50 PDF pages, 10,000 spreadsheet rows. Upload via `POST /api/upload` (multipart/form-data).
- **ClarificationManager** — Structured agent-human clarification. 5 typed request categories (missing_info, ambiguous, approach, risk, suggestion). Blocking mode pauses agent until response or 5-minute timeout. Non-blocking mode emits event and continues. Events: `clarification.request`, `clarification.response`, `clarification.timeout`.
- **Sandbox** — Isolated execution environment (Docker or local). `SandboxConfig` controls image, timeout, memory/CPU limits, network, writable paths, `exposed_ports` (port forwarding), `startup_command`, and `env_vars`. `PortMapping` maps container ports to host ports (auto-assign or explicit). `SandboxInfo` provides runtime introspection (status, mapped ports, uptime). Virtual path mapping with traversal protection. `SandboxedShellSkill` wraps sandbox as a drop-in Skill for agent use.
- **SandboxManager** — Session-scoped sandbox lifecycle in dashboard. Lazy initialization on first use, per-session workspace isolation (`/workspace/{session_id}/`), configurable `max_concurrent` (default 10), LRU eviction, cleanup on shutdown. **Port allocation pool** (default range 9000-9099) prevents host-port collisions between sessions. `get_sandbox_info(session_id)` returns `SandboxInfo` with live container metadata. Enabled via `SANDBOX_ENABLED=true`. Env vars: `SANDBOX_TYPE` (docker/local), `SANDBOX_IMAGE`, `SANDBOX_TIMEOUT`, `SANDBOX_MEMORY`, `SANDBOX_MAX_CONCURRENT`. API: `GET /api/sandbox/status`, `GET /api/sandbox/{session_id}/info` (ports, status, uptime), `GET /api/sandbox/{session_id}/logs` (SSE log streaming), `DELETE /api/sandbox/{session_id}`, `WS /ws/sandbox/{session_id}/terminal` (interactive shell via xterm.js).
- **RunManager (SSE)** — HTTP SSE streaming for graph execution. Creates background runs, fans events to multiple SSE subscribers, supports HITL interrupt/resume with configurable timeout. Max 100 runs, 30-min TTL eviction. Mirrors events to EventBus for WebSocket clients.
- **Modular Dashboard** — `app.py` is a composition root (282 lines) that includes `gateway_api.py` (REST management) and `agent_runtime_router.py` (execution + streaming). Can run as single process or split via `--mode gateway|runtime`. Split mode: `docker-compose.split.yml` + `nginx-split.conf`.

## SSE Streaming Runs

HTTP Server-Sent Events (SSE) for graph execution — an alternative to WebSocket streaming compatible with LangGraph SDK patterns.

- **Module**: `dashboard/sse.py` — `RunManager`, `HITLConfig`, `RunInfo`, SSE formatting helpers
- **Endpoints** (registered in `app.py`):
  - `POST /api/runs` — create and start a graph run; returns `{"run_id": "..."}` immediately
  - `GET /api/runs/{run_id}` — poll run status (`pending/running/interrupted/completed/failed`)
  - `GET /api/runs/{run_id}/stream` — `text/event-stream`; streams `data:` JSON lines in real-time
  - `POST /api/runs/{run_id}/resume` — resume an interrupted (HITL) run with `{"human_input": {...}}`
- **stream_mode**: `"events"` (node-level, default) or `"values"` (full state snapshot per step)
- **RunManager**: max 100 active runs; TTL eviction after 30 min; fans events out to multiple SSE subscribers
- **HITLConfig**: `enabled`, `timeout_seconds` (default 300), `auto_approve` (useful for tests)
- **Reconnection**: `Last-Event-ID` header triggers a reconnect comment; each event carries an `id:` field
- **EventBus integration**: SSE events are also mirrored to the EventBus so WebSocket clients see them
- **Tests**: `tests/test_sse.py` — 44 tests covering lifecycle, formatting, HITL, TTL, stream modes, integration

## Agent Error Tracking

Tool and LLM errors from sub-agents are persisted to PostgreSQL (`agent_errors` table) for analysis.

- **Storage**: `usage_db.record_error()` — persists session, agent, tool, error type/message, step, model, provider
- **Classification**: Errors auto-classified as `command_not_found`, `exit_code_error`, `timeout`, `not_allowed`, or generic `tool_error`
- **Hooks**: `agent_runner._instrumented_execute()` logs errors when `result.success == False`
- **API**: `GET /api/errors` — returns recent errors (last 100) and summary grouped by agent/error_type
- **Graceful**: Falls back silently if DB unavailable (no crash, in-memory only)

## Agent Memory System

Cross-thread long-term memory for agents, backed by PostgreSQL (durable) or InMemoryStore (dev).

- **Store**: `src/agent_orchestrator/core/store_postgres.py` — `PostgresStore(pool)` implements BaseStore on `store_items` table (JSONB values, dot-encoded namespaces, lazy TTL expiry)
- **Wiring**: Dashboard startup creates `PostgresStore` when `DATABASE_URL` is set, `InMemoryStore` otherwise. Accessible as `app.state.store` and via `store_holder[0]`
- **Namespaces**: `("agent", agent_name)` for per-agent memory, `("shared",)` for cross-agent facts
- **Injection**: Before each `run_agent` call, recent memories from both namespaces are prepended to the system prompt as a `<memory>` block (capped at 2000 chars)
- **Persistence**: After a successful agent run, a task summary is stored under `("agent", agent_name)` with a 30-day TTL
- **Summarization**: `ConversationManager` is configured with `SummarizationConfig(threshold=50, retain_last=10)` — triggers at 50 messages, keeps 10 most recent verbatim
- **API**: `GET /api/memory/namespaces`, `GET /api/memory/{namespace}`, `DELETE /api/memory/{namespace}/{key}`, `GET /api/memory/stats`

## Agents (30)

Agents are organised by **category** under `.claude/agents/<category>/`.
Root-level agents live directly in `.claude/agents/`.

### Root-Level Agents (6)

```
.claude/agents/
  ├── team-lead (sonnet) ──────── orchestrator, coordinates all categories
  ├── architect (sonnet) ──────── codebase architecture analysis
  ├── code-reviewer (sonnet) ──── code quality and security review
  ├── dependency-checker (sonnet)  dependency updates, vulnerabilities, unused packages
  ├── migration-helper (sonnet) ── database migrations, API versioning, breaking changes
  └── test-runner (sonnet) ──────── run tests after code changes
```

### Software Engineering (8 agents)

```
.claude/agents/software-engineering/
  ├── backend (sonnet) ──────── API, database, server logic
  ├── frontend (sonnet) ─────── UI, state management, styling
  ├── devops (sonnet) ───────── Docker/OrbStack, CI/CD, infra
  ├── platform-engineer (sonnet) system design, scalability, observability
  ├── ai-engineer (opus) ────── LLM integration, prompt engineering
  ├── scout (opus) ──────────── GitHub pattern discovery
  ├── research-scout (opus) ─── Analyzes starred repos, proposes code improvements
  └── security-auditor (opus) ─ Vulnerability scanning, OWASP, secrets detection
```

#### Cross-Agent Dependencies

```
Backend ↔ Frontend:  API contracts, data models
Backend ↔ Platform:  database, caching, queues
DevOps  ↔ All:       Docker, CI/CD, deployment
AI-Eng  ↔ Backend:   provider implementations, LLM integration
Scout   →  All:       discovers patterns, creates PRs for integration
Security → All:       audits code, deps, config for vulnerabilities
```

### Data Science (5 agents)

```
.claude/agents/data-science/
  ├── data-analyst (sonnet) ──── EDA, statistical testing, visualization
  ├── ml-engineer (opus) ─────── model training, evaluation, MLOps
  ├── data-engineer (sonnet) ─── ETL pipelines, data warehousing, quality
  ├── nlp-specialist (opus) ──── text processing, embeddings, NER, RAG
  └── bi-analyst (sonnet) ────── dashboards, KPI metrics, data storytelling
```

#### Cross-Agent Dependencies

```
Data-Analyst ↔ ML-Engineer:  feature discovery, model validation
Data-Engineer ↔ All:         pipeline outputs feed all analysis
NLP-Specialist ↔ ML-Engineer: text features, embedding models
BI-Analyst ↔ Data-Analyst:   metrics definitions, data sources
```

### Finance (5 agents)

```
.claude/agents/finance/
  ├── financial-analyst (sonnet) ── financial modeling, valuation, forecasting
  ├── risk-analyst (opus) ─────── VaR, stress testing, regulatory compliance
  ├── quant-developer (opus) ──── algorithmic trading, backtesting, signals
  ├── compliance-officer (sonnet)  audit trails, KYC/AML, policy enforcement
  └── accountant (sonnet) ──────── bookkeeping, reconciliation, tax prep
```

#### Cross-Agent Dependencies

```
Financial-Analyst ↔ Risk-Analyst:  valuation inputs, risk metrics
Quant-Developer ↔ Risk-Analyst:   portfolio risk, position limits
Compliance-Officer ↔ All:         regulatory checks on all outputs
Accountant ↔ Financial-Analyst:   financial statements, budgets
```

### Marketing (5 agents)

```
.claude/agents/marketing/
  ├── content-strategist (sonnet) ── content planning, brand voice, SEO copy
  ├── seo-specialist (sonnet) ────── keyword research, technical SEO, links
  ├── growth-hacker (opus) ─────── acquisition funnels, A/B tests, CRO
  ├── social-media-manager (sonnet)  social strategy, community, paid social
  └── email-marketer (sonnet) ────── campaigns, automation, segmentation
```

#### Cross-Agent Dependencies

```
Content-Strategist ↔ SEO-Specialist: keyword-driven content
Growth-Hacker ↔ All:                 experiment design across channels
Social-Media-Manager ↔ Content:      content distribution
Email-Marketer ↔ Growth-Hacker:      funnel automation, nurture flows
```

### Tooling (1 agent)

```
.claude/agents/tooling/
  └── skillkit-scout (opus) ── searches SkillKit marketplace, installs skills
```

#### Escalation Flow

```
Team-lead cannot route task → skillkit-scout searches 15,000+ skills
  → Found: install & assign to appropriate agent
  → Not found: report to user, suggest custom agent/skill
```

### Skills Map (19 total)

| Skill | Agent | Description |
|-------|-------|-------------|
| `/docker-build` | devops | Build and manage containers via OrbStack |
| `/test-runner` | all | Run pytest suite via Docker |
| `/lint-check` | all | Ruff linting and formatting checks |
| `/code-review` | all | Automated quality/security review |
| `/deploy` | devops | Container deployment via docker-compose |
| `/scout` | scout | GitHub pattern discovery |
| `/website-dev` | frontend | Documentation site development |
| `/verify` | all | Pre-PR quality gate (tests, lint, format, security, diff review) |
| `/cost-optimization` | ai-engineer | Review LLM API costs, routing, budget, retry efficiency |
| `/ship` | all | Full pipeline: test, lint, docs sync, commit, push |
| `/feature` | all | End-to-end feature dev: implement, user review loop, tests, SOLID review, docs, commit, push |
| `/fix` | all | Bug fix with mandatory regression tests, lint, deploy |
| `/doc` | all | Full docs review: audit all docs/ against codebase, fix stale/missing/inaccurate content |
| `/fetch-star-repos` | scout | Fetch GitHub starred repos for research scout analysis |
| `/research-scout` | research-scout | Analyze starred repos and propose code improvements |
| `/web-research` | all | Search the internet for solutions, docs, and best practices |
| `/analysis` | all | Deep-dive repo analysis: clone, explore, produce up to 30 MD files in analysis/<name>/ |
| `/epic` | all | Multi-phase epic: break large features into phased stories, execute each via /feature |

### Research Scout & Nightly Workflow

The `research-scout` analyzes **GitHub starred repos** (one per run) via LLM and
proposes concrete code improvements as PRs. Token-efficient: one repo, one LLM call.

- **Source**: GitHub starred repos (fetched via `scripts/fetch_github_stars.py`)
- **Lookback**: 30 days (stars older than 30 days are ignored)
- **LLM backend**: `claude` CLI locally, OpenRouter (`qwen/qwen3.5-flash-02-23`) on CI
- **Analysis**: LLM compares repo's patterns against our codebase, proposes 1-3 improvements with code
- **State tracking**: `.claude/research-scout-state.json` (tracks processed URLs)
- **Findings file**: `.claude/research-scout-findings.md` (ephemeral, gitignored — used only as PR body, never committed)
- **GitHub Actions**: `.github/workflows/nightly-research.yml` (runs at 02:00 UTC), `.github/workflows/alert-analysis.yml` (automated root-cause analysis on alert issues)
- **Scripts**: `scripts/fetch_github_stars.py`, `scripts/run_research_scout.py`
- **PR creation**: Handled by the CI workflow (`nightly-research.yml`). When findings exist, the workflow creates a branch `research-scout/YYYY-MM-DD-HHMM`, commits state files, pushes, and opens a PR with findings as body. State is always pushed to main.

GitHub vars/secrets needed: `GITHUB_USERNAME` (repo variable), `OPENROUTER_API_KEY` (secret, for LLM analysis), `GITHUB_TOKEN` (auto-provided).

## Deploy Pipeline (CI/CD)

Automated deploy to EC2 on every push to `main`. Config: `.github/workflows/deploy.yml`.

- **Trigger**: push to main (ignores `docs/`, `*.md`, `terraform/`)
- **Steps**: test → lint → rsync code → inject secrets → build → deploy → health check
- **Secret injection**: all GitHub Secrets are injected into `.env.prod` on EC2 via `_inject()` helper (idempotent upsert)
- **Secrets managed**: `AWS_*`, `OPENROUTER_API_KEY`, `JWT_SECRET_KEY`, `OAUTH_CLIENT_ID/SECRET`, `GRAFANA_SMTP_*`, `POSTGRES_PASSWORD`, `BASE_URL`, `GITHUB_USERNAME`
- **Force-recreate**: only `dashboard` and `aws-cost-exporter` are force-recreated (not postgres/redis/nginx/tempo)
- **Tempo**: trace backend container (ports 3200 Tempo API, 4318 OTLP HTTP, 7-day retention). Defined in `docker-compose.prod.yml`.
- **OTEL_EXPORTER_OTLP_ENDPOINT**: set on the `dashboard` container (e.g. `http://tempo:4318`) to enable trace export. Omit to disable tracing (graceful no-op).
- **Postgres password sync**: `ALTER USER` runs on every deploy to fix first-init password mismatch
- **Nginx timeout**: 600s (10 min) for long team runs
- **BASE_URL**: `https://agents-orchestrator.com` (domain, not IP — required for OAuth callbacks)
- **Static cache busting**: bump `?v=NNN` in `index.html` on every frontend change

## Security Scanning (CI)

Automated vulnerability scanning runs on every PR and weekly (Monday 06:00 UTC).

| Tool | What it scans | Config |
|------|--------------|--------|
| **Dependabot** | Python, npm, Docker, GitHub Actions deps | `.github/dependabot.yml` |
| **pip-audit** | Python packages for known CVEs | `security-scan.yml` |
| **CodeQL** | Python & JS static analysis (SAST) | `security-scan.yml` |
| **Trivy** | Docker image vulnerabilities | `security-scan.yml` |
| **TruffleHog** | Leaked secrets in git history | `security-scan.yml` |

Dependabot opens PRs automatically for outdated/vulnerable dependencies. Results appear in GitHub's Security tab.

**Security Autofix**: `.github/workflows/security-autofix.yml` runs daily, auto-fixes CodeQL alerts via Claude Code, and opens PRs.

## Alert Pipeline (automated root-cause analysis)

When Grafana alerts fire (severity warning or critical):

1. **Webhook**: Grafana → `POST /api/alerts/webhook` on dashboard
2. **Diagnostics**: `AlertHandler` collects recent errors, error summary, and usage snapshot from PostgreSQL
3. **GitHub Issue**: Creates issue with structured diagnostic report using `gh` CLI (labels: `alert`, `automated`)
4. **Analysis**: `.github/workflows/alert-analysis.yml` triggers on new alert issues, runs LLM analysis via OpenRouter (qwen3-235b), posts root-cause analysis as comment
5. **Triage**: Adds `needs-triage` label for human review

New Prometheus alerts added with this feature: `GraphNodeHung`, `LLMCallSlow`, `FrontendErrorSpike`, `ProviderDegraded`.

## Job Log Archiving

Session logs (`jobs/job_<session_id>/`) are created lazily (only on first file write) and empty dirs are auto-cleaned after 30s. Archived to S3 with metadata in PostgreSQL.

- **Archiver script**: `scripts/archive_jobs.py` — scans for sessions older than N days, tarballs them, uploads to S3, records metadata in `job_archives` table, deletes local files
- **Docker service**: `archiver` in `docker-compose.prod.yml` — runs every 7 days automatically
- **S3 bucket**: `agent-orchestrator-jobs-archive` (Terraform: `terraform/modules/s3/`)
- **Lifecycle**: S3 Standard → Glacier at 90 days → deleted at 365 days
- **IAM**: EC2 role has `s3:PutObject/GetObject/DeleteObject/ListBucket` (Terraform: `terraform/modules/iam/`)
- **Dry run**: `python scripts/archive_jobs.py --dry-run` to preview without uploading

## Container Runtime: OrbStack

Docker containers (Postgres, dashboard, docs) run on **OrbStack**. Tests and linting run locally via Python venv.

- Container startup: **0.2s** (vs 3.2s Docker Desktop) — **16x faster**
- Idle RAM: ~180 MB (vs 2+ GB) — **11x less memory**

## Dashboard

Real-time monitoring UI for the orchestrator. Shows agent interactions, technical metrics, task plan, and graph visualization.

```bash
docker compose up dashboard    # https://localhost:5005
```

### Multi-Category Agent Routing

The dashboard routes tasks to the correct agent category based on keyword detection:

| Category | Agents | Example keywords |
|----------|--------|-----------------|
| **software-engineering** | backend, frontend | code, api, database, docker |
| **finance** | financial-analyst, risk-analyst | stock, portfolio, trading, valuation |
| **data-science** | data-analyst, ml-engineer | dataset, machine learning, regression |
| **marketing** | content-strategist, growth-hacker | seo, campaign, social media, funnel |

Both `agent_runner.py` (team execution) and `graphs.py` (graph composition) use category-aware routing. Falls back to software-engineering if no keywords match.

### Conversation Persistence

Conversation memory persists across restarts and session reloads:

- **PostgresCheckpointer** — used when `DATABASE_URL` is set (production). Falls back to `InMemoryCheckpointer` otherwise.
- **Session restore** — `POST /api/jobs/{session_id}/restore` re-hydrates conversation context from job records when loading a historical session.
- **Frontend integration** — `loadSessionIntoChat()` calls the restore endpoint automatically, preserving `conversation_id` for continuity.

### MCP Integration

The dashboard exposes all agents and skills as MCP (Model Context Protocol) tools, enabling external AI tools to discover and invoke them.

- **Manifest**: `GET /api/mcp/manifest` — full MCP server manifest for client discovery
- **Tool list**: `GET /api/mcp/tools` — all registered tools with input schemas
- **Invoke**: `POST /api/mcp/tools/{name}/invoke` — execute a tool (skill or agent)
- **Orchestrator bridge**: `Orchestrator.register_mcp_tools()` populates an `MCPServerRegistry` from all configured agents and skills
- **UI**: MCP tool count shown in dashboard header

#### MCP Client — connecting to external servers

The dashboard also acts as an MCP **client**, connecting outbound to external MCP servers.

- **List servers**: `GET /api/mcp/servers` — connected external servers with tool counts
- **Add server**: `POST /api/mcp/servers` — connect to a new external server (body: `name`, `transport`, `command`/`url`, `env`, `headers`)
- **Remove server**: `DELETE /api/mcp/servers/{name}` — disconnect and remove a server
- **Read resource**: `GET /api/mcp/resources/{server_name}/{uri}` — fetch resource content from an external server
- **Transports**: `stdio` (subprocess stdin/stdout) and `sse` (Server-Sent Events + HTTP POST)
- **Tool injection**: `SkillRegistry.register_mcp_tools(manager)` registers all external tools as local skills (prefixed `{server}/{tool}`)
- **Implementation**: `core/mcp_client.py` — `MCPClientManager`, `MCPClient`, `StdioTransport`, `SSETransport`

### Session Explorer

Built-in file browser for navigating agent-created artifacts per session. Access via the **Explorer** button in the header.

- **3-pane layout**: Sessions list → File list → File preview with syntax highlighting
- **Syntax highlighting**: via highlight.js (CDN) — supports Python, JS, JSON, Markdown, etc.
- **Download**: individual files or entire session as ZIP archive
- **API endpoints**:
  - `GET /api/jobs/{session_id}/files` — list files in a session
  - `GET /api/jobs/{session_id}/files/{filename}` — read file content
  - `GET /api/jobs/{session_id}/download` — download session as ZIP
- **Security**: path traversal protection, 500KB file size limit

### Session Management

- **Delete sessions**: hover over a session in History → click X → confirm. Files are removed but DB metrics (tokens, cost) are preserved.
- **Lazy directory creation**: session directories are created only when the first file is written, not on session init.
- **Auto-cleanup**: empty session directories are automatically removed after 30 seconds.
- **API**: `DELETE /api/jobs/{session_id}` — cannot delete the current active session.

### Async Team Run

Multi-agent team runs execute as background tasks to prevent HTTP timeouts:

- **Non-blocking**: `POST /api/team/run` returns immediately with `{"job_id", "status": "started"}`
- **Background execution**: `run_team()` runs as `asyncio.Task`, streams events via WebSocket
- **Event lifecycle**: `team.started` → `agent.*` events → `team.complete` (with full result)
- **Graph visualization**: `run_team()` emits `GRAPH_START`/`GRAPH_NODE_ENTER`/`GRAPH_NODE_EXIT`/`GRAPH_END` for 3-phase workflow (plan → sub-agents → review)
- **Polling fallback**: `GET /api/team/status/{job_id}` returns current status and result
- **Memory safety**: completed jobs are evicted (keeps last 20) to prevent unbounded growth

### Usage Metrics

The dashboard header shows two metric groups:

- **Session metrics** (left): tokens, cost, and speed for the current server session
- **Cumulative metrics** (right): all-time totals from PostgreSQL — tokens, cost, avg speed, requests
- **Speed tracking**: `avg_speed` (total average output tok/s from DB), `session_speed` (current server session)
- **DB indicator**: green dot = PostgreSQL connected, metrics persisted; red = in-memory only
- **Debug**: `GET /auth/debug` — shows OAuth config (base_url, redirect_uri, client_id prefix)

### UI Enhancements (DeepFlow-Inspired)

Rich rendering capabilities in the vanilla JS dashboard (no framework, CDN-only):

- **Mermaid.js** — renders ` ```mermaid ` code blocks as SVG diagrams in chat messages (CDN: `mermaid@11`)
- **KaTeX** — renders `$...$` (inline) and `$$...$$` (block) LaTeX math formulas (CDN: `katex@0.16`)
- **Progressive markdown streaming** — buffers streaming chunks and re-renders full markdown on each chunk, fixing broken code blocks and tables mid-stream
- **Reasoning/thinking accordion** — extracts `<thinking>` / `<reasoning>` tags into collapsible `<details>` blocks (auto-collapsed, purple left border)
- **Task Plan panel** — right sidebar section showing real-time graph execution progress (pending/in_progress/completed/failed) with elapsed time per node
- **HITL option buttons** — renders clarification options as clickable pill buttons; interrupt events show Approve/Reject buttons; clicks POST to `/api/runs/{run_id}/resume`
- **SSE toggle** — switch between WebSocket and EventSource for event streaming; indicator dot in header

### Modular Architecture

The dashboard is split into composable router modules for independent scaling:

- **`app.py`** (282 lines) — composition root: middleware, shared state, router composition
- **`gateway_api.py`** — REST management: config, users, jobs, MCP, metrics, memory, sandbox
- **`agent_runtime_router.py`** — execution: `/api/prompt`, `/api/agent/run`, `/api/team/*`, WebSocket, SSE
- **Single process** (default): `python -m agent_orchestrator.dashboard.server` — includes both routers
- **Split process**: `--mode gateway` (port 5006) or `--mode runtime` (port 5007)
- **Docker split**: `docker compose -f docker-compose.prod.yml -f docker-compose.split.yml up`
- **Nginx routing**: `nginx-split.conf` routes `/api/prompt`, `/api/agent/*`, `/api/team/*`, `/ws*` to runtime; everything else to gateway

## Development

```bash
# Setup (once)
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,dashboard]"

# Install with OpenTelemetry support
pip install -e ".[dev,dashboard,otel]"

# Install with Rust acceleration (optional — requires Rust toolchain)
cd rust && maturin develop --release && cd ..

# React frontend development
cd frontend && npm install && npm run dev    # http://localhost:5173 (proxied)
cd frontend && npm run build                 # build to frontend/dist/

# Tests & linting (local venv)
pytest
ruff check src/ tests/
ruff format src/ tests/

# Dashboard (Docker — needs Postgres)
docker compose up dashboard

# Docs site (Docker — Docusaurus)
docker compose up docs          # http://localhost:3000
```

## Hooks (auto-guards)

| Trigger | Matcher | Action |
|---------|---------|--------|
| UserPromptSubmit | (all prompts) | Suggests relevant skills based on keyword matching |
| PreToolUse | `Bash` | Safety guard (prevents dangerous operations) |
| PostToolUse | `Edit` (project source files) | Reminds to run tests |

Config: `.claude/settings.json` · Scripts: `.claude/hooks/`
