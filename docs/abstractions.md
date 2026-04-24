# Key Abstractions (Reference Catalog)

Exhaustive list of every abstraction in the codebase, grouped by concern. For conceptual architecture and design rationale see `architecture.md`.

## Core Runtime

- **Provider** — LLM backend (Claude, GPT, Gemini, local). Swappable per agent. `core/provider.py`
- **Agent** — Autonomous unit with a role, tools, and a provider. Stateless between tasks. `core/agent.py`
- **Skill** — Reusable capability with middleware chain (retry, logging, timeout). Provider-independent. `core/skill.py`
- **Orchestrator** — Coordinates agents, task decomposition, anti-stall enforcement. `core/orchestrator.py`
- **Cooperation** — Inter-agent messaging: delegation, results, conflict resolution. `core/cooperation.py`
- **OrchestratorClient** — Embedded Python client (`client.py`). Wraps Orchestrator, Agent, SkillRegistry, and StateGraph into a single API. Supports `run_agent()`, `run_team()`, `run_graph()`, `list_agents()`, `list_skills()`, plus sync wrappers. No HTTP server required.

## Routing & Cost Control

- **TaskRouter** — Smart routing: 6 strategies (local-first, cost-optimized, complexity-based, etc.). Category-aware: auto-detects task domain (finance, data-science, marketing, software) and selects appropriate agents. Fallback routing uses category-matched agents instead of defaulting to backend+frontend. `core/router.py`
- **UsageTracker** — Cost tracking with budget enforcement (per task/session/day). `core/usage.py`
- **HealthMonitor** — Provider health: latency, error rates, availability, auto-failover. `core/health.py`
- **RateLimiter** — Per-provider rate limiting. `core/rate_limiter.py`
- **ProviderPresetManager** — One-click presets: local_only, cloud_only, hybrid, high_quality. `core/provider_presets.py`

## Observability

- **AuditLog** — Structured audit trail: 11 event types, filtering, task traces. `core/audit.py`
- **MetricsRegistry** — Prometheus-compatible metrics (counters, gauges, histograms). `core/metrics.py`
- **Tracing** — Optional OpenTelemetry integration. Initialized in `server.py` at startup via `setup_tracing()` + `instrument_fastapi()`. Spans on `Provider.traced_complete()`, `Agent._execute_with_provider()`, graph nodes. `instrument.py` also feeds `tracing_metrics` collectors (LLM durations, node durations, stall counts) which are exported at `/metrics` for Prometheus. Graceful no-op when OTel packages not installed. Exports via OTLP HTTP to Tempo.
- **AlertHandler** — Receives Grafana webhook alerts, collects diagnostics (recent errors, usage, metrics), creates GitHub issues with `gh` CLI. Triggers automated root-cause analysis via `.github/workflows/alert-analysis.yml`.

## Graph Engine

