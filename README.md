# Agent Orchestrator

Provider-agnostic AI agent orchestration framework. Swap LLM providers per agent, route tasks by complexity, mix cloud and local models.

## Why

Current agent tools (Claude Code, Cursor, Copilot) lock you into one provider. This framework:

- **Abstracts the provider** — same agent runs on Claude, GPT, Gemini, or local Llama
- **Routes by cost** — simple tasks go to cheap models, complex ones to frontier
- **Mixes cloud + local** — sensitive code stays on your hardware
- **Built-in anti-stall** — retry caps, timeouts, deadlock detection

## Quick Start

```bash
pip install -e ".[all]"
```

### Run the Dashboard (recommended)

The dashboard provides an interactive multi-agent orchestration UI with real-time monitoring:

```bash
# Start Ollama (if not already running)
ollama serve

# Pull a model
ollama pull qwen2.5-coder:7b-instruct

# Start dashboard via OrbStack/Docker
docker compose up dashboard -d

# Open http://localhost:5005
```

### Run Examples Locally

```bash
# With Ollama (free, local)
python3.11 examples/test_ollama_graph.py

# With Anthropic API (requires API key in .env.local)
python3.11 examples/test_claude_graph.py
```

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full design.
See [docs/components.md](docs/components.md) for the component interaction graph.

### Core Abstractions

| Concept | Description |
|---------|-------------|
| **Provider** | LLM backend (Claude, GPT, Gemini, OpenRouter, local). Swappable. |
| **Agent** | Autonomous unit with role, tools, provider. Stateless. |
| **Skill** | Reusable capability. Provider-independent. |
| **Orchestrator** | Coordinates agents, routes tasks, enforces anti-stall. |
| **Cooperation** | Inter-agent delegation, artifact sharing, conflict resolution. |
| **StateGraph** | LangGraph-inspired directed graph engine for orchestration flows. |

### StateGraph Engine

The graph engine is the core of the orchestration system. Inspired by LangGraph but fully provider-agnostic:

```python
from agent_orchestrator.core.graph import END, START, StateGraph
from agent_orchestrator.core.llm_nodes import llm_node
from agent_orchestrator.providers.local import LocalProvider

# Use any Ollama model
provider = LocalProvider(model="qwen2.5-coder:7b-instruct")

# Create nodes
analyze = llm_node(provider=provider, system="Analyze the code.", prompt_key="code", output_key="analysis")
fix = llm_node(provider=provider, system="Fix the code.", prompt_template=lambda s: f"Analysis:\n{s['analysis']}\n\nCode:\n{s['code']}", output_key="fixed")

# Build graph
graph = StateGraph()
graph.add_node("analyze", analyze)
graph.add_node("fix", fix)
graph.add_edge(START, "analyze")
graph.add_edge("analyze", "fix")
graph.add_edge("fix", END)

# Execute
result = await graph.compile().invoke({"code": "def avg(lst): return sum(lst) / len(lst)"})
print(result.state["fixed"])
```

Features:
- **Parallel execution** — independent nodes run via `asyncio.gather`
- **Conditional routing** — route to different nodes based on LLM output
- **Human-in-the-loop** — pause graph execution for user input, resume later
- **Checkpointing** — save/restore graph state (InMemory, SQLite, Postgres)
- **LLM node factories** — `llm_node()`, `multi_provider_node()`, `chat_node()`
- **Reducers** — control how state merges (append, replace, merge_dict, etc.)
- **Sub-graphs** — wrap compiled graphs as callable nodes with I/O mapping
- **Graph patterns** — retry, loop, map-reduce as composable patterns
- **Graph templates** — versioned templates with JSON serialization

### Dashboard

Real-time monitoring UI with three execution modes:

- **Multi-Agent** (default) — team-lead decomposes tasks, backend-dev and frontend-dev execute with tools (file_write, shell_exec), team-lead summarizes
- **Single Agent** — one agent with full tool access
- **Simple Prompt** — direct LLM call with optional streaming

Features:
- **Provider selector** — Cloud (OpenRouter free models) or Local (Ollama)
- **Model selector** — dynamically lists available models per provider
- **Live agent activity** — real-time tool calls, task delegation, completion
- **Agent graph** — visual node status per agent
- **Session-based jobs** — all agent outputs persisted to `jobs/job_<session>/`
- **File context** — attach project files to prompts
- **Markdown rendering** — chat renders code blocks, headers, lists, links
- **WebSocket streaming** — real-time token streaming (Simple Prompt mode)
- **Event log** — filterable timeline of all orchestrator events

### Routing Strategies

- **Fixed** — each agent always uses one provider
- **Cost-optimized** — cheap models for simple tasks, expensive for complex
- **Capability-based** — match task needs to model strengths
- **Fallback chain** — try provider A, fall back to B on failure
- **Local-first** — prefer local models, fallback to cloud
- **Complexity-based** — estimate task complexity, route accordingly

## Providers

| Provider | Class | Models |
|----------|-------|--------|
| Anthropic | `AnthropicProvider` | claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5 |
| OpenAI | `OpenAIProvider` | gpt-4o, gpt-4o-mini, o3 |
| Google | `GoogleProvider` | gemini-2.0-flash, gemini-2.5-pro |
| OpenRouter | `OpenRouterProvider` | Free models from Google, Meta, Qwen, OpenAI, Mistral, NVIDIA |
| Local/Ollama | `LocalProvider` | Any Ollama model (qwen2.5-coder, deepseek-r1, gemma3, etc.) |

