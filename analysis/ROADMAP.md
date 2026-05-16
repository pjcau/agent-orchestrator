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
- [x] **83 tests passing**

---

## Completed (v0.3.0 — Agent Execution)

Real agent execution through the dashboard, not just graph nodes.

### Local (Ollama)
- [x] **Live agent execution**: agents run tasks via local LLM (qwen2.5-coder, deepseek-r1)
- [x] **Agent spawning from dashboard**: select agent + Ollama model, assign a task, watch it work
- [x] **Tool call visualization**: see each tool call/result in real-time (file edits, shell commands)
- [x] **Skill invocation UI**: trigger skills manually from the dashboard (clickable skill tags)
- [x] **Per-agent model assignment**: pick different Ollama models per agent role

### Cloud (OpenRouter)
- [x] **Cloud agent execution**: same agents run on cloud models (Qwen 3.5 Plus, DeepSeek V3, Llama 4)
- [x] **Provider toggle per agent**: switch agent between Ollama and OpenRouter in one click
- [x] **Cost preview**: show estimated cost before running a cloud agent task
- [ ] **Token budget per task**: set max tokens before execution starts (cloud-only safeguard)

## Completed (v0.4.0 — Multi-Agent Cooperation)

Multiple agents working together on a single task.

### Local (Ollama)
- [x] **Team-lead delegation (local)**: team-lead decomposes tasks to sub-agents via `Orchestrator.run()`
- [x] **Parallel agent execution**: independent agents run simultaneously via `asyncio.gather`
- [x] **Shared context store**: agents publish artifacts (code, specs) that others can read; subscription support
- [x] **Agent-to-agent messages**: `AgentMessage` with from/to/broadcast, visible via `SharedContextStore`
- [x] **Dependency graph**: orchestrator respects topological ordering, `get_parallel_batches()` for concurrent dispatch

### Cloud (OpenRouter)
- [x] **Hybrid cooperation**: team-lead on cloud, sub-agents on local — `escalation_provider_key` per agent
- [x] **Cloud escalation**: if local agent stalls (max retries), auto-escalate to cloud model via `Agent.execute()`
- [x] **Cross-provider artifact sharing**: local and cloud agents share the same `SharedContextStore`
- [x] **Conflict resolution**: `ConflictRecord` auto-detected when different agents modify same artifact; `resolve_conflict()` API
- [x] **Progress tracking**: `on_progress` callback in Orchestrator emits events for dashboard (agent.start, batch.start/end, task.complete)

## Completed (v0.5.0 — Smart Routing & Cost Optimization)

Intelligent model selection and cost control across local and cloud.

### Local (Ollama)
- [x] **Local-first routing**: always try Ollama first, only go to cloud when needed
- [x] **Model benchmarking (local)**: run tasks on multiple Ollama models, compare quality/speed
- [x] **Ollama health monitoring**: track inference speed (tok/s), memory usage, model load status
- [x] **Auto-model selection**: match task type to best local model (coding→coder, reasoning→deepseek)

### Cloud (OpenRouter)
- [x] **Cost budgets**: set max spend per task/session/day, auto-switch to cheaper models or local
- [x] **Fallback chains**: Ollama → OpenRouter → direct API (configurable per agent)
- [x] **Provider health monitoring**: track latency, error rates, availability per OpenRouter model
- [x] **Cost dashboard**: real-time cost tracking with projections, alerts, and local-vs-cloud breakdown
- [x] **Model price comparison**: show cost/quality matrix across local and cloud models

### Hybrid
- [x] **Complexity-based routing**: classify task difficulty → simple=local, medium=Qwen3.5, hard=DeepSeek R1
- [x] **Automatic failover**: if Ollama is down or too slow, transparently route to OpenRouter
- [x] **Split execution**: decompose task → run cheap sub-tasks locally, expensive ones on cloud

## Completed (v0.6.0 — Production Hardening)

Make it reliable enough for real workloads.