- **StreamEvent / astream()** — Real-time graph execution streaming. `CompiledGraph.astream()` yields `StreamEvent` at each node start/end/error, with state deltas and timing. `core/graph.py`
- **GraphTemplateStore** — Versioned graph templates with JSON serialisation and `build_graph()`. `core/graph_templates.py`
- **SubGraphNode** — Wrap compiled graphs as callable nodes with I/O mapping. `core/graph_patterns.py`
- **Hybrid Graph Execution** (PR #84) — `CompiledGraph.invoke(preload=[...], store=...)`. Pre-fetches `(namespace, key, state_key)` triples from a `BaseStore` and merges into `initial_state` before graph traversal. Lets nodes reference external memory without extra skill calls. Missing keys silently skipped. Regression-safe.
- **RunManager (SSE)** — HTTP SSE streaming for graph execution. Creates background runs, fans events to multiple SSE subscribers, supports HITL interrupt/resume with configurable timeout. Max 100 runs, 30-min TTL eviction. Mirrors events to EventBus for WebSocket clients. `dashboard/sse.py`

## Memory & State

- **BaseStore** — Cross-thread persistent key-value store (namespace, filter, TTL). Separate from checkpoints. `core/store.py`
- **PostgresStore** — Durable PostgreSQL backend for BaseStore. JSONB values, lazy TTL expiry, UPSERT semantics, dot-encoded namespaces. Wired into dashboard at startup when `DATABASE_URL` is set; falls back to InMemoryStore. `core/store_postgres.py`
- **SessionStore** — Session-scoped wrapper on BaseStore. Auto-tracks written keys, deletes all session data on close(). Async context manager.
- **ConversationManager** — Thread-based multi-turn memory. Accumulates messages across invocations via checkpointing. Supports fork, clear, max_history trim. Persists to PostgreSQL and survives container restarts. Sessions can be restored from job records via `POST /api/jobs/{session_id}/restore`. Supports **configurable context summarization** via `SummarizationConfig`: when threads exceed a trigger threshold (message count, token count, or fraction of max_history), older messages are replaced with a single system summary, retaining the last N messages verbatim. Metrics: `conversation_summarization_total` counter, `conversation_tokens_saved` gauge. `core/conversation.py`
- **MemoryFilter** — Sanitizes session-scoped file paths (job dirs, tmp files, uploads, workspace) before persisting to conversation memory or cross-thread store. Replaces paths with `[session-file]` placeholder. Messages containing only session-file references are dropped. Integrated with `ConversationManager._save_thread()` and `InMemoryStore.aput()`. `core/memory_filter.py`
- **Hierarchical Namespace Helpers** (PR #81) — `core/store.path_to_namespace`, `namespace_to_path`, `descends_from`, `namespace_depth`, `NAMESPACE_SEP` (default `.`). `BaseStore` gains `aget_path(path, key)`, `aput_path(path, key, value)`, `asearch_path(path_prefix)` for dotted-path ergonomics.
- **Verbatim Checkpoint Log** (PR #81) — `Checkpoint` dataclass has optional `raw_log: str | None`. Persisted in `InMemoryCheckpointer`, `SQLiteCheckpointer`, and `PostgresCheckpointer` (new `raw_log TEXT` column, auto-migrated via `ADD COLUMN IF NOT EXISTS`). Opt-in — defaults to None to keep storage flat.

## Skills & Middleware

- **SkillMiddleware** — Composable interceptors on skill execution (retry, logging, timeout, cache). `core/skill.py`
- **Tool Description (`_description`)** — Optional `_description` parameter on every tool call. Extracted before execution (never forwarded to the skill), logged, propagated via `SkillRequest.metadata["tool_description"]`, included in `AuditEntry.tool_description`, and shown in dashboard tool-call events. Injected into `to_tool_definitions()` schemas so LLMs can explain why they invoke a tool.
- **LLM Cache** — Shared `InMemoryCache` for LLM node responses. Activated via `cache_policy` param on `llm_node()`. Skips cache when `temperature > 0`. Dashboard shows hits/misses/rate in real time.
- **Tool Cache** — `cache_middleware()` on `SkillRegistry` caches idempotent skills (`file_read`, `glob_search`). Auto-invalidates on `file_write`.
- **Progressive Skill Loading** — System prompts include only compact `SkillSummary` (name + description + category) instead of full instructions. Agents invoke `load_skill` to fetch detailed instructions on demand, reducing base prompt token usage. `skill_loads_total` counter tracks load frequency.
- **Verification Gate Middleware** (PR #59) — `core/skill.verification_middleware(validators, metrics=)`. Rejects skill results that fail the per-skill validator (returning `False` or `(False, reason)`). Converts failures into error `SkillResult`. Metrics: `verification_total`, `verification_pass_total`, `verification_fail_total`, `verification_duration_seconds` (all per-skill). See `docs/phase2.md`.
- **Context Loader Middleware** (PR #61) — `core/skill.context_loader_middleware(context_dir, target_skills=, metadata_key=, max_bytes=, metrics=)`. Reads `*.md` files from a directory, caches concatenated content, injects under `request.metadata[metadata_key]`. Skills opt-in by reading that key. Metrics: `context_files_loaded_total`, `context_bytes_injected` (per skill).

## Prompt Engineering

- **Marker-based Prompt Injection** (PR #57) — `core/prompt_markers.py`. `inject_marker_sections(base, {marker: content})` inserts/replaces named blocks delimited by `<!-- NAME START -->` / `<!-- NAME END -->` comments; idempotent, never mutates inputs. `Agent.set_prompt_section(marker, content)` updates instance state and increments counter `marker_updates_total{agent=<name>}`. `Agent.build_system_prompt()` returns the effective prompt with all markers applied — used at every provider call. See `tests/test_prompt_markers.py` and `docs/prompt-engineering.md`.
- **PromptRegistry** (PR #56) — `core/prompt_registry.py`. Tag-indexed, metadata-rich prompt template store backed by `BaseStore` (durable via `PostgresStore`). `PromptTemplate(name, content, tags, category, version, description, metadata)` dataclass with `.format(**kwargs)`. Namespace: `("prompt",)`. API: `register`, `get`, `delete`, `search(tags=, category=)` (AND-intersection on tags), `list_all`. Metrics: `prompt_registry_lookups_total`, `prompt_registry_hits_total`, `prompt_registry_misses_total`, `prompt_registry_lookup_duration_seconds`. REST endpoints at `/api/prompts*`. Wired at startup in `dashboard/app.py`. Frontend: dedicated **Prompts** floating-action panel. See `tests/test_prompt_registry.py`.
- **Compaction Metrics** (PR #60) — `ConversationManager` now accepts `metrics: MetricsRegistry`. Every `summarize_thread` call records counter `conversation_summarization_total`, gauge `conversation_tokens_saved` (cumulative), histogram `conversation_summarization_duration_seconds`, gauge `conversation_compaction_ratio` (last run), counter `conversation_messages_compacted_total`. Endpoint: `GET /api/compaction/stats`. Header widget **Tokens saved** in the dashboard surfaces the savings live. See `tests/test_compaction_metrics.py`.

## Reliability & Safety

- **ToolRecovery** — Detects dangling tool calls (assistant messages with `tool_calls` that have no matching `ToolMessage` response) and injects placeholder responses. Called automatically in `Agent.execute()` before each LLM call and in `ConversationManager._load_thread()` when restoring persisted threads. `core/tool_recovery.py`
- **LoopDetector** — Per-session sliding window loop detection for agent tool calls. Hashes tool_name+params (MD5), tracks in a `deque(maxlen=20)`. Warns at 3 repeats, hard stops at 5. LRU eviction at 500 sessions. Integrated into `Agent.execute()` via optional `loop_detector` + `session_id` params. Emits `loop.warning` / `loop.hard_stop` events; increments `loop_warnings_total` / `loop_hard_stops_total` counters.
- **Resilience** — `core/resilience.py`. `RetryPolicy` (exponential backoff with jitter, customizable `retryable` predicate) and `CircuitBreaker` (closed/open/half_open, threshold + cooldown). Combine via `resilient_call()`. Opt-in per-call wrapping — does not force behavior on existing providers. Non-retryable by default: `ValueError`, `TypeError`, `CircuitOpenError`.
- **SmokeTester** — `core/smoke_tester.py`. Detects a project's language (20 languages) from config files + conventional entry-point filenames, then runs a deps-free syntax check (`python -m py_compile`, `cargo check`, `node --check`, `go vet`, `bash -n`, `php -l`, `ruby -c`, etc.). Never raises — gracefully skips when the toolchain is missing (`shutil.which` guard). Wired into `run_team()` between the validation step and the summary step: a failure prepends a structured re-assignment (`{agent, task}`) so the broken entry point is fixed before summary is produced. Disable globally with `DISABLE_SMOKE_TEST=true`. Result exposed on the team-run return dict under `smoke_test`. See `tests/test_smoke_tester.py`.
- **Atomic Task Validator** (PR #59) — `core/atomic_tasks.validate_atomic_tasks(assignments)`. Lints team-lead decompositions for tasks that are too long, contain too many imperatives, or have multi-step conjunctions. Wired into `dashboard/agent_runner.run_team`; issues emit as `agent.step` events (no hard gate). Counter `tasks_rejected_too_complex_total`. See `tests/test_phase2.py`.
- **Modality Detection** (PR #88) — `core/modality.py`. `detect_modality(task_input)` returns one of `Modality.{TEXT, CODE, IMAGE, STRUCTURED, EQUATION, MIXED}`. Deterministic rule-based: detects PNG/JPEG/GIF/WebP magic bytes, dicts with image fields, LaTeX equations, code patterns (fenced blocks, function definitions, shebangs, Java/C signatures), structured tabular data. Priority: IMAGE > MIXED > STRUCTURED > EQUATION > CODE > TEXT. Counter `modality_detected_total{modality=...}` via `record_detection(modality, metrics)`. See `docs/phase3.md`.

## Configuration & Extensibility

- **ConfigManager** — Load/save/validate orchestrator configuration with rollback history. Supports YAML import/export via `import_yaml()`/`export_yaml()`. `core/config_manager.py`
- **YAMLConfigLoader** — YAML-based configuration with reflection class loading (`module:Class`), `${ENV_VAR}` substitution, config versioning with auto-upgrade, and validation. See `orchestrator.yaml.example`. `core/yaml_config.py`
- **ProjectManager** — Multi-project support with archive/unarchive and current project. `core/project.py`
- **UserManager** — Multi-user RBAC: admin, developer, viewer roles with API key auth. `core/users.py`
- **MigrationManager** — Import configs from LangGraph, CrewAI, AutoGen with auto-detection. `core/migration.py`
- **APIRegistry** — Versioned REST API (/api/v1/) with OpenAPI 3.0 spec export. `core/api.py`
- **PluginLoader** — Register/load plugin manifests (skills, providers) at runtime. `core/plugins.py`
- **WebhookRegistry** — Inbound webhooks with HMAC-SHA256 signature validation. `core/webhook.py`
- **OfflineManager** — Filter to local-only providers when offline. `core/offline.py`

## MCP (Model Context Protocol)

- **MCPServerRegistry** — Expose agents/skills as MCP tools and resources. `Orchestrator.register_mcp_tools()` bridges all agents and skills into the registry in one call. `core/mcp_server.py`
- **MCPClientManager** — Connect to external MCP servers (stdio or SSE transport). Aggregates their tools (prefixed `{server}/{tool}`) and proxies `call_tool`. `SkillRegistry.register_mcp_tools()` injects all discovered tools as local skills. `core/mcp_client.py`

## Sandbox

- **Sandbox** — Isolated execution environment (Docker or local). `SandboxConfig` controls image, timeout, memory/CPU limits, network, writable paths, `exposed_ports` (port forwarding), `startup_command`, and `env_vars`. `PortMapping` maps container ports to host ports (auto-assign or explicit). `SandboxInfo` provides runtime introspection (status, mapped ports, uptime). Virtual path mapping with traversal protection. `SandboxedShellSkill` wraps sandbox as a drop-in Skill for agent use. `core/sandbox.py`
- **SandboxManager** — Session-scoped sandbox lifecycle in dashboard. Lazy initialization on first use, per-session workspace isolation (`/workspace/{session_id}/`), configurable `max_concurrent` (default 10), LRU eviction, cleanup on shutdown. **Port allocation pool** (default range 9000-9099) prevents host-port collisions between sessions. `get_sandbox_info(session_id)` returns `SandboxInfo` with live container metadata. Enabled via `SANDBOX_ENABLED=true`. Env vars: `SANDBOX_TYPE` (docker/local), `SANDBOX_IMAGE`, `SANDBOX_TIMEOUT`, `SANDBOX_MEMORY`, `SANDBOX_MAX_CONCURRENT`. API: `GET /api/sandbox/status`, `GET /api/sandbox/{session_id}/info` (ports, status, uptime), `GET /api/sandbox/{session_id}/logs` (SSE log streaming), `DELETE /api/sandbox/{session_id}`, `WS /ws/sandbox/{session_id}/terminal` (interactive shell via xterm.js).
- **Sandbox Live Stats** (PR #81 follow-up) — `Sandbox.get_stats()` queries `docker stats --no-stream` and returns CPU %, memory bytes/limit/percent, net rx/tx. Endpoint `GET /api/sandbox/{session}/stats`. Frontend: live CPU/MEM sparklines (30 samples at 3 s) in the Sandbox **Status** tab.

## Integrations & I/O

- **DocumentConverter** — Converts uploaded files (PDF, Excel, CSV, Word, PowerPoint, HTML, text) to Markdown for LLM consumption. Graceful fallback when optional deps missing. Limits: 10 MB file size, 50 PDF pages, 10,000 spreadsheet rows. Upload via `POST /api/upload` (multipart/form-data). `core/document_converter.py`
- **ClarificationManager** — Structured agent-human clarification. 5 typed request categories (missing_info, ambiguous, approach, risk, suggestion). Blocking mode pauses agent until response or 5-minute timeout. Non-blocking mode emits event and continues. Events: `clarification.request`, `clarification.response`, `clarification.timeout`. `core/clarification.py`
- **TelegramBot** — Telegram integration using long-polling (no public IP required). Maps Telegram chats to conversation_ids and routes free-text to agents. Commands: `/start`, `/new`, `/status`, `/agents`, `/help`. Auth via `allowed_user_ids`. Install: `pip install agent-orchestrator[telegram]`. `integrations/telegram_bot.py`
- **SlackBot** — Slack integration via Socket Mode (no public IP). Maps Slack threads to orchestrator conversations (`slack-{channel}-{thread_ts}`). Handles `@bot` mentions, `/agent` and `/team` commands. Auto-detects task category for agent routing. Install: `pip install agent-orchestrator[slack]`. `integrations/slack_bot.py`

## Dashboard Composition

- **Modular Dashboard** — `app.py` is a composition root (~282 lines) that includes `gateway_api.py` (REST management) and `agent_runtime_router.py` (execution + streaming). Can run as single process or split via `--mode gateway|runtime`. Split mode: `docker-compose.split.yml` + `nginx-split.conf`. See `docs/dashboard.md`.
