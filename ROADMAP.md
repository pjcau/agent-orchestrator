# Roadmap — Agent Orchestrator

## Completed (v0.1.0 — Foundation)

- [x] Core abstractions: Provider, Agent, Skill, Orchestrator, Cooperation
- [x] 4 LLM providers: Anthropic, OpenAI, Google, Local/Ollama
- [x] StateGraph engine (LangGraph-inspired, provider-agnostic)
  - [x] Parallel node execution
  - [x] Conditional routing
  - [x] Human-in-the-loop (interrupt/resume)
  - [x] LLM node factories (llm_node, multi_provider_node, chat_node)
  - [x] State reducers (append, replace, merge_dict)
- [x] Checkpointing: InMemory, SQLite, PostgreSQL
- [x] Dashboard: real-time monitoring UI with WebSocket
  - [x] Chat-style interaction (prompt + response in same area)
  - [x] Agent tree with sub-agents and skills (glow on activation)
  - [x] Interactive graph visualization (clickable nodes -> details)
  - [x] Inter-agent communication display
  - [x] Ollama model selector (dynamic)
  - [x] 6 graph types: Auto, Chat, Review, Chain, Parallel, Team
  - [x] Token speed metric (tok/s)
- [x] Docker/OrbStack: dashboard, postgres, test, lint, format services
- [x] Husky pre-commit hooks (lint, format, tests)
- [x] 7 agent definitions + 8 skills (incl. web-research)
- [x] 83 tests passing
- [x] Documentation: architecture, cost analysis, infrastructure, migration guide

---

## Completed (v0.2.0 — Local LLM Automation)

Reach a solid level with local Ollama models to automate small daily tasks.

- [x] **Streaming responses**: stream LLM output token-by-token to the chat UI via WebSocket
- [x] **Multi-turn chat**: conversation context across messages (last 3 exchanges)
- [x] **Task presets**: 6 one-click presets (Explain, Review, Tests, Refactor, Docs, Fix)
- [x] **File context**: attach project files to prompts via file picker modal
- [x] **Model comparison**: run same prompt on 2 models side-by-side, compare quality/speed/cost
- [x] **Auto-select model**: regex-based task classification → model routing (coding→coder, reasoning→deepseek)
- [x] **Ollama model management**: pull/delete models from the dashboard UI
- [ ] **Conversation memory**: persist chat history across sessions (in-memory only for now)
- [ ] **Code execution**: run generated code snippets directly from the dashboard (sandboxed)
- [ ] **Prompt templates**: save reusable prompt templates for repetitive tasks

---

## Completed (v0.2.5 — OpenRouter Cloud Provider)

Cloud LLM access via OpenRouter for models not available locally.

- [x] **OpenRouter provider**: new provider extending OpenAI-compatible API at `openrouter.ai/api/v1`
- [x] **9 curated models**: Qwen 3.5 Plus, DeepSeek Chat V3/R1, Llama 4 Scout/Maverick, Mistral Medium/Small, Gemma 3 27B, Phi-4
- [x] **Provider selector in dashboard**: switch between Ollama (local) and OpenRouter (cloud) per request
- [x] **Dynamic model list**: models populated from provider (local Ollama tags + OpenRouter catalog)
- [x] **Cost tracking**: per-request cost calculation based on OpenRouter pricing (input/output token rates)
- [x] **Streaming support**: OpenRouter streaming via same WebSocket pipeline as Ollama
- [x] **Docker env_file integration**: `.env.local` for API keys, no secrets in docker-compose
- [x] **Integration test suite**: 6/6 tests passing (OpenRouter, streaming, Ollama, StateGraph, multi-turn, comparison)

## Completed (v0.2.6 — Team Orchestration Graph)

Multi-agent orchestration visible in the dashboard.

- [x] **Team graph type**: new "Team" graph in the selector — team-lead delegates to sub-agents
- [x] **Agent-aware node wrapper** (`_agent_node`): wraps LLM calls with agent lifecycle events
- [x] **Agent spawn events**: `agent.spawn` emitted per agent — agent tree lights up in real-time
- [x] **Cooperation events**: `cooperation.task_assigned` / `cooperation.task_completed` — visible in inter-agent messages panel
- [x] **Parallel sub-agents**: backend-dev + frontend-dev run in parallel, team-lead summarizes
- [x] **Full event flow**: agent.spawn → task_assigned → agent.step → agent.complete → task_completed
- [x] **6 graph types total**: Auto, Chat, Review, Chain, Parallel, Team
- [x] **Graph reset**: clear all agent/task/event state from the dashboard
- [x] **Node replay**: re-run any completed node from the last graph execution
- [x] **Last run context**: stored for replay — provider, graph, state per step
- [x] **83 tests total**

