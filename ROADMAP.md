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
  - [x] 5 graph types: Auto, Chat, Review, Chain, Parallel
  - [x] Token speed metric (tok/s)
- [x] Docker/OrbStack: dashboard, postgres, test, lint, format services
- [x] Husky pre-commit hooks (lint, format, tests)
- [x] 7 agent definitions + 8 skills (incl. web-research)
- [x] 72 tests passing
- [x] Documentation: architecture, cost analysis, infrastructure, migration guide

---

## v0.2.0 — Local LLM Automation (Next)

Reach a solid level with local Ollama models to automate small daily tasks.

- [ ] **Streaming responses**: stream LLM output token-by-token to the chat UI via WebSocket (faster perceived speed)
- [ ] **Conversation memory**: persist chat history across sessions, with context window management
- [ ] **Task presets**: one-click buttons for common tasks (explain code, write tests, review PR, refactor, generate docs)
- [ ] **File context**: attach files/folders to the prompt so the LLM can read and modify them
- [ ] **Code execution**: run generated code snippets directly from the dashboard (sandboxed)
- [ ] **Multi-turn chat**: keep conversation context across messages (not just single-shot)
- [ ] **Prompt templates**: save reusable prompt templates for repetitive tasks
- [ ] **Model comparison**: run same prompt on 2+ Ollama models side-by-side, compare quality/speed
- [ ] **Auto-select model**: pick best local model based on task type (coding -> qwen2.5-coder, reasoning -> deepseek-r1)
- [ ] **Ollama model management**: pull/delete models from the dashboard UI

## v0.3.0 — Agent Execution

Real agent execution through the dashboard, not just graph nodes.

- [ ] **Live agent execution**: agents actually run tasks via LLM with tool use
- [ ] **Agent spawning from dashboard**: select an agent, assign a task, watch it work
- [ ] **Skill invocation UI**: trigger skills manually from the dashboard
- [ ] **Tool call visualization**: see each tool call/result in real-time (file edits, shell commands)
- [ ] **Multi-model routing**: pick different Ollama models per agent in the dashboard

## v0.4.0 — Multi-Agent Cooperation

Multiple agents working together on a single task.

- [ ] **Team-lead delegation**: team-lead receives task, decomposes, assigns to sub-agents
- [ ] **Parallel agent execution**: backend + frontend agents work simultaneously
- [ ] **Shared context store**: agents publish artifacts (code, specs) that others can read
- [ ] **Dependency graph**: orchestrator respects ordering (e.g., backend API before frontend)
- [ ] **Conflict resolution**: when 2 agents modify the same file, team-lead resolves
- [ ] **Progress tracking**: real-time progress bar per agent in the dashboard
- [ ] **Agent-to-agent messages**: visible in the inter-agent communication panel

## v0.5.0 — Smart Routing & Cost Optimization

Intelligent model selection and cost control.

- [ ] **Auto-routing by complexity**: classify task difficulty, pick model accordingly
- [ ] **Cost budgets**: set max spend per task/session, auto-switch to cheaper models
- [ ] **Fallback chains**: if Ollama fails, fall back to cloud provider (or vice versa)
- [ ] **Model benchmarking**: run tasks on multiple models, compare quality/speed/cost
- [ ] **Provider health monitoring**: track latency, error rates, availability per provider
- [ ] **Cost dashboard**: real-time cost tracking with projections and alerts

## v0.6.0 — Production Hardening

Make it reliable enough for real workloads.

- [ ] **Persistent task queue**: tasks survive server restarts (Redis or Postgres-backed)
- [ ] **Authentication**: API key or OAuth for dashboard access
- [ ] **Rate limiting**: per-provider token rate limits to avoid API throttling
- [ ] **Retry with backoff**: exponential backoff on provider errors
- [ ] **Audit log**: full trace of every agent action, tool call, and decision
- [ ] **Health checks**: `/health` endpoint, container health probes
- [ ] **Metrics export**: Prometheus metrics for monitoring (tokens, latency, cost, errors)

## v0.7.0 — Advanced Graph Patterns

More powerful orchestration flows.

- [ ] **Sub-graphs**: nested graphs as nodes (compose complex workflows)
- [ ] **Map-reduce**: fan out over a list, process in parallel, aggregate results
- [ ] **Loop/retry nodes**: graph-level retry with max iterations
- [ ] **Dynamic graph construction**: LLM decides which nodes to add at runtime
- [ ] **Graph templates**: save/load reusable graph patterns from the dashboard
- [ ] **Graph versioning**: track changes to graphs over time

## v0.8.0 — External Integrations

Connect to the real world.

- [ ] **GitHub integration**: create PRs, review code, respond to issues
- [ ] **Slack/Discord bot**: trigger orchestrator from chat, receive results
- [ ] **Webhook triggers**: start graphs from external events (CI, cron, API calls)
- [ ] **MCP server**: expose orchestrator as a Model Context Protocol server
- [ ] **Plugin system**: drop-in skills/providers without modifying core code
- [ ] **RAG pipeline**: vector search over project docs for context-aware agents

## v1.0.0 — General Availability

- [ ] **Stable API**: versioned REST API with OpenAPI spec
- [ ] **pip installable**: `pip install agent-orchestrator`
- [ ] **Web-based config**: edit agents, skills, graphs from the dashboard (no YAML)
- [ ] **Multi-project support**: manage multiple codebases from one dashboard
- [ ] **User management**: multi-user with roles (admin, developer, viewer)
- [ ] **Documentation site**: full docs with tutorials, API reference, examples

---

## Backlog (Ideas)

- [ ] Voice interface (speak to agents via Whisper/STT)
- [ ] Mobile dashboard (responsive or native app)
- [ ] Fine-tuned local models for specific tasks (code review, test writing)
- [ ] A/B testing of prompts (run same task with different system prompts, compare)
- [ ] Agent marketplace (share/import agent configs and skills)
- [ ] Visual graph editor (drag-and-drop node builder)
- [ ] Multi-language support (agent UIs in different languages)
