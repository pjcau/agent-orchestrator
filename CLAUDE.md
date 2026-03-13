# Agent Orchestrator

## Language

All code, comments, commit messages, documentation, and any written content in this project MUST be in **English**.

## Mandatory: Tests & Documentation

Every code change (new feature, bug fix, refactor) **MUST** include:

1. **Tests** — Add or update tests covering the change. Run `pytest` to verify.
2. **Documentation** — Update relevant docs (CLAUDE.md, README.md, `docs/`, inline comments) to reflect the change.

Do NOT skip these steps. They are required for every modification.

## Overview

Provider-agnostic AI agent orchestration framework. Abstracts the concepts of skill, agent, subagent, and inter-agent cooperation away from any single LLM vendor (Claude, GPT, Gemini, Llama, Mistral, etc.).

## Project Structure

```
agent-orchestrator/
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
│   ├── dashboard/Dockerfile     # Dashboard container (FastAPI + auth)
│   ├── docs/Dockerfile          # Docusaurus docs site
│   ├── archiver/Dockerfile      # Job archiver (S3 upload + PG metadata)
│   ├── nginx/nginx.conf         # Reverse proxy (TLS, rate limiting, WebSocket)
│   ├── aws-cost-exporter/       # Custom Prometheus exporter for AWS billing
│   ├── prometheus/              # prometheus.yml + alerts.yml
│   └── grafana/                 # Provisioning (datasources, dashboards, alerts)
├── scripts/
│   ├── archive_jobs.py          # S3 job archiver (tarball + PG metadata)
│   ├── fetch_github_stars.py    # Fetch starred repos for research scout
│   ├── run_research_scout.py    # LLM analysis of starred repos
│   └── simulate_finance_team.py # Multi-agent finance simulation (OpenRouter)
├── docker-compose.yml           # Dev services (postgres, dashboard, docs)
├── docker-compose.prod.yml      # Production (nginx, redis, prometheus, grafana, archiver)
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
│       │   ├── conformance.py  # Conformance test suites (Provider, Checkpointer, Store)
│       │   ├── store.py        # Cross-thread persistent store (namespace, filter, TTL)
│       │   ├── conversation.py # Thread-based conversation memory (multi-turn, fork, persist)
│       │   └── bookmark_tracker.py # JSON-based bookmark tracking (7-day lookback)
│       ├── providers/
│       │   ├── anthropic.py     # Claude provider
│       │   ├── openai.py        # GPT provider
│       │   ├── google.py        # Gemini provider
│       │   ├── openrouter.py    # OpenRouter (free cloud models)
│       │   └── local.py         # Local models (Ollama, vLLM)
│       ├── dashboard/
│       │   ├── app.py           # FastAPI dashboard (REST + WebSocket + streaming)
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
│       │   ├── server.py        # CLI entrypoint (uvicorn)
│       │   └── static/          # HTML/CSS/JS dashboard UI
│       └── skills/
│           ├── filesystem.py    # File read/write/search
│           ├── shell.py         # Shell command execution
│           ├── doc_sync.py      # Documentation sync checker
│           ├── github_skill.py  # GitHub integration via gh CLI
│           ├── webhook_skill.py # Outgoing webhook skill
│           └── web_reader.py   # Web content fetcher & HTML text extractor
├── tests/
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
- **MCPServerRegistry** — Expose agents/skills as MCP tools and resources.
- **OfflineManager** — Filter to local-only providers when offline.
- **ConfigManager** — Load/save/validate orchestrator configuration with rollback history.
- **ProjectManager** — Multi-project support with archive/unarchive and current project.
- **UserManager** — Multi-user RBAC: admin, developer, viewer roles with API key auth.
- **ProviderPresetManager** — One-click presets: local_only, cloud_only, hybrid, high_quality.
- **MigrationManager** — Import configs from LangGraph, CrewAI, AutoGen with auto-detection.
- **APIRegistry** — Versioned REST API (/api/v1/) with OpenAPI 3.0 spec export.
- **BaseStore** — Cross-thread persistent key-value store (namespace, filter, TTL). Separate from checkpoints.
- **SessionStore** — Session-scoped wrapper on BaseStore. Auto-tracks written keys, deletes all session data on close(). Async context manager.
- **StreamEvent / astream()** — Real-time graph execution streaming. `CompiledGraph.astream()` yields `StreamEvent` at each node start/end/error, with state deltas and timing.
- **SkillMiddleware** — Composable interceptors on skill execution (retry, logging, timeout, cache).
- **LLM Cache** — Shared `InMemoryCache` for LLM node responses. Activated via `cache_policy` param on `llm_node()`. Skips cache when `temperature > 0`. Dashboard shows hits/misses/rate in real time.
- **Tool Cache** — `cache_middleware()` on `SkillRegistry` caches idempotent skills (`file_read`, `glob_search`). Auto-invalidates on `file_write`.
- **ConversationManager** — Thread-based multi-turn memory. Accumulates messages across invocations via checkpointing. Supports fork, clear, max_history trim. Persists to PostgreSQL and survives container restarts. Sessions can be restored from job records via `POST /api/jobs/{session_id}/restore`.

## Agent Error Tracking

Tool and LLM errors from sub-agents are persisted to PostgreSQL (`agent_errors` table) for analysis.

- **Storage**: `usage_db.record_error()` — persists session, agent, tool, error type/message, step, model, provider
- **Classification**: Errors auto-classified as `command_not_found`, `exit_code_error`, `timeout`, `not_allowed`, or generic `tool_error`
- **Hooks**: `agent_runner._instrumented_execute()` logs errors when `result.success == False`
- **API**: `GET /api/errors` — returns recent errors (last 100) and summary grouped by agent/error_type
- **Graceful**: Falls back silently if DB unavailable (no crash, in-memory only)

## Agents (24)

Agents are organised by **category** under `.claude/agents/<category>/`.
The `team-lead` lives at root level (`.claude/agents/team-lead.md`).

```
team-lead (sonnet) ──── orchestrator, coordinates all categories
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