### Local (Ollama)
- [ ] **Local model registry**: track which models are pulled, their sizes, last used date
- [ ] **Ollama auto-pull**: if a required model isn't available, pull it automatically
- [ ] **GPU memory management**: monitor VRAM usage, prevent OOM by queuing requests
- [x] **Local inference metrics**: Prometheus metrics for tok/s, queue depth, model load times

### Cloud (OpenRouter)
- [ ] **API key rotation**: support multiple OpenRouter API keys with round-robin
- [x] **Rate limiting**: per-provider token rate limits to avoid API throttling
- [ ] **Retry with backoff**: exponential backoff on provider errors (429, 500, timeout)
- [x] **Spend alerts**: email/webhook notification when daily/weekly spend exceeds threshold

### Both
- [x] **Persistent task queue**: tasks survive server restarts (Postgres-backed)
- [x] **Authentication**: API key or OAuth for dashboard access
- [x] **Audit log**: full trace of every agent action, tool call, decision, and provider used
- [ ] **Health checks**: `/health` endpoint with per-provider status (Ollama up? OpenRouter reachable?)
- [x] **Metrics export**: Prometheus metrics for tokens, latency, cost, errors (tagged by provider)

## Completed (v0.7.0 — Advanced Graph Patterns)

More powerful orchestration flows.

### Local (Ollama)
- [x] **Loop/retry nodes**: graph-level retry with automatic model upgrade on failure
- [x] **Long-context nodes**: nodes that require >128K context auto-routed to cloud models
- [ ] **Local-only graph templates**: graph patterns optimized for Ollama models (smaller context)
- [ ] **Dynamic graph construction**: local LLM decides which nodes to add at runtime

### Cloud (OpenRouter)
- [x] **Cloud-augmented nodes**: specific graph nodes that always run on cloud (provider annotations)
- [x] **Map-reduce with cloud fan-out**: parallel cloud calls for high-throughput processing

### Both
- [x] **Sub-graphs**: nested graphs as nodes (compose complex workflows)
- [x] **Graph templates**: save/load reusable graph patterns (JSON serialisation, versioned store)
- [x] **Graph versioning**: track changes to graphs over time (auto-increment on save)
- [x] **Provider annotations**: tag nodes with preferred provider (local/cloud/any)

## Completed (v0.8.0 — External Integrations)

Connect to the real world.

### Local (Ollama)
- [x] **Offline mode**: full functionality without internet (local models + local tools only)
- [ ] **Local RAG pipeline**: vector search over project docs using local embeddings (nomic-embed)
- [ ] **Local code indexing**: build codebase index with local model for context-aware agents

### Cloud (OpenRouter)
- [x] **GitHub integration**: create PRs, review code, respond to issues via `gh` CLI
- [x] **Webhook triggers**: start graphs from external events (CI, cron, API calls)
- [x] **MCP server**: expose orchestrator as a Model Context Protocol server
- [ ] **Slack/Discord bot**: trigger orchestrator from chat, choose local or cloud execution

### Both
- [x] **Plugin system**: drop-in skills/providers without modifying core code
- [ ] **Provider marketplace**: browse and add new OpenRouter models or Ollama model configs
- [x] **Unified RAG**: combine local embeddings with cloud reranking for best results — **shipped in v1.3.0 P1** (HashEmbedder dev default, sentence-transformers via `[rag]`, OpenAI via `[openai]`)

## Completed (v1.0.0 — General Availability)

- [x] **Stable API**: versioned REST API (`/api/v1/`) with OpenAPI 3.0 spec export (27 endpoints)
- [x] **pip installable**: `pip install agent-orchestrator` (hatchling build, version 1.0.0)
- [x] **Web-based config**: ConfigManager with JSON import/export, validation, rollback history
- [x] **Multi-project support**: ProjectManager with CRUD, archive/unarchive, current project switching
- [x] **User management**: UserManager with roles (admin, developer, viewer), RBAC, API key auth
- [x] **Provider presets**: 4 built-in presets (local_only, cloud_only, hybrid, high_quality) + custom
- [x] **Documentation site**: Docusaurus site with roadmap docs for every version
- [x] **Migration wizard**: import from LangGraph, CrewAI, AutoGen configs with auto-detection

---

## Completed (v1.3.0 — Q1+Q2 Sprint, May 2026)