---

## v0.3.0 — Agent Execution

Real agent execution through the dashboard, not just graph nodes.

### Local (Ollama)
- [ ] **Live agent execution**: agents run tasks via local LLM (qwen2.5-coder, deepseek-r1)
- [ ] **Agent spawning from dashboard**: select agent + Ollama model, assign a task, watch it work
- [ ] **Tool call visualization**: see each tool call/result in real-time (file edits, shell commands)
- [ ] **Skill invocation UI**: trigger skills manually from the dashboard
- [ ] **Per-agent model assignment**: pick different Ollama models per agent role

### Cloud (OpenRouter)
- [ ] **Cloud agent execution**: same agents run on cloud models (Qwen 3.5 Plus, DeepSeek V3, Llama 4)
- [ ] **Provider toggle per agent**: switch agent between Ollama and OpenRouter in one click
- [ ] **Cost preview**: show estimated cost before running a cloud agent task
- [ ] **Token budget per task**: set max tokens before execution starts (cloud-only safeguard)

## v0.4.0 — Multi-Agent Cooperation

Multiple agents working together on a single task.

### Local (Ollama)
- [ ] **Team-lead delegation (local)**: team-lead on qwen2.5-coder decomposes tasks to sub-agents
- [ ] **Parallel agent execution**: backend + frontend agents on separate Ollama models simultaneously
- [ ] **Shared context store**: agents publish artifacts (code, specs) that others can read
- [ ] **Agent-to-agent messages**: visible in the inter-agent communication panel
- [ ] **Dependency graph**: orchestrator respects ordering (e.g., backend API before frontend)

### Cloud (OpenRouter)
- [ ] **Hybrid cooperation**: team-lead on cloud (Qwen 3.5 Plus), sub-agents on local Ollama
- [ ] **Cloud escalation**: if local agent stalls, auto-escalate to cloud model
- [ ] **Cross-provider artifact sharing**: local and cloud agents share the same context store
- [ ] **Conflict resolution**: when 2 agents (local + cloud) modify the same file, team-lead resolves
- [ ] **Progress tracking**: real-time progress bar per agent with provider badge (local/cloud)

## v0.5.0 — Smart Routing & Cost Optimization

Intelligent model selection and cost control across local and cloud.

### Local (Ollama)
- [ ] **Local-first routing**: always try Ollama first, only go to cloud when needed
- [ ] **Model benchmarking (local)**: run tasks on multiple Ollama models, compare quality/speed
- [ ] **Ollama health monitoring**: track inference speed (tok/s), memory usage, model load status
- [ ] **Auto-model selection**: match task type to best local model (coding→coder, reasoning→deepseek)

### Cloud (OpenRouter)
- [ ] **Cost budgets**: set max spend per task/session/day, auto-switch to cheaper models or local
- [ ] **Fallback chains**: Ollama → OpenRouter → direct API (configurable per agent)
- [ ] **Provider health monitoring**: track latency, error rates, availability per OpenRouter model
- [ ] **Cost dashboard**: real-time cost tracking with projections, alerts, and local-vs-cloud breakdown
- [ ] **Model price comparison**: show cost/quality matrix across local and cloud models

### Hybrid
- [ ] **Complexity-based routing**: classify task difficulty → simple=local, medium=Qwen3.5, hard=DeepSeek R1
- [ ] **Automatic failover**: if Ollama is down or too slow, transparently route to OpenRouter
- [ ] **Split execution**: decompose task → run cheap sub-tasks locally, expensive ones on cloud

## v0.6.0 — Production Hardening

Make it reliable enough for real workloads.

### Local (Ollama)
- [ ] **Local model registry**: track which models are pulled, their sizes, last used date
- [ ] **Ollama auto-pull**: if a required model isn't available, pull it automatically
- [ ] **GPU memory management**: monitor VRAM usage, prevent OOM by queuing requests
- [ ] **Local inference metrics**: Prometheus metrics for tok/s, queue depth, model load times

### Cloud (OpenRouter)
- [ ] **API key rotation**: support multiple OpenRouter API keys with round-robin
- [ ] **Rate limiting**: per-provider token rate limits to avoid API throttling
- [ ] **Retry with backoff**: exponential backoff on provider errors (429, 500, timeout)
- [ ] **Spend alerts**: email/webhook notification when daily/weekly spend exceeds threshold