### Skills Map (13 total)

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
| `/ship` | all | Full pipeline: test, lint, docs sync, commit, push |
| `/feature` | all | End-to-end feature dev: implement, user review loop, tests, SOLID review, docs, commit, push |
| `/fix` | all | Bug fix with mandatory regression tests, lint, deploy |

### Research Scout & Nightly Workflow

The `research-scout` analyzes **GitHub starred repos** (one per run) via LLM and
proposes concrete code improvements as PRs. Token-efficient: one repo, one LLM call.

- **Source**: GitHub starred repos (fetched via `scripts/fetch_github_stars.py`)
- **Lookback**: 30 days (stars older than 30 days are ignored)
- **LLM backend**: `claude` CLI locally, OpenRouter (`qwen/qwen3.5-flash-02-23`) on CI
- **Analysis**: LLM compares repo's patterns against our codebase, proposes 1-3 improvements with code
- **State tracking**: `.claude/research-scout-state.json` (tracks processed URLs)
- **Findings file**: `.claude/research-scout-findings.md` (ephemeral, gitignored — used only as PR body, never committed)
- **GitHub Action**: `.github/workflows/nightly-research.yml` (runs at 02:00 UTC)
- **Scripts**: `scripts/fetch_github_stars.py`, `scripts/run_research_scout.py`
- **PR creation**: Handled by the CI workflow (`nightly-research.yml`). When findings exist, the workflow creates a branch `research-scout/YYYY-MM-DD-HHMM`, commits state files, pushes, and opens a PR with findings as body. State is always pushed to main.

GitHub vars/secrets needed: `GITHUB_USERNAME` (repo variable), `OPENROUTER_API_KEY` (secret, for LLM analysis), `GITHUB_TOKEN` (auto-provided).

## Deploy Pipeline (CI/CD)

Automated deploy to EC2 on every push to `main`. Config: `.github/workflows/deploy.yml`.

- **Trigger**: push to main (ignores `docs/`, `*.md`, `terraform/`)
- **Steps**: test → lint → rsync code → inject secrets → build → deploy → health check
- **Secret injection**: all GitHub Secrets are injected into `.env.prod` on EC2 via `_inject()` helper (idempotent upsert)
- **Secrets managed**: `AWS_*`, `OPENROUTER_API_KEY`, `JWT_SECRET_KEY`, `OAUTH_CLIENT_ID/SECRET`, `GRAFANA_SMTP_*`, `POSTGRES_PASSWORD`, `BASE_URL`, `GITHUB_USERNAME`
- **Force-recreate**: only `dashboard` and `aws-cost-exporter` are force-recreated (not postgres/redis/nginx)
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

## Development

```bash
# Setup (once)
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,dashboard]"

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
| Pre-commit (git) | all commits | Lint, format, test, **docs check** |

The **docs check** (`.husky/check-docs.sh`) verifies that CLAUDE.md stays in sync with the actual codebase: modules, docker services, hook scripts, test coverage.

Config: `.claude/settings.json` · Scripts: `.claude/hooks/` · Git hooks: `.husky/`