Six priorities (P1–P6) from the harnessed-LLM-agent reference matrix shipped in a single afternoon: 4 backend agents + 1 architect agent + 1 frontend agent worked in parallel worktrees, then converged into main. Reference-matrix coverage went from ~82% to **~95%** (18/19 ✅, 1 ⚠, 0 ❌). Tests: 1865 → **2065** (+200).

Live deep-dive (mermaid graphs + try-it commands per priority):
**[https://pjcau.github.io/agent-orchestrator/docs/roadmap/q1q2-sprint](https://pjcau.github.io/agent-orchestrator/docs/roadmap/q1q2-sprint)**

### P1 — Semantic Knowledge / RAG (the only ❌ flipped to ✅)
- [x] **Knowledge subsystem**: `core/knowledge/` with `EmbeddingProvider` ABC + 3 impls (`HashEmbedder` for dev, `LocalEmbeddingProvider` via sentence-transformers, `OpenAIEmbeddingProvider`), `Chunker` ABC + `MarkdownChunker` / `TextChunker`, `KnowledgeStore` (ISP-split into `IngestInterface` / `QueryInterface`), `Ingester`, `Retriever`. Namespaces: `("shared",)`, `("agent", name)`, `("user", id)`.
- [x] **RetrievalSkill**: agents call `knowledge_retrieve` to pull top-k chunks; result rendered as Markdown context block with citations.
- [x] **REST API**: `POST /api/knowledge/{ingest,search}`, `GET /api/knowledge/{namespaces,health}`, `DELETE /api/knowledge/namespaces/{ns}`.
- [x] **EventBus**: `KNOWLEDGE_INGESTED`, `KNOWLEDGE_RETRIEVED`, `KNOWLEDGE_RETRIEVAL_SKIPPED`.
- [x] **Frontend toggle**: RAG checkbox + namespace input next to the Stream toggle in `ChatInput`. Persisted via Zustand + `localStorage` (`ao_rag_enabled` / `ao_rag_namespace`); survives Reset (user preference, not session state).
- [x] **Frontend log highlighting**: `knowledge.*` events get a "K" icon and a distinct accent in the right-side event log; new "Knowledge" filter option. System bubble "RAG: \<namespace\> · N chunks retrieved (\<embedding_model\>)" before each assistant reply when enabled.
- [x] **Production swap-in**: env vars `RAG_EMBEDDING_PROVIDER={hash,openai,local}` + `RAG_OPENAI_MODEL` / `RAG_LOCAL_MODEL` pick alternative embedders without code changes (Open/Closed).

### P2 — Evaluator Framework
- [x] **Core**: `core/evaluator.py` with `Evaluator` ABC, `RubricEvaluator` (regex / contains / JSON-schema / min-max length, weighted), `LLMJudge` (Provider-injected, robust JSON extraction), `EvalSuite`, `EvalReport`, `JsonDataset`.
- [x] **Smoke dataset**: `evals/datasets/smoke.json` (5 hand-picked cases: code summary, math reasoning, JSON output, safety refusal, conversational).
- [x] **CLI runner**: `python -m evals.runners.cli --suite … --agent … --provider … --model …` with coloured table, `--dry-run`, `--json` output, HTTP agent mode.
- [x] **REST**: `POST /api/evals/run`, `GET /api/evals/runs`, `GET /api/evals/runs/{id}`, `GET /api/evals/compare?a=&b=` with delta on pass_rate + mean_score.

### P3 — Guardrails Layer
- [x] **Unified `GuardrailManager`**: aggregates redacts and short-circuits on first block.
- [x] **Built-ins**: `PIIScanner` (email / phone / SSN / IBAN / credit cards — redact), `SecretsScanner` (AWS keys, GitHub tokens, generic API keys — block), `PromptInjectionDetector` (heuristic — block), `OutputSchemaGuard` (JSON Schema validation — block), `CostGuard` (per-call budget cap — block).
- [x] **Agent integration**: `Agent.execute()` calls `run_input(messages)` pre-LLM and `run_output(response)` post-LLM. Block raises `GuardrailBlocked` (`RuntimeError` subclass); redact substitutes messages.
- [x] **Events + metrics**: `guardrail.checked / blocked / redacted` events; counters `guardrail_checks_total{type,side}`, `guardrail_blocks_total{type}`, `guardrail_redactions_total{type}`.
- [x] **YAML config**: `orchestrator.yaml` `guardrails:` block with `input:` and `output:` lists; `guardrail_manager_from_config(yaml_dict)` loader.

### P4 — Personalized Memory
- [x] **`PersonalizedMemory`** facade over `BaseStore`: `put`, `get`, `list`, `delete`, `wipe` scoped to `("user", user_id)` namespace.
- [x] **MemoryFilter integration**: blacklisted paths never persist (RGPD-friendly defaults).
- [x] **`ProfileExtractorSkill`**: scans recent messages, calls a `Provider` to extract preferences/style/recurring topics, persists best-effort (Provider failure does NOT block the agent flow).
- [x] **System-prompt injection**: `Agent` `__init__` accepts `personalized_memory` + `user_id`; `build_system_prompt()` appends a `<user_profile>` block when both are set. `prefetch_user_profile()` async method warms the cache before the synchronous prompt-build path.
- [x] **REST**: `/api/user-memory/users/{user_id}` (GET list / DELETE wipe), `/api/user-memory/users/{user_id}/{key}` (GET single / DELETE single). Prefix `/api/user-memory/` chosen to avoid the existing `/api/memory/{namespace:path}` catch-all.

### P5a — Agent ↔ Agent typed messages + spec
- [x] **Typed dataclasses** in `core/cooperation_messages.py`: `CooperationMessage` (base), `DelegateMessage`, `ResultMessage`, `CapabilityQueryMessage`, `CapabilityResponseMessage`, `ConflictMessage`. Each has `from_dict` / `to_dict` round-trips.
- [x] **`parse_message(d)`** dispatcher on the `kind` field. Tolerant: missing optional fields default to None / [].
- [x] **Backwards-compatible**: existing dict-based callers in `core/cooperation.py` keep working — the typed module is additive.
- [x] **Protocol spec**: [`docs/cooperation-protocol.md`](../docs/cooperation-protocol.md) with mermaid sequence diagram + state-transition diagram (`TaskAssigned → InProgress → Completed/Failed/Conflicted`) + error-handling rules + migration path.
- [ ] **P5b — A2A adapter** (parked). Google A2A spec still moving as of April 2026; revisit Q3.

### P6 — Observability sinks (Langfuse + Phoenix)
- [x] **`core/observability/langfuse_exporter.py`**: registers a Langfuse span processor. Env: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`. Optional dep: `pip install -e ".[langfuse]"`.
- [x] **`core/observability/phoenix_exporter.py`**: registers a Phoenix (Arize) OTLP HTTP exporter. Env: `PHOENIX_COLLECTOR_ENDPOINT`, `PHOENIX_API_KEY`. Optional dep: `pip install -e ".[phoenix]"`.
- [x] **Both opt-in + graceful degradation**: importing without the optional package logs a warning and no-ops. Existing Tempo / OTel / Prometheus pipeline keeps working alongside.
- [x] **Trace schema doc**: [`docs/trace-schema.md`](../docs/trace-schema.md) with span inventory (`agent.run`, `llm.call` with `gen_ai.*` attributes, `graph.node`, `skill.execute`, `agent.message`) and how to view traces in each backend.

### Pre-sprint UI improvements (also landed today)
- [x] **A2 — Conversation persistence**: `conversationId` auto-created on first send, persisted via `localStorage` (`ao_conv_id`), chat replays at boot.
- [x] **B — Full Reset**: button at the top of Agent Interactions wipes graph + chat + attached files + conversation memory + `localStorage`. Best-effort: UI clears even on network failure.
- [x] **C2 — Real document upload**: `POST /api/upload` (multipart) with `DocumentConverter` server-side. Replaces the previous `file.text()` flow that silently corrupted binary files.
- [x] **C2.1 — Image OCR**: `_convert_image` via `pytesseract` + `Pillow` + `tesseract` system binary. PNG / JPG / etc. now produce real markdown via OCR instead of being rejected as "unsupported".
- [x] **D — File context transparency**: each chip carries a kind badge (PDF/CSV/IMG/…), byte size, source colour (upload vs workspace), truncation indicator. System bubble at send time: `Sent with N files: a.pdf (3.2 KB) [upload], b.csv (12 KB) [workspace]`.
- [x] **Vanilla UI removed**: 5198 lines deleted from `dashboard/static/`. Production has always served React from `frontend/dist/` via the Docker build; the vanilla fallback was dead code.

### CI / pipelines fixed in the same day
- [x] **Code-scanning alerts**: 11 CodeQL errors closed (8 log-injection via `_safe_log` helper, 3 stack-trace-exposure by replacing `str(exc)` with canonical messages).
- [x] **pip-audit**: 4 vulnerable transitive deps upgraded (`authlib >= 1.6.11`, `PyJWT >= 2.12.0`, `cryptography >= 46.0.7`, `pytest >= 9.0.3`); 3 unfixable pip CVEs ignored with `--ignore-vuln` (pip itself comes from the runner image).
- [x] **Deploy + Security Scan + Deploy Docusaurus**: all three pipelines green again. Deploy Docusaurus had been red since 2026-04-21 because no commit had touched `docs/website/**`; that's now fixed and the published site reflects the sprint.

---

## Planned (v1.4.0 — External Reference Gap Closure, Q3 2026)

Cross-analysis of three reference projects in `analysis/` — **DeepFlow**, **Harnessed-LLM-Agent**, **LangGraph** — to find features the orchestrator still lacks. After grep-verification against `src/`, the previous draft of this section was 80 % wrong: most candidates from DeepFlow / LangGraph are **already shipped** and live under `core/{loop_detection,sandbox,channels,cache,clarification,conformance,resilience,tool_recovery,yaml_config,memory_filter}.py` (+ tests). Below are only the items that grep + UI inspection confirmed are still missing.

### Confirmed shipped (do NOT re-do)

For traceability — these were on the original draft list but are already on `main`:

| Candidate | Where it lives now |
|---|---|
| Loop detection middleware | `core/loop_detection.py` (+ `tests/test_loop_detection.py`) |
| Sandboxed code execution | `core/sandbox.py`, `skills/sandboxed_shell.py` |
| Progressive skill loading | `skills/skill_loader.py` |
| Structured clarification protocol | `core/clarification.py`, `skills/clarification_skill.py` |
| Dangling tool-call recovery | `core/tool_recovery.py::recover_dangling_tool_calls` |
| Typed channels (`LastValue`/`Topic`/…) | `core/channels.py` |
| Per-node CachePolicy | `core/cache.py` |
| Skill middleware chain | wired in `core/skill.py` (+ `tests/test_skill_middleware.py`) |
| RetryPolicy + circuit breaker | `core/resilience.py::RetryPolicy`, `CircuitBreaker`, `resilient_call` |
| Conformance test suite | `core/conformance.py` |
| `tool_description` on tool calls | `core/skill.py` (`SkillRequest.metadata["tool_description"]`) |
| Configurable summarisation triggers | `core/conversation.py::SummarizationConfig` (TOKEN/MESSAGE/FRACTION + `retain_last`) |
| Config versioning + auto-upgrade | `core/yaml_config.py` (`config_version`, `_upgrade_v0_to_v1`, `CURRENT_CONFIG_VERSION`) |
| Memory upload filter | `core/memory_filter.py::MemoryFilter` |
| HITL interrupt/resume, RAG, Evaluator, Langfuse/Phoenix, embedded client, IM channels, harness boundary, file upload + OCR, GuardrailManager | shipped in v0.1.0–v1.3.0 |

### Genuinely missing (the v1.4.0 scope)

#### P1 — Must-have
- [ ] **A2A adapter (P5b — un-park)** — The only ⚠ in the harnessed-LLM-agent 19-item match matrix. Builds on top of `core/cooperation_messages.py` already in main. Goal: bidirectional bridge with Google's A2A protocol so external A2A agents appear as local cooperation peers (and vice-versa). Spec churn was the reason for the Q1 park; revisit now that the Apr-2026 stabilisation has landed.
- [ ] **Managed values (computed state)** — LangGraph-style read-only injections into node state: `step_count`, `remaining_steps`, `interrupt_ids`, `is_final_step`. Computed at runtime by the engine, **never checkpointed**. Lets nodes self-throttle (`if remaining_steps < 2: summarise_and_finish()`) without polluting `State` schemas. Touches `core/graph.py` (`Pregel` loop) + new `core/managed_values.py`.

#### P2 — Nice-to-have
- [ ] **Personalized Memory dashboard UI** — REST endpoints `/api/user-memory/users/*` exist since v1.3.0 P4, but no React page consumes them (verified — `frontend/src/components/` has no `user-memory` references). Build a `frontend/src/components/memory/UserMemoryPanel.tsx` that lists per-user keys, lets the user edit/delete entries, and shows the last `<user_profile>` block injected into the system prompt.
- [ ] **Granular stream modes (close the gap to LangGraph 7)** — `dashboard/sse.py` exposes 2 of LangGraph's 7 modes today: `events` (default) and `values`. Add the 5 missing: `updates` (per-node delta), `messages` (LLM-message stream only), `tasks` (task lifecycle), `debug` (verbose internal), `custom` (user-emitted via `emit()`). Selectable per WebSocket subscription. Reduces frontend filtering load and matches LangGraph SDK consumers.

#### P3 — Optional
- [ ] **Content-addressed checkpoint blobs** — In `core/store_postgres.py` / `PostgresCheckpointer`, split large state values into a `blobs(sha256 PRIMARY KEY, payload BYTEA)` table and reference by hash. Repeated values across checkpoints share a single row. Expected 5-20× storage reduction on long conversations where the same RAG context or system prompt repeats.
- [ ] **Structured deprecation annotations** — `@deprecated(since="1.4", removed_in="1.6", migration="use X instead")` decorator that emits a `DeprecationWarning` + powers a `docs/deprecations.md` page generated at release time. Currently zero `@deprecated` / `DeprecationWarning` hits in `core/`. Pre-requisite for any 2.x cleanup pass.

### Sprint shape (suggested)
- 1 backend agent on **A2A adapter** (largest item; touches cooperation + new external protocol).
- 1 architect agent on **managed values** (Pregel loop change — needs careful design).
- 1 backend agent on **content-addressed blobs** + **deprecation decorator** (both small, same dev).
- 1 frontend agent on **Personalized Memory UI** + **5 missing stream modes UI selector**.
- Match-matrix target: **19/19 ✅** by end of sprint.

---

## In Progress (v1.5.0 — Workspace Repair Loop, Q3 2026)

Verify-and-retry pipeline wrapped around `run_team()`. Motivated by the 2026-05-16 baseline run (`docs/learning-path-tests/2026-05-16_task-tracker.md`) where a single `psycopg<3` typo dropped the confidence score from ~79 to 32.5/100. Distinct from the per-skill `verification_middleware` (PR #59): this validates the **whole workspace** after a team run and retries up to 5 times.

### Phase status

- [x] **Phase 1** — Design doc (`docs/architecture-repair-loop.md`)
- [x] **Phase 2** — `VerificationGate` + 3 verifiers (`core/verification_gate.py`, `core/verifiers/{syntax,encoding,dependency}.py`)
- [x] **Phase 3** — `RepairLoop` harness (`core/repair_loop.py`) — max 5 attempts, $0.50 cumulative cap, signature memory for anti-loop
- [x] **Phase 4** — `FailurePatternRegistry` + bundled YAML (`core/failure_patterns.{py,yaml}`) — `pip_pin_repair`, `unicode_unescape`, `noop`
- [x] **Phase 5** — Opt-in wiring into `/api/team/run` (`dashboard/agent_runtime_router.py`, `dashboard/events.py`, `orchestrator.yaml.example`) — controlled by `REPAIR_LOOP_ENABLED=true`
- [x] **Phase 6** — Feature maps + roadmap sync (this file, `docs/website/architecture-map.yaml`, regenerated `*-map.json`, `sidebars.js`, Docusaurus page)
- [x] **Phase 7** — `/orchestrator-learning-path-test` validation: 49.0/100 (vs 32.5 baseline, +16.5); what-if with the dep gap patched = 72/100. Surfaced 2 verifier gaps + 1 UI gap, all closed below.
- [x] **Phase 7.1** — `ImportVerifier` + `requirements_append` auto-fix (catches the passlib/python-jose missing-dep failure mode the original 3-verifier chain missed)
- [x] **Phase 7.2** — `WorkspaceCoherenceVerifier` (catches `docker-compose.yml::DATABASE_URL` vs `backend/database.py` default scheme mismatches)
- [x] **Phase 7.3** — React dashboard surfaces the `repair: {…}` summary as a system message
- [x] **Phase 7.4** — Default verifier chain extended from 3 to 5
- [ ] **Phase 7.5** — Benchmark re-run with the 5-verifier chain (target: close the +23-point what-if gap)

### KPIs

- Confidence on 2026-05-16 baseline: **32.5 → ~85** (estimate; validated in Phase 7)
- Median verifier-chain overhead per `team_run`: target **< 15 s**
- LLM-call-free auto-fix coverage: **≥ 33 %** of first-attempt failures

Full design + per-phase plan: `docs/architecture-repair-loop.md` and [website roadmap → v1.5](https://pjcau.github.io/agent-orchestrator/docs/roadmap/v150-repair-loop).

---

## Provider Matrix

| Provider | Type | Models | Cost | Best For |
|----------|------|--------|------|----------|
| **Ollama** | Local | qwen2.5-coder, deepseek-r1, llama3.3, codestral | Free (hardware) | Daily tasks, privacy, speed |
| **OpenRouter** | Cloud | Qwen 3.5 Plus, DeepSeek V3/R1, Llama 4, Mistral, Gemma 3 | $0.10-2.00/1M tok | Complex tasks, long context, quality |
| **OpenAI** | Cloud (direct) | GPT-5 Nano, GPT-4.1 | $0.05-0.40/1M tok | Fallback, specific capabilities |
| **Anthropic** | Cloud (direct) | Claude Sonnet/Opus | $3-15/1M tok | Highest quality, complex reasoning |

---

## Phase 1 — Conversation Memory (v1.2)

Multi-turn conversation memory for iterative multi-agent interactions.

### Completed
- [x] **Thread-based message history** (Solution 1): `ConversationManager` accumulates messages per thread, uses checkpointing for persistence. Supports multi-turn, thread isolation, fork, clear, max_history trim. Works with InMemory and SQLite checkpointers.
- [x] **Agent conversation memory**: `Agent.execute()` accepts `conversation_history` parameter. Previous user/assistant exchanges are prepended to the agent's message list for multi-turn context.
- [x] **Graph/prompt conversation memory**: `run_graph()` accepts `conversation_id` + `conversation_manager`. Previous exchanges are prepended to the prompt automatically.
- [x] **Dashboard integration**: `ConversationManager` wired into `/api/prompt`, `/api/agent/run`, `/api/team/run`. New endpoints: `DELETE /api/conversation/{id}`, `POST /api/conversation/{id}/fork`, `GET /api/conversations`.
- [x] **24 tests**: Core manager, agent integration, graph integration, persistence, fork, clear, metadata.

### Planned
- [x] **Store-based semantic memory** (Solution 2): **shipped in v1.3.0 P1+P4**. `core/knowledge` provides retrieval over the same `BaseStore`; `core/personalized_memory` handles per-user namespace; the `Retriever.retrieve()` call returns a Markdown context block that the agent can prepend to its system prompt. RAG handles the embedding + chunking; LLM summarisation is reserved for the existing `SummarizationConfig` path on long conversation histories.
- [x] **PostgreSQL conversation store**: `PostgresCheckpointer(_db_url, table_name="conversation_checkpoints")` already wired in `dashboard/app.py` startup; falls back to `InMemoryCheckpointer` when `DATABASE_URL` is not set.

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