### Both
- [ ] **Persistent task queue**: tasks survive server restarts (Postgres-backed)
- [ ] **Authentication**: API key or OAuth for dashboard access
- [ ] **Audit log**: full trace of every agent action, tool call, decision, and provider used
- [ ] **Health checks**: `/health` endpoint with per-provider status (Ollama up? OpenRouter reachable?)
- [ ] **Metrics export**: Prometheus metrics for tokens, latency, cost, errors (tagged by provider)

## v0.7.0 — Advanced Graph Patterns

More powerful orchestration flows.

### Local (Ollama)
- [ ] **Local-only graph templates**: graph patterns optimized for Ollama models (smaller context)
- [ ] **Loop/retry nodes**: graph-level retry with automatic model upgrade on failure
- [ ] **Dynamic graph construction**: local LLM decides which nodes to add at runtime

### Cloud (OpenRouter)
- [ ] **Cloud-augmented nodes**: specific graph nodes that always run on cloud (e.g., final review)
- [ ] **Map-reduce with cloud fan-out**: parallel cloud calls for high-throughput processing
- [ ] **Long-context nodes**: nodes that require >128K context auto-routed to cloud models

### Both
- [ ] **Sub-graphs**: nested graphs as nodes (compose complex workflows)
- [ ] **Graph templates**: save/load reusable graph patterns from the dashboard
- [ ] **Graph versioning**: track changes to graphs over time
- [ ] **Provider annotations**: tag nodes with preferred provider (local/cloud/any)

## v0.8.0 — External Integrations

Connect to the real world.

### Local (Ollama)
- [ ] **Local RAG pipeline**: vector search over project docs using local embeddings (nomic-embed)
- [ ] **Local code indexing**: build codebase index with local model for context-aware agents
- [ ] **Offline mode**: full functionality without internet (local models + local tools only)

### Cloud (OpenRouter)
- [ ] **GitHub integration**: create PRs, review code, respond to issues (cloud model for quality)
- [ ] **Slack/Discord bot**: trigger orchestrator from chat, choose local or cloud execution
- [ ] **Webhook triggers**: start graphs from external events (CI, cron, API calls)
- [ ] **MCP server**: expose orchestrator as a Model Context Protocol server

### Both
- [ ] **Plugin system**: drop-in skills/providers without modifying core code
- [ ] **Provider marketplace**: browse and add new OpenRouter models or Ollama model configs
- [ ] **Unified RAG**: combine local embeddings with cloud reranking for best results

## v1.0.0 — General Availability

- [ ] **Stable API**: versioned REST API with OpenAPI spec
- [ ] **pip installable**: `pip install agent-orchestrator`
- [ ] **Web-based config**: edit agents, skills, graphs, provider settings from the dashboard
- [ ] **Multi-project support**: manage multiple codebases from one dashboard
- [ ] **User management**: multi-user with roles (admin, developer, viewer)
- [ ] **Provider presets**: one-click setup for "local only", "cloud only", "hybrid" modes
- [ ] **Documentation site**: full docs with tutorials, API reference, examples
- [ ] **Migration wizard**: import from LangGraph / CrewAI / AutoGen configs

---

## Provider Matrix

| Provider | Type | Models | Cost | Best For |
|----------|------|--------|------|----------|
| **Ollama** | Local | qwen2.5-coder, deepseek-r1, llama3.3, codestral | Free (hardware) | Daily tasks, privacy, speed |
| **OpenRouter** | Cloud | Qwen 3.5 Plus, DeepSeek V3/R1, Llama 4, Mistral, Gemma 3 | $0.10-2.00/1M tok | Complex tasks, long context, quality |
| **OpenAI** | Cloud (direct) | GPT-5 Nano, GPT-4.1 | $0.05-0.40/1M tok | Fallback, specific capabilities |
| **Anthropic** | Cloud (direct) | Claude Sonnet/Opus | $3-15/1M tok | Highest quality, complex reasoning |

---

## Backlog (Ideas)

- [ ] Voice interface (speak to agents via Whisper/STT — local via whisper.cpp)
- [ ] Mobile dashboard (responsive or native app)
- [ ] Fine-tuned local models for specific tasks (code review, test writing)
- [ ] A/B testing of prompts (run same task with different system prompts, compare local vs cloud)
- [ ] Agent marketplace (share/import agent configs and skills)
- [ ] Visual graph editor (drag-and-drop node builder)
- [ ] Multi-language support (agent UIs in different languages)
- [ ] Local model fine-tuning pipeline (LoRA on Ollama models from agent feedback)
- [ ] Edge deployment (run orchestrator on Raspberry Pi / NAS with small models)