## Docker / OrbStack

Everything runs via Docker Compose on OrbStack:

```bash
# Dashboard (interactive orchestrator UI)
docker compose up dashboard -d        # http://localhost:5005

# Run tests
docker compose run --rm test

# Lint
docker compose run --rm lint

# Format check
docker compose run --rm format
```

The dashboard container connects to Ollama on the host via `host.docker.internal:11434`.

### Services

| Service | Port | Description |
|---------|------|-------------|
| `dashboard` | 5005 | Interactive dashboard with prompt |
| `postgres` | 5432 | PostgreSQL for checkpointing |
| `app` | - | Development shell |
| `test` | - | pytest runner |
| `lint` | - | ruff linter |
| `format` | - | ruff format checker |

## Project Structure

```
agent-orchestrator/
├── src/agent_orchestrator/
│   ├── core/
│   │   ├── graph.py              # StateGraph engine (nodes, edges, parallel, HITL)
│   │   ├── llm_nodes.py          # LLM node factories (llm_node, multi_provider, chat)
│   │   ├── checkpoint.py         # InMemory + SQLite checkpointers
│   │   ├── checkpoint_postgres.py # Postgres checkpointer (asyncpg)
│   │   ├── reducers.py           # State reducers (append, merge, replace, etc.)
│   │   ├── graph_patterns.py     # Sub-graphs, retry, loop, map-reduce
│   │   ├── graph_templates.py    # Template store with versioning & JSON
│   │   ├── provider.py           # Provider ABC interface
│   │   ├── agent.py              # Agent base class
│   │   ├── orchestrator.py       # Task decomposition + routing
│   │   ├── cooperation.py        # Inter-agent protocols
│   │   ├── skill.py              # Skill registry
│   │   ├── router.py             # Smart task routing (6 strategies)
│   │   ├── usage.py              # Cost tracking & budget enforcement
│   │   ├── health.py             # Provider health monitoring
│   │   ├── benchmark.py          # Model benchmarking suite
│   │   ├── rate_limiter.py       # Per-provider rate limiting
│   │   ├── audit.py              # Structured audit logging
│   │   ├── task_queue.py         # Priority task queue with retries
│   │   ├── metrics.py            # Prometheus-compatible metrics
│   │   ├── alerts.py             # Spend alert rules & manager
│   │   ├── plugins.py            # Plugin manifest & loader
│   │   ├── webhook.py            # Webhook registry & HMAC validation
│   │   ├── mcp_server.py         # MCP tool/resource registry
│   │   ├── offline.py            # Offline mode (local-only filtering)
│   │   ├── config_manager.py     # Configuration manager (JSON, validation, rollback)
│   │   ├── project.py            # Multi-project support
│   │   ├── users.py              # User management with RBAC
│   │   ├── provider_presets.py   # One-click provider presets
│   │   ├── migration.py          # Import from LangGraph/CrewAI/AutoGen
│   │   └── api.py                # Versioned REST API registry (OpenAPI 3.0)
│   ├── providers/
│   │   ├── anthropic.py          # Claude
│   │   ├── openai.py             # GPT
│   │   ├── google.py             # Gemini
│   │   ├── openrouter.py         # OpenRouter (free cloud models)
│   │   └── local.py              # Ollama / vLLM (OpenAI-compatible)
│   ├── dashboard/
│   │   ├── app.py                # FastAPI app (REST + WebSocket + streaming)
│   │   ├── agent_runner.py       # Agent/team execution with event emissions
│   │   ├── agents_registry.py    # Agent configuration registry
│   │   ├── graphs.py             # Graph builders for dashboard prompt
│   │   ├── job_logger.py         # Session-based job persistence
│   │   ├── auth.py               # API key authentication middleware
│   │   ├── events.py             # EventBus + WebSocket broadcast
│   │   ├── instrument.py         # Auto-instrumentation of core classes
│   │   ├── server.py             # Entrypoint (uvicorn)
│   │   └── static/               # HTML, CSS, JS
│   └── skills/
│       ├── filesystem.py         # File read/write/search
│       ├── shell.py              # Shell command execution
│       ├── doc_sync.py           # Documentation sync checker
│       ├── github_skill.py       # GitHub integration via gh CLI
│       └── webhook_skill.py      # Outgoing webhook skill
├── examples/
│   ├── test_ollama_graph.py      # 4 examples with Ollama/Qwen (free)
│   └── test_claude_graph.py      # 4 examples with Anthropic API
├── tests/                        # 382 tests (13 test files)
├── docker-compose.yml            # OrbStack services
└── pyproject.toml
```

## Documentation

- [Architecture](docs/architecture.md) — core design and abstractions
- [Components](docs/components.md) — component interaction graph
- [Cost Analysis](docs/cost-analysis.md) — provider comparison, cost modeling, break-even analysis
- [Infrastructure](docs/infrastructure.md) — cloud vs physical machines decision framework
- [Migration from Claude](docs/migration-from-claude.md) — how to abstract away from Claude Code

## Development

```bash
pip install -e ".[dev]"
pytest
```

### Pre-commit Hooks (Husky)

Pre-commit hooks run lint, format check, and tests via Docker:

```bash
# Hooks run automatically on git commit
# Manual run:
docker compose run --rm lint
docker compose run --rm format
docker compose run --rm test
```

## Status

v1.0.0 — Full framework: core abstractions, 5 providers (Anthropic, OpenAI, Google, OpenRouter, Local), StateGraph engine, multi-agent dashboard with session persistence, 382 tests.
