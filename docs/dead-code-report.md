# Code Usage Report — Dual View

Analysis of all public definitions in `src/` (714 total):

- **Section A — Used in src/ only** (54): production code WITHOUT test coverage — needs tests.
- **Section B — Used in src/ AND tests/** (437): production code WITH test coverage — healthy.
- **Section C — Not used in src/** (223): dead code or scaffolding (may be used only in tests).

---

# A — Used in src/ only (no test coverage)

These definitions are used in production code but have **no references in tests/**. Priority candidates for adding test coverage.

**Total: 54**

| Category | Count | Breakdown |
|----------|-------|-----------|
| Core | 36 | 28 classes, 2 functions, 6 methods |
| Dashboard | 11 | 8 functions, 3 methods |
| Providers | 2 | 2 classes |
| Skills | 5 | 5 classes |

## Core

### `src/agent_orchestrator/core/agent.py`

- **L46** — class `TaskResult` (src: 23, tests: 0)

### `src/agent_orchestrator/core/alerts.py`

- **L31** — class `Alert` (src: 6, tests: 0)

### `src/agent_orchestrator/core/benchmark.py`

- **L13** — class `BenchmarkResult` (src: 9, tests: 0)

### `src/agent_orchestrator/core/cache.py`

- **L67** — class `BaseCache` (src: 8, tests: 0)
- **L192** — method `wrapper` (src: 12, tests: 0)

### `src/agent_orchestrator/core/channels.py`

- **L28** — class `BaseChannel` (src: 15, tests: 0)
- **L253** — method `get_channel` (src: 3, tests: 0)

### `src/agent_orchestrator/core/checkpoint.py`

- **L19** — class `Checkpoint` (src: 45, tests: 0)
- **L28** — class `Checkpointer` (src: 17, tests: 0)

### `src/agent_orchestrator/core/checkpoint_postgres.py`

- **L19** — class `PostgresCheckpointer` (src: 5, tests: 0)

### `src/agent_orchestrator/core/conformance.py`

- **L103** — function `run_provider_conformance` (src: 4, tests: 0)

### `src/agent_orchestrator/core/conversation.py`

- **L56** — class `ConversationResult` (src: 5, tests: 0)

### `src/agent_orchestrator/core/cooperation.py`

- **L12** — class `Priority` (src: 2, tests: 0)
- **L69** — class `ConflictRecord` (src: 3, tests: 0)

### `src/agent_orchestrator/core/graph.py`

- **L35** — class `EdgeType` (src: 8, tests: 0)
- **L41** — class `Edge` (src: 7, tests: 0)
- **L50** — class `NodeConfig` (src: 4, tests: 0)
- **L89** — class `GraphResult` (src: 14, tests: 0)

### `src/agent_orchestrator/core/health.py`

- **L11** — class `ProviderHealth` (src: 9, tests: 0)

### `src/agent_orchestrator/core/llm_nodes.py`

- **L83** — method `node_func` (src: 16, tests: 0)
- **L162** — method `node_func` (src: 16, tests: 0)
- **L238** — method `node_func` (src: 16, tests: 0)

### `src/agent_orchestrator/core/migration.py`

- **L10** — class `MigrationResult` (src: 12, tests: 0)

### `src/agent_orchestrator/core/orchestrator.py`

- **L50** — class `OrchestratorResult` (src: 9, tests: 0)

### `src/agent_orchestrator/core/provider.py`

- **L27** — class `ToolCall` (src: 7, tests: 0)
- **L34** — class `ToolDefinition` (src: 22, tests: 0)
- **L123** — method `estimate_cost` (src: 6, tests: 0)

### `src/agent_orchestrator/core/rate_limiter.py`

- **L17** — class `RateLimitStatus` (src: 3, tests: 0)
- **L26** — class `_ProviderState` (src: 6, tests: 0)

### `src/agent_orchestrator/core/reducers.py`

- **L37** — function `merge_dict_reducer` (src: 2, tests: 0)

### `src/agent_orchestrator/core/store.py`

- **L32** — class `Item` (src: 10, tests: 0)
- **L90** — class `BaseStore` (src: 6, tests: 0)

### `src/agent_orchestrator/core/task_queue.py`

- **L27** — class `QueueStats` (src: 2, tests: 0)

### `src/agent_orchestrator/core/usage.py`

- **L30** — class `BudgetStatus` (src: 5, tests: 0)
- **L37** — class `CostBreakdown` (src: 2, tests: 0)

### `src/agent_orchestrator/core/webhook.py`

- **L25** — class `WebhookEvent` (src: 4, tests: 0)

## Dashboard

### `src/agent_orchestrator/dashboard/agent_runner.py`

- **L35** — function `create_skill_registry` (src: 4, tests: 0)
- **L69** — function `run_agent` (src: 3, tests: 0)

### `src/agent_orchestrator/dashboard/app.py`

- **L540** — method `cache_stats` (src: 4, tests: 0)

### `src/agent_orchestrator/dashboard/auth.py`

- **L121** — function `get_base_url` (src: 3, tests: 0)

### `src/agent_orchestrator/dashboard/graphs.py`

- **L122** — function `list_ollama_models` (src: 2, tests: 0)
- **L176** — function `run_graph` (src: 2, tests: 0)
- **L666** — method `wrapper` (src: 12, tests: 0)

### `src/agent_orchestrator/dashboard/instrument.py`

- **L21** — function `instrument_all` (src: 4, tests: 0)

### `src/agent_orchestrator/dashboard/job_logger.py`

- **L201** — method `load_session` (src: 2, tests: 0)

### `src/agent_orchestrator/dashboard/user_store.py`

- **L78** — function `setup_db` (src: 2, tests: 0)
- **L236** — function `async_get_or_create_user` (src: 3, tests: 0)

## Providers

### `src/agent_orchestrator/providers/local.py`

- **L9** — class `LocalProvider` (src: 4, tests: 0)

### `src/agent_orchestrator/providers/openai.py`

- **L52** — class `OpenAIProvider` (src: 4, tests: 0)

## Skills

### `src/agent_orchestrator/skills/doc_sync.py`

- **L12** — class `DocSyncSkill` (src: 2, tests: 0)

### `src/agent_orchestrator/skills/filesystem.py`

- **L11** — class `FileReadSkill` (src: 4, tests: 0)
- **L45** — class `FileWriteSkill` (src: 4, tests: 0)
- **L77** — class `GlobSkill` (src: 4, tests: 0)

### `src/agent_orchestrator/skills/shell.py`

- **L11** — class `ShellExecSkill` (src: 4, tests: 0)

---

# B — Used in src/ AND tests/ (covered)

These definitions are used in production AND have test coverage. This is the healthy, well-connected code.

**Total: 437**

| Category | Count | Breakdown |
|----------|-------|-----------|
| Core | 327 | 106 classes, 20 functions, 201 methods |
| Dashboard | 52 | 6 classes, 20 functions, 26 methods |
| Providers | 26 | 1 classes, 25 methods |
| Skills | 32 | 32 methods |

## Core

### `src/agent_orchestrator/core/agent.py`

- **L17** — class `TaskStatus` (src: 29, tests: 10)
- **L27** — class `AgentConfig` (src: 9, tests: 20)
- **L39** — class `Task` (src: 18, tests: 14)
- **L58** — class `Agent` (src: 46, tests: 25)
- **L74** — method `execute` (src: 70, tests: 54)

### `src/agent_orchestrator/core/alerts.py`

- **L22** — class `AlertRule` (src: 5, tests: 12)
- **L39** — class `AlertManager` (src: 2, tests: 12)
- **L53** — method `check` (src: 17, tests: 22)

### `src/agent_orchestrator/core/api.py`

- **L18** — class `HTTPMethod` (src: 28, tests: 8)
- **L27** — class `APIEndpoint` (src: 32, tests: 2)
- **L42** — class `APIResponse` (src: 2, tests: 2)
- **L51** — class `APIRegistry` (src: 2, tests: 12)
- **L61** — method `register` (src: 43, tests: 50)

### `src/agent_orchestrator/core/audit.py`

- **L28** — class `AuditEntry` (src: 11, tests: 2)
- **L40** — class `AuditLog` (src: 2, tests: 12)
- **L50** — method `log` (src: 22, tests: 64)
- **L140** — method `clear` (src: 15, tests: 9)

### `src/agent_orchestrator/core/benchmark.py`

- **L23** — class `BenchmarkSuite` (src: 3, tests: 8)
- **L36** — method `run_benchmark` (src: 2, tests: 6)

### `src/agent_orchestrator/core/cache.py`

- **L18** — class `CachePolicy` (src: 14, tests: 10)
- **L28** — class `CacheEntry` (src: 6, tests: 4)
- **L43** — class `CacheStats` (src: 5, tests: 5)
- **L53** — method `hit_rate` (src: 2, tests: 3)
- **L57** — method `to_dict` (src: 12, tests: 6)
- **L71** — method `get` (src: 551, tests: 125)
- **L76** — method `put` (src: 8, tests: 29)
- **L81** — method `invalidate` (src: 4, tests: 2)
- **L86** — method `clear` (src: 15, tests: 9)
- **L91** — method `size` (src: 32, tests: 5)
- **L96** — method `get_stats` (src: 5, tests: 9)
- **L101** — class `InMemoryCache` (src: 13, tests: 29)
- **L109** — method `get` (src: 551, tests: 125)
- **L123** — method `put` (src: 8, tests: 29)
- **L134** — method `invalidate` (src: 4, tests: 2)
- **L140** — method `clear` (src: 15, tests: 9)
- **L145** — method `size` (src: 32, tests: 5)
- **L148** — method `get_stats` (src: 5, tests: 9)
- **L159** — function `make_cache_key` (src: 9, tests: 9)
- **L175** — function `cached_node` (src: 6, tests: 8)

### `src/agent_orchestrator/core/channels.py`

- **L16** — class `EmptyChannelError` (src: 6, tests: 3)
- **L22** — class `InvalidUpdateError` (src: 3, tests: 2)
- **L32** — method `get` (src: 551, tests: 125)
- **L37** — method `update` (src: 67, tests: 29)
- **L42** — method `is_available` (src: 14, tests: 15)
- **L47** — method `checkpoint` (src: 58, tests: 13)
- **L52** — method `from_checkpoint` (src: 6, tests: 5)
- **L56** — method `reset` (src: 14, tests: 10)
- **L64** — class `LastValue` (src: 6, tests: 15)
- **L71** — method `get` (src: 551, tests: 125)
- **L76** — method `update` (src: 67, tests: 29)
- **L87** — method `is_available` (src: 14, tests: 15)
- **L90** — method `checkpoint` (src: 58, tests: 13)
- **L93** — method `from_checkpoint` (src: 6, tests: 5)
- **L97** — class `BinaryOperatorChannel` (src: 6, tests: 9)
- **L108** — method `get` (src: 551, tests: 125)
- **L113** — method `update` (src: 67, tests: 29)
- **L125** — method `is_available` (src: 14, tests: 15)
- **L128** — method `checkpoint` (src: 58, tests: 13)
- **L131** — method `from_checkpoint` (src: 6, tests: 5)
- **L135** — class `TopicChannel` (src: 2, tests: 11)
- **L146** — method `get` (src: 551, tests: 125)
- **L149** — method `update` (src: 67, tests: 29)
- **L155** — method `is_available` (src: 14, tests: 15)
- **L158** — method `reset` (src: 14, tests: 10)
- **L162** — method `checkpoint` (src: 58, tests: 13)
- **L165** — method `from_checkpoint` (src: 6, tests: 5)
- **L169** — class `EphemeralChannel` (src: 3, tests: 6)
- **L178** — method `get` (src: 551, tests: 125)
- **L183** — method `update` (src: 67, tests: 29)
- **L189** — method `is_available` (src: 14, tests: 15)
- **L192** — method `reset` (src: 14, tests: 10)
- **L195** — method `checkpoint` (src: 58, tests: 13)
- **L198** — method `from_checkpoint` (src: 6, tests: 5)
- **L202** — class `BarrierChannel` (src: 2, tests: 6)
- **L212** — method `get` (src: 551, tests: 125)
- **L215** — method `update` (src: 67, tests: 29)
- **L220** — method `is_available` (src: 14, tests: 15)
- **L223** — method `reset` (src: 14, tests: 10)
- **L226** — method `checkpoint` (src: 58, tests: 13)
- **L229** — method `from_checkpoint` (src: 6, tests: 5)
- **L241** — class `ChannelManager` (src: 7, tests: 8)
- **L250** — method `register` (src: 43, tests: 50)
- **L282** — method `checkpoint` (src: 58, tests: 13)
- **L286** — method `restore` (src: 2, tests: 3)
- **L294** — method `channels` (src: 14, tests: 1)

### `src/agent_orchestrator/core/checkpoint.py`

- **L32** — method `save` (src: 29, tests: 15)
- **L35** — method `get` (src: 551, tests: 125)
- **L38** — method `get_latest` (src: 7, tests: 1)
- **L41** — method `list_thread` (src: 8, tests: 1)
- **L44** — class `InMemoryCheckpointer` (src: 10, tests: 16)
- **L51** — method `save` (src: 29, tests: 15)
- **L56** — method `get` (src: 551, tests: 125)
- **L59** — method `get_latest` (src: 7, tests: 1)
- **L65** — method `list_thread` (src: 8, tests: 1)
- **L70** — class `SQLiteCheckpointer` (src: 3, tests: 6)
- **L97** — method `save` (src: 29, tests: 15)
- **L116** — method `get` (src: 551, tests: 125)
- **L124** — method `get_latest` (src: 7, tests: 1)
- **L132** — method `list_thread` (src: 8, tests: 1)
- **L150** — method `close` (src: 17, tests: 15)

### `src/agent_orchestrator/core/checkpoint_postgres.py`

- **L39** — method `setup` (src: 9, tests: 4)
- **L59** — method `save` (src: 29, tests: 15)
- **L79** — method `get` (src: 551, tests: 125)
- **L88** — method `get_latest` (src: 7, tests: 1)
- **L98** — method `list_thread` (src: 8, tests: 1)
- **L139** — method `close` (src: 17, tests: 15)

### `src/agent_orchestrator/core/config_manager.py`

- **L12** — class `AgentConfigEntry` (src: 6, tests: 8)
- **L24** — class `ProviderConfigEntry` (src: 6, tests: 6)
- **L36** — class `OrchestratorConfiguration` (src: 14, tests: 7)
- **L50** — class `ConfigManager` (src: 3, tests: 16)
- **L62** — method `config` (src: 129, tests: 81)
- **L65** — method `update` (src: 67, tests: 29)
- **L79** — method `get_history` (src: 10, tests: 43)
- **L83** — method `validate` (src: 4, tests: 6)

### `src/agent_orchestrator/core/conformance.py`

- **L26** — class `TestStatus` (src: 9, tests: 9)
- **L33** — class `TestResult` (src: 6, tests: 8)
- **L41** — class `ConformanceReport` (src: 9, tests: 5)
- **L47** — method `passed` (src: 8, tests: 3)
- **L51** — method `failed` (src: 41, tests: 13)
- **L55** — method `skipped` (src: 3, tests: 2)
- **L62** — method `summary` (src: 51, tests: 19)
- **L69** — method `to_dict` (src: 12, tests: 6)
- **L207** — function `run_checkpointer_conformance` (src: 4, tests: 2)
- **L233** — method `test_get_nonexistent` (src: 3, tests: 2)

### `src/agent_orchestrator/core/conversation.py`

- **L29** — class `ConversationMessage` (src: 16, tests: 16)
- **L37** — method `to_dict` (src: 12, tests: 6)
- **L68** — class `ConversationManager` (src: 13, tests: 33)
- **L90** — method `send` (src: 7, tests: 44)
- **L151** — method `get_history` (src: 10, tests: 43)

### `src/agent_orchestrator/core/cooperation.py`

- **L20** — class `TaskAssignment` (src: 11, tests: 23)
- **L33** — class `TaskReport` (src: 10, tests: 10)
- **L45** — class `Artifact` (src: 10, tests: 18)
- **L57** — class `AgentMessage` (src: 10, tests: 3)
- **L91** — method `publish` (src: 2, tests: 16)
- **L129** — method `report` (src: 35, tests: 23)
- **L139** — method `send_message` (src: 2, tests: 2)
- **L169** — method `get_conflicts` (src: 2, tests: 5)
- **L183** — class `CooperationProtocol` (src: 11, tests: 20)
- **L192** — method `assign` (src: 2, tests: 25)
- **L198** — method `complete` (src: 36, tests: 22)

### `src/agent_orchestrator/core/graph.py`

- **L57** — class `GraphConfig` (src: 5, tests: 6)
- **L63** — class `InterruptType` (src: 3, tests: 9)
- **L70** — class `Interrupt` (src: 5, tests: 9)
- **L80** — class `GraphInterrupt` (src: 5, tests: 8)
- **L98** — class `StepRecord` (src: 7, tests: 1)
- **L106** — class `StreamEventType` (src: 16, tests: 18)
- **L117** — class `StreamEvent` (src: 16, tests: 8)
- **L137** — class `StateGraph` (src: 33, tests: 57)
- **L165** — method `add_node` (src: 20, tests: 73)
- **L173** — method `add_edge` (src: 26, tests: 122)
- **L177** — method `add_conditional_edges` (src: 3, tests: 3)
- **L194** — method `compile` (src: 8, tests: 64)
- **L263** — class `CompiledGraph` (src: 13, tests: 11)
- **L282** — method `invoke` (src: 7, tests: 51)
- **L383** — method `astream` (src: 2, tests: 11)
- **L794** — method `get_graph_info` (src: 2, tests: 6)

### `src/agent_orchestrator/core/graph_patterns.py`

- **L22** — class `SubGraphNode` (src: 7, tests: 5)
- **L76** — function `retry_node` (src: 3, tests: 6)
- **L92** — method `wrapped` (src: 12, tests: 22)
- **L129** — function `loop_node` (src: 3, tests: 7)
- **L141** — method `wrapped` (src: 12, tests: 22)
- **L168** — function `map_reduce_node` (src: 3, tests: 6)
- **L184** — method `wrapped` (src: 12, tests: 22)
- **L216** — function `provider_annotated_node` (src: 4, tests: 7)
- **L267** — method `wrapped` (src: 12, tests: 22)
- **L278** — function `long_context_node` (src: 4, tests: 5)
- **L301** — method `wrapped` (src: 12, tests: 22)

### `src/agent_orchestrator/core/graph_templates.py`

- **L59** — class `NodeTemplate` (src: 8, tests: 4)
- **L81** — class `EdgeTemplate` (src: 10, tests: 6)
- **L97** — class `GraphTemplate` (src: 18, tests: 3)
- **L114** — class `GraphTemplateStore` (src: 6, tests: 15)
- **L131** — method `save` (src: 29, tests: 15)
- **L150** — method `get` (src: 551, tests: 125)
- **L179** — method `delete` (src: 18, tests: 8)
- **L190** — method `export_dict` (src: 2, tests: 1)
- **L197** — method `import_dict` (src: 2, tests: 1)
- **L204** — method `to_json` (src: 2, tests: 1)
- **L208** — method `from_json` (src: 2, tests: 1)
- **L238** — method `build_graph` (src: 4, tests: 4)
- **L291** — method `router` (src: 25, tests: 35)

### `src/agent_orchestrator/core/health.py`

- **L22** — class `HealthMonitor` (src: 6, tests: 14)
- **L72** — method `record_error` (src: 2, tests: 15)
- **L101** — method `is_available` (src: 14, tests: 15)
- **L105** — method `get_best_provider` (src: 5, tests: 3)

### `src/agent_orchestrator/core/llm_nodes.py`

- **L48** — function `get_llm_cache` (src: 6, tests: 3)
- **L53** — function `llm_node` (src: 22, tests: 19)
- **L145** — function `multi_provider_node` (src: 4, tests: 7)
- **L224** — function `chat_node` (src: 2, tests: 3)

### `src/agent_orchestrator/core/mcp_server.py`

- **L15** — class `MCPTool` (src: 10, tests: 4)
- **L23** — class `MCPResource` (src: 6, tests: 4)
- **L30** — class `MCPServerRegistry` (src: 3, tests: 9)
- **L51** — method `register_tool` (src: 2, tests: 3)

### `src/agent_orchestrator/core/metrics.py`

- **L15** — class `Counter` (src: 10, tests: 4)
- **L29** — method `get` (src: 551, tests: 125)
- **L32** — method `reset` (src: 14, tests: 10)
- **L36** — class `Gauge` (src: 10, tests: 2)
- **L45** — method `set` (src: 43, tests: 26)
- **L54** — method `get` (src: 551, tests: 125)
- **L58** — class `Histogram` (src: 9, tests: 4)
- **L72** — method `get_count` (src: 2, tests: 2)
- **L75** — method `get_sum` (src: 2, tests: 1)
- **L82** — method `get_percentile` (src: 3, tests: 4)
- **L106** — class `MetricsRegistry` (src: 4, tests: 6)
- **L112** — method `counter` (src: 24, tests: 41)
- **L126** — method `gauge` (src: 6, tests: 2)
- **L140** — method `histogram` (src: 4, tests: 1)
- **L215** — function `default_metrics` (src: 2, tests: 3)

### `src/agent_orchestrator/core/migration.py`

- **L23** — class `MigrationManager` (src: 2, tests: 16)

### `src/agent_orchestrator/core/offline.py`

- **L10** — class `OfflineConfig` (src: 4, tests: 5)
- **L16** — class `OfflineManager` (src: 2, tests: 10)
- **L62** — method `get_status` (src: 2, tests: 6)

### `src/agent_orchestrator/core/orchestrator.py`

- **L25** — class `RoutingStrategy` (src: 12, tests: 11)
- **L33** — class `TaskComplexity` (src: 10, tests: 7)
- **L64** — class `Orchestrator` (src: 16, tests: 12)
- **L97** — method `run` (src: 31, tests: 67)

### `src/agent_orchestrator/core/plugins.py`

- **L10** — class `PluginManifest` (src: 8, tests: 10)
- **L20** — class `PluginLoader` (src: 2, tests: 11)
- **L38** — method `register` (src: 43, tests: 50)
- **L101** — method `to_dict` (src: 12, tests: 6)

### `src/agent_orchestrator/core/project.py`

- **L11** — class `ProjectConfig` (src: 8, tests: 20)
- **L23** — class `ProjectManager` (src: 2, tests: 16)
- **L34** — method `create` (src: 16, tests: 24)
- **L45** — method `get` (src: 551, tests: 125)
- **L56** — method `update` (src: 67, tests: 29)
- **L63** — method `delete` (src: 18, tests: 8)
- **L79** — method `current` (src: 60, tests: 14)
- **L106** — method `get_status` (src: 2, tests: 6)

### `src/agent_orchestrator/core/provider.py`

- **L11** — class `Role` (src: 34, tests: 6)
- **L19** — class `Message` (src: 57, tests: 7)
- **L41** — class `ModelCapabilities` (src: 18, tests: 26)
- **L52** — class `Usage` (src: 21, tests: 17)
- **L59** — class `Completion` (src: 17, tests: 18)
- **L67** — class `StreamChunk` (src: 11, tests: 16)
- **L73** — class `Provider` (src: 84, tests: 27)
- **L77** — method `complete` (src: 36, tests: 22)
- **L89** — method `stream` (src: 27, tests: 10)
- **L101** — method `model_id` (src: 35, tests: 30)
- **L107** — method `capabilities` (src: 22, tests: 13)
- **L113** — method `input_cost_per_million` (src: 7, tests: 8)
- **L119** — method `output_cost_per_million` (src: 12, tests: 10)

### `src/agent_orchestrator/core/provider_presets.py`

- **L10** — class `ProviderPresetEntry` (src: 7, tests: 2)
- **L22** — class `ProviderPreset` (src: 13, tests: 3)
- **L110** — class `ProviderPresetManager` (src: 2, tests: 15)
- **L125** — method `get` (src: 551, tests: 125)
- **L137** — method `remove` (src: 6, tests: 2)
- **L148** — method `activate` (src: 3, tests: 5)
- **L157** — method `active` (src: 40, tests: 15)

### `src/agent_orchestrator/core/rate_limiter.py`

- **L10** — class `RateLimitConfig` (src: 4, tests: 8)
- **L33** — class `RateLimiter` (src: 2, tests: 11)
- **L48** — method `acquire` (src: 11, tests: 12)
- **L83** — method `get_status` (src: 2, tests: 6)
- **L120** — method `reset` (src: 14, tests: 10)

### `src/agent_orchestrator/core/reducers.py`

- **L19** — function `append_reducer` (src: 4, tests: 8)
- **L27** — function `add_reducer` (src: 3, tests: 4)

### `src/agent_orchestrator/core/router.py`

- **L108** — class `TaskComplexityClassifier` (src: 4, tests: 4)
- **L117** — method `classify` (src: 10, tests: 7)
- **L156** — class `RoutingStrategy` (src: 12, tests: 11)
- **L179** — class `RouterConfig` (src: 4, tests: 11)
- **L190** — class `TaskRouter` (src: 3, tests: 13)
- **L215** — method `route` (src: 6, tests: 13)

### `src/agent_orchestrator/core/skill.py`

- **L18** — class `SkillResult` (src: 75, tests: 16)
- **L30** — class `SkillRequest` (src: 18, tests: 6)
- **L56** — class `Skill` (src: 29, tests: 13)
- **L61** — method `name` (src: 402, tests: 182)
- **L65** — method `description` (src: 97, tests: 106)
- **L69** — method `parameters` (src: 17, tests: 8)
- **L74** — method `execute` (src: 70, tests: 54)
- **L77** — class `SkillRegistry` (src: 10, tests: 37)
- **L84** — method `register` (src: 43, tests: 50)
- **L87** — method `get` (src: 551, tests: 125)
- **L90** — method `use` (src: 19, tests: 31)
- **L98** — method `execute` (src: 70, tests: 54)
- **L145** — method `wrapped` (src: 12, tests: 22)
- **L154** — function `logging_middleware` (src: 2, tests: 4)
- **L161** — method `middleware` (src: 26, tests: 16)
- **L178** — function `retry_middleware` (src: 2, tests: 5)
- **L181** — method `middleware` (src: 26, tests: 16)
- **L196** — function `timeout_middleware` (src: 2, tests: 3)
- **L201** — method `middleware` (src: 26, tests: 16)
- **L217** — function `cache_middleware` (src: 4, tests: 7)
- **L237** — method `middleware` (src: 26, tests: 16)

### `src/agent_orchestrator/core/store.py`

- **L43** — class `SearchItem` (src: 7, tests: 2)
- **L98** — method `aget` (src: 13, tests: 12)
- **L103** — method `aput` (src: 29, tests: 21)
- **L115** — method `adelete` (src: 9, tests: 2)
- **L120** — method `asearch` (src: 14, tests: 8)
- **L132** — method `alist_namespaces` (src: 3, tests: 2)
- **L145** — method `get` (src: 551, tests: 125)
- **L157** — method `put` (src: 8, tests: 29)
- **L176** — method `delete` (src: 18, tests: 8)
- **L192** — class `InMemoryStore` (src: 5, tests: 16)
- **L214** — method `aget` (src: 13, tests: 12)
- **L219** — method `aput` (src: 29, tests: 21)
- **L243** — method `adelete` (src: 9, tests: 2)
- **L248** — method `asearch` (src: 14, tests: 8)
- **L291** — method `alist_namespaces` (src: 3, tests: 2)
- **L329** — class `SessionStore` (src: 7, tests: 16)
- **L367** — method `session_id` (src: 66, tests: 38)
- **L371** — method `namespace` (src: 36, tests: 2)
- **L383** — method `get` (src: 551, tests: 125)
- **L388** — method `put` (src: 8, tests: 29)
- **L400** — method `delete` (src: 18, tests: 8)
- **L406** — method `search` (src: 8, tests: 2)
- **L417** — method `close` (src: 17, tests: 15)
- **L449** — function `run_store_conformance` (src: 2, tests: 2)
- **L470** — method `test_get_nonexistent` (src: 3, tests: 2)

### `src/agent_orchestrator/core/task_queue.py`

- **L11** — class `QueuedTask` (src: 8, tests: 18)
- **L35** — class `TaskQueue` (src: 2, tests: 15)
- **L77** — method `complete` (src: 36, tests: 22)
- **L86** — method `fail` (src: 2, tests: 18)
- **L106** — method `retry` (src: 10, tests: 4)
- **L130** — method `get_stats` (src: 5, tests: 9)

### `src/agent_orchestrator/core/usage.py`

- **L11** — class `UsageRecord` (src: 5, tests: 15)
- **L23** — class `BudgetConfig` (src: 3, tests: 4)
- **L55** — class `UsageTracker` (src: 2, tests: 11)
- **L69** — method `record` (src: 16, tests: 29)
- **L133** — method `get_session_cost` (src: 2, tests: 1)
- **L137** — method `get_daily_cost` (src: 2, tests: 1)

### `src/agent_orchestrator/core/users.py`

- **L21** — class `UserRole` (src: 14, tests: 11)
- **L62** — class `User` (src: 21, tests: 3)
- **L75** — class `UserManager` (src: 2, tests: 22)
- **L141** — method `list_users` (src: 3, tests: 7)
- **L164** — method `activate` (src: 3, tests: 5)

### `src/agent_orchestrator/core/webhook.py`

- **L13** — class `WebhookConfig` (src: 7, tests: 14)
- **L34** — class `WebhookRegistry` (src: 2, tests: 15)
- **L45** — method `register` (src: 43, tests: 50)
- **L56** — method `get` (src: 551, tests: 125)

## Dashboard

### `src/agent_orchestrator/dashboard/__init__.py`

- **L6** — function `create_dashboard_app` (src: 5, tests: 1)

### `src/agent_orchestrator/dashboard/agent_runner.py`

- **L30** — function `get_tool_cache` (src: 4, tests: 3)
- **L722** — function `run_team` (src: 2, tests: 28)

### `src/agent_orchestrator/dashboard/agents_registry.py`

- **L85** — function `get_agent_registry` (src: 6, tests: 26)

### `src/agent_orchestrator/dashboard/app.py`

- **L94** — function `create_dashboard_app` (src: 5, tests: 1)
- **L195** — method `index` (src: 13, tests: 3)
- **L200** — method `session` (src: 71, tests: 69)
- **L579** — method `events` (src: 38, tests: 42)
- **L584** — method `agents` (src: 133, tests: 87)
- **L926** — method `models` (src: 37, tests: 19)
- **L1101** — method `get_conversation` (src: 2, tests: 1)
- **L1181** — method `prompt` (src: 61, tests: 29)

### `src/agent_orchestrator/dashboard/auth.py`

- **L56** — function `create_session_token` (src: 2, tests: 4)
- **L75** — function `verify_session_token` (src: 6, tests: 6)
- **L93** — function `create_oauth` (src: 3, tests: 3)
- **L131** — class `APIKeyMiddleware` (src: 3, tests: 11)
- **L204** — function `check_ws_auth` (src: 3, tests: 5)

### `src/agent_orchestrator/dashboard/events.py`

- **L16** — class `EventType` (src: 84, tests: 66)
- **L60** — class `Event` (src: 81, tests: 34)
- **L67** — method `to_dict` (src: 12, tests: 6)
- **L73** — class `EventBus` (src: 35, tests: 56)
- **L84** — method `get` (src: 551, tests: 125)
- **L90** — method `reset` (src: 14, tests: 10)
- **L93** — method `emit` (src: 65, tests: 35)
- **L111** — method `get_history` (src: 10, tests: 43)
- **L114** — method `get_snapshot` (src: 3, tests: 16)

### `src/agent_orchestrator/dashboard/graphs.py`

- **L31** — function `list_openrouter_models` (src: 2, tests: 9)
- **L298** — function `replay_node` (src: 2, tests: 7)
- **L397** — function `get_last_run_info` (src: 2, tests: 3)
- **L611** — method `route` (src: 6, tests: 13)

### `src/agent_orchestrator/dashboard/job_logger.py`

- **L19** — class `JobLogger` (src: 2, tests: 36)
- **L114** — method `touch` (src: 4, tests: 4)
- **L120** — method `session_id` (src: 66, tests: 38)
- **L124** — method `session_dir` (src: 26, tests: 74)
- **L129** — method `log` (src: 22, tests: 64)
- **L149** — method `get_history` (src: 10, tests: 43)

### `src/agent_orchestrator/dashboard/server.py`

- **L57** — function `main` (src: 5, tests: 37)

### `src/agent_orchestrator/dashboard/usage_db.py`

- **L19** — class `UsageDB` (src: 2, tests: 13)
- **L77** — method `setup` (src: 9, tests: 4)
- **L215** — method `record` (src: 16, tests: 29)
- **L292** — method `get_per_model` (src: 2, tests: 1)
- **L296** — method `get_per_agent` (src: 2, tests: 1)
- **L317** — method `record_error` (src: 2, tests: 15)
- **L397** — method `append_message` (src: 4, tests: 1)
- **L413** — method `get_conversation` (src: 2, tests: 1)

### `src/agent_orchestrator/dashboard/user_store.py`

- **L380** — function `list_users` (src: 3, tests: 7)
- **L410** — function `approve_user` (src: 2, tests: 7)
- **L477** — function `update_user_role` (src: 2, tests: 3)
- **L517** — function `deactivate_user` (src: 2, tests: 2)
- **L621** — function `list_pending` (src: 2, tests: 7)
- **L646** — function `approve_pending` (src: 2, tests: 3)
- **L692** — function `reject_pending` (src: 2, tests: 3)

## Providers

### `src/agent_orchestrator/providers/anthropic.py`

- **L62** — method `complete` (src: 36, tests: 22)
- **L109** — method `stream` (src: 27, tests: 10)
- **L135** — method `model_id` (src: 35, tests: 30)
- **L139** — method `capabilities` (src: 22, tests: 13)
- **L151** — method `input_cost_per_million` (src: 7, tests: 8)
- **L155** — method `output_cost_per_million` (src: 12, tests: 10)

### `src/agent_orchestrator/providers/google.py`

- **L53** — method `complete` (src: 36, tests: 22)
- **L66** — method `stream` (src: 27, tests: 10)
- **L77** — method `model_id` (src: 35, tests: 30)
- **L81** — method `capabilities` (src: 22, tests: 13)
- **L93** — method `input_cost_per_million` (src: 7, tests: 8)
- **L97** — method `output_cost_per_million` (src: 12, tests: 10)

### `src/agent_orchestrator/providers/local.py`

- **L35** — method `capabilities` (src: 22, tests: 13)
- **L46** — method `input_cost_per_million` (src: 7, tests: 8)
- **L50** — method `output_cost_per_million` (src: 12, tests: 10)

### `src/agent_orchestrator/providers/openai.py`

- **L93** — method `complete` (src: 36, tests: 22)
- **L148** — method `stream` (src: 27, tests: 10)
- **L173** — method `model_id` (src: 35, tests: 30)
- **L177** — method `capabilities` (src: 22, tests: 13)
- **L189** — method `input_cost_per_million` (src: 7, tests: 8)
- **L193** — method `output_cost_per_million` (src: 12, tests: 10)

### `src/agent_orchestrator/providers/openrouter.py`

- **L26** — class `OpenRouterProvider` (src: 5, tests: 11)
- **L195** — method `capabilities` (src: 22, tests: 13)
- **L208** — method `input_cost_per_million` (src: 7, tests: 8)
- **L213** — method `output_cost_per_million` (src: 12, tests: 10)
- **L231** — method `complete` (src: 36, tests: 22)

## Skills

### `src/agent_orchestrator/skills/doc_sync.py`

- **L23** — method `name` (src: 402, tests: 182)
- **L27** — method `description` (src: 97, tests: 106)
- **L35** — method `parameters` (src: 17, tests: 8)
- **L53** — method `execute` (src: 70, tests: 54)

### `src/agent_orchestrator/skills/filesystem.py`

- **L16** — method `name` (src: 402, tests: 182)
- **L20** — method `description` (src: 97, tests: 106)
- **L24** — method `parameters` (src: 17, tests: 8)
- **L33** — method `execute` (src: 70, tests: 54)
- **L50** — method `name` (src: 402, tests: 182)
- **L54** — method `description` (src: 97, tests: 106)
- **L58** — method `parameters` (src: 17, tests: 8)
- **L68** — method `execute` (src: 70, tests: 54)
- **L82** — method `name` (src: 402, tests: 182)
- **L86** — method `description` (src: 97, tests: 106)
- **L90** — method `parameters` (src: 17, tests: 8)
- **L100** — method `execute` (src: 70, tests: 54)

### `src/agent_orchestrator/skills/github_skill.py`

- **L15** — method `name` (src: 402, tests: 182)
- **L19** — method `description` (src: 97, tests: 106)
- **L23** — method `parameters` (src: 17, tests: 8)
- **L48** — method `execute` (src: 70, tests: 54)

### `src/agent_orchestrator/skills/shell.py`

- **L23** — method `name` (src: 402, tests: 182)
- **L27** — method `description` (src: 97, tests: 106)
- **L31** — method `parameters` (src: 17, tests: 8)
- **L40** — method `execute` (src: 70, tests: 54)

### `src/agent_orchestrator/skills/web_reader.py`

- **L57** — method `name` (src: 402, tests: 182)
- **L61** — method `description` (src: 97, tests: 106)
- **L65** — method `parameters` (src: 17, tests: 8)
- **L78** — method `execute` (src: 70, tests: 54)

### `src/agent_orchestrator/skills/webhook_skill.py`

- **L23** — method `name` (src: 402, tests: 182)
- **L27** — method `description` (src: 97, tests: 106)
- **L31** — method `parameters` (src: 17, tests: 8)
- **L47** — method `execute` (src: 70, tests: 54)

---

# C — Not used in src/ (dead / scaffolding)

These definitions exist in `src/` but are **never referenced by production code**. Some may be used only in tests (scaffolding). Candidates for wiring up or removal.

**Total: 223**

| Category | Count | Breakdown |
|----------|-------|-----------|
| Core | 188 | 3 classes, 10 functions, 175 methods |
| Dashboard | 29 | 10 functions, 19 methods |
| Providers | 2 | 2 classes |
| Skills | 4 | 3 classes, 1 methods |

## Core

### `src/agent_orchestrator/core/alerts.py`

- **L111** — method `get_triggered_alerts` (src: 0, tests: 2)
- **L119** — method `clear_alerts` (src: 0, tests: 1)
- **L124** — method `add_rule` (src: 0, tests: 1)
- **L128** — method `remove_rule` (src: 0, tests: 1)

### `src/agent_orchestrator/core/api.py`

- **L65** — method `list_endpoints` (src: 0, tests: 5)
- **L71** — method `get_endpoint` (src: 0, tests: 5)
- **L78** — method `export_openapi_paths` (src: 1, tests: 1)
- **L105** — method `export_openapi_spec` (src: 0, tests: 1)

### `src/agent_orchestrator/core/audit.py`

- **L54** — method `log_action` (src: 0, tests: 20)
- **L88** — method `get_entries` (src: 0, tests: 6)
- **L111** — method `get_agent_history` (src: 0, tests: 1)
- **L115** — method `get_task_trace` (src: 0, tests: 1)
- **L123** — method `export_json` (src: 1, tests: 2)

### `src/agent_orchestrator/core/benchmark.py`

- **L74** — method `compare_models` (src: 0, tests: 1)
- **L98** — method `get_results` (src: 0, tests: 1)
- **L102** — method `get_best_for_task` (src: 1, tests: 2)

### `src/agent_orchestrator/core/bookmark_tracker.py`

- **L17** — function `load_state` (src: 0, tests: 4)
- **L29** — function `save_state` (src: 0, tests: 3)
- **L37** — function `load_bookmarks` (src: 0, tests: 5)
- **L59** — function `filter_unprocessed` (src: 0, tests: 7)
- **L93** — function `mark_processed` (src: 1, tests: 6)
- **L109** — function `cleanup_old_entries` (src: 0, tests: 4)

### `src/agent_orchestrator/core/cache.py`

- **L39** — method `is_expired` (src: 1, tests: 2)
- **L182** — method `my_node` (src: 0, tests: 15)
- **L188** — method `decorator` (src: 1, tests: 1)

### `src/agent_orchestrator/core/channels.py`

- **L234** — class `ChannelConfig` (src: 0, tests: 0)
- **L256** — method `get_state` (src: 0, tests: 5)
- **L264** — method `apply_writes` (src: 0, tests: 4)
- **L277** — method `reset_ephemeral` (src: 0, tests: 1)

### `src/agent_orchestrator/core/checkpoint_postgres.py`

- **L108** — method `delete_thread` (src: 0, tests: 0)

### `src/agent_orchestrator/core/config_manager.py`

- **L72** — method `rollback` (src: 1, tests: 2)
- **L130** — method `export_json` (src: 1, tests: 2)
- **L134** — method `import_json` (src: 0, tests: 1)
- **L138** — method `add_agent` (src: 0, tests: 4)
- **L144** — method `remove_agent` (src: 0, tests: 2)
- **L153** — method `add_provider` (src: 0, tests: 4)
- **L159** — method `remove_provider` (src: 0, tests: 2)
- **L168** — method `get_agent` (src: 0, tests: 4)
- **L175** — method `get_provider` (src: 0, tests: 2)

### `src/agent_orchestrator/core/conformance.py`

- **L59** — method `all_passed` (src: 1, tests: 5)
- **L110** — method `test_model_id` (src: 1, tests: 0)
- **L115** — method `test_capabilities` (src: 1, tests: 0)
- **L123** — method `test_cost_properties` (src: 1, tests: 0)
- **L131** — method `test_estimate_cost` (src: 1, tests: 0)
- **L136** — method `test_complete_simple` (src: 1, tests: 0)
- **L143** — method `test_complete_with_system` (src: 1, tests: 0)
- **L153** — method `test_complete_returns_usage` (src: 1, tests: 0)
- **L160** — method `test_complete_multi_turn` (src: 1, tests: 0)
- **L170** — method `test_stream_basic` (src: 1, tests: 0)
- **L216** — method `test_save_and_get` (src: 1, tests: 1)
- **L237** — method `test_get_latest` (src: 1, tests: 0)
- **L253** — method `test_get_latest_nonexistent` (src: 1, tests: 0)
- **L257** — method `test_list_thread` (src: 1, tests: 0)
- **L275** — method `test_list_empty_thread` (src: 1, tests: 0)
- **L279** — method `test_overwrite` (src: 1, tests: 0)
- **L300** — method `test_metadata` (src: 1, tests: 1)
- **L315** — method `test_thread_isolation` (src: 1, tests: 0)
- **L341** — method `test_complex_state` (src: 1, tests: 0)

### `src/agent_orchestrator/core/conversation.py`

- **L46** — method `from_dict` (src: 1, tests: 2)
- **L155** — method `clear_thread` (src: 1, tests: 2)
- **L168** — method `list_threads` (src: 1, tests: 1)
- **L172** — method `fork_thread` (src: 1, tests: 2)

### `src/agent_orchestrator/core/cooperation.py`

- **L80** — class `SharedContextStore` (src: 1, tests: 12)
- **L112** — method `get_artifact` (src: 1, tests: 4)
- **L115** — method `list_artifacts` (src: 0, tests: 1)
- **L118** — method `get_all_artifacts` (src: 1, tests: 1)
- **L121** — method `subscribe_artifacts` (src: 0, tests: 1)
- **L126** — method `unsubscribe_artifacts` (src: 0, tests: 1)
- **L132** — method `get_reports` (src: 0, tests: 0)
- **L147** — method `get_messages` (src: 0, tests: 3)
- **L159** — method `subscribe_messages` (src: 0, tests: 0)
- **L164** — method `unsubscribe_messages` (src: 0, tests: 0)
- **L174** — method `resolve_conflict` (src: 0, tests: 1)
- **L195** — method `mark_running` (src: 1, tests: 1)
- **L204** — method `get_pending` (src: 1, tests: 3)
- **L210** — method `get_ready_tasks` (src: 1, tests: 10)
- **L219** — method `get_parallel_batches` (src: 1, tests: 2)
- **L230** — method `all_complete` (src: 1, tests: 4)
- **L233** — method `get_completed` (src: 0, tests: 1)

### `src/agent_orchestrator/core/graph.py`

- **L683** — method `run_node` (src: 1, tests: 0)

### `src/agent_orchestrator/core/graph_patterns.py`

- **L188** — method `map_one` (src: 1, tests: 0)

### `src/agent_orchestrator/core/graph_templates.py`

- **L160** — method `get_version` (src: 1, tests: 3)
- **L170** — method `list_templates` (src: 0, tests: 1)
- **L174** — method `get_versions` (src: 0, tests: 1)
- **L218** — method `export_yaml` (src: 0, tests: 1)
- **L226** — method `import_yaml` (src: 0, tests: 1)
- **L290** — method `make_router` (src: 1, tests: 0)

### `src/agent_orchestrator/core/health.py`

- **L55** — method `record_success` (src: 0, tests: 9)
- **L93** — method `get_health` (src: 0, tests: 3)
- **L97** — method `get_all_health` (src: 0, tests: 1)

### `src/agent_orchestrator/core/mcp_server.py`

- **L55** — method `get_tool` (src: 0, tests: 2)
- **L59** — method `list_tools` (src: 0, tests: 3)
- **L63** — method `unregister_tool` (src: 0, tests: 2)
- **L74** — method `register_resource` (src: 0, tests: 3)
- **L78** — method `get_resource` (src: 0, tests: 1)
- **L82** — method `list_resources` (src: 0, tests: 1)
- **L86** — method `unregister_resource` (src: 0, tests: 2)
- **L97** — method `register_agent_tools` (src: 0, tests: 1)
- **L123** — method `register_skill_tools` (src: 0, tests: 1)
- **L145** — method `export_manifest` (src: 0, tests: 1)

### `src/agent_orchestrator/core/metrics.py`

- **L24** — method `inc` (src: 1, tests: 45)
- **L48** — method `inc` (src: 1, tests: 45)
- **L51** — method `dec` (src: 0, tests: 1)
- **L68** — method `observe` (src: 0, tests: 3)
- **L78** — method `get_avg` (src: 1, tests: 2)
- **L154** — method `get_all` (src: 0, tests: 3)
- **L175** — method `export_prometheus` (src: 0, tests: 1)

### `src/agent_orchestrator/core/migration.py`

- **L39** — method `supported_formats` (src: 0, tests: 1)
- **L43** — method `detect_format` (src: 1, tests: 4)
- **L61** — method `import_config` (src: 0, tests: 8)
- **L217** — method `export_langgraph` (src: 0, tests: 1)

### `src/agent_orchestrator/core/offline.py`

- **L27** — method `enable` (src: 0, tests: 1)
- **L31** — method `disable` (src: 0, tests: 1)
- **L36** — method `is_offline` (src: 1, tests: 5)
- **L44** — method `filter_providers` (src: 0, tests: 2)
- **L52** — method `is_provider_allowed` (src: 1, tests: 3)

### `src/agent_orchestrator/core/orchestrator.py`

- **L41** — class `OrchestratorConfig` (src: 1, tests: 7)
- **L282** — method `resolve_provider` (src: 1, tests: 0)

### `src/agent_orchestrator/core/plugins.py`

- **L42** — method `load_from_dict` (src: 0, tests: 1)
- **L56** — method `get_manifest` (src: 0, tests: 4)
- **L60** — method `list_plugins` (src: 0, tests: 2)
- **L67** — method `unregister` (src: 1, tests: 5)
- **L81** — method `register_skill_instance` (src: 0, tests: 2)
- **L85** — method `register_provider_instance` (src: 0, tests: 1)
- **L89** — method `get_loaded_skills` (src: 0, tests: 2)
- **L93** — method `get_loaded_providers` (src: 0, tests: 1)

### `src/agent_orchestrator/core/project.py`

- **L49** — method `list_projects` (src: 0, tests: 3)
- **L72** — method `set_current` (src: 0, tests: 3)
- **L86** — method `current_id` (src: 0, tests: 4)
- **L90** — method `archive` (src: 1, tests: 8)
- **L98** — method `unarchive` (src: 0, tests: 1)

### `src/agent_orchestrator/core/provider_presets.py`

- **L121** — method `list_presets` (src: 0, tests: 1)
- **L129** — method `get_builtin_names` (src: 0, tests: 1)
- **L133** — method `add_custom` (src: 0, tests: 2)
- **L164** — method `active_name` (src: 0, tests: 2)
- **L168** — method `get_provider_configs` (src: 0, tests: 2)
- **L191** — method `get_default_provider_key` (src: 0, tests: 2)

### `src/agent_orchestrator/core/rate_limiter.py`

- **L72** — method `record_usage` (src: 1, tests: 7)

### `src/agent_orchestrator/core/reducers.py`

- **L32** — function `replace_reducer` (src: 0, tests: 0)
- **L44** — function `append_unique_reducer` (src: 1, tests: 0)
- **L52** — function `max_reducer` (src: 0, tests: 0)
- **L59** — function `last_non_none_reducer` (src: 0, tests: 0)

### `src/agent_orchestrator/core/router.py`

- **L243** — method `get_classifier` (src: 0, tests: 1)

### `src/agent_orchestrator/core/skill.py`

- **L40** — method `override` (src: 1, tests: 3)
- **L106** — method `core_executor` (src: 1, tests: 0)
- **L124** — method `list_skills` (src: 1, tests: 1)
- **L127** — method `to_tool_definitions` (src: 0, tests: 0)

### `src/agent_orchestrator/core/store.py`

- **L375** — method `keys_written` (src: 1, tests: 2)
- **L461** — method `test_put_and_get` (src: 1, tests: 3)
- **L474** — method `test_update_preserves_created_at` (src: 1, tests: 0)
- **L486** — method `test_delete` (src: 1, tests: 3)
- **L492** — method `test_delete_nonexistent` (src: 1, tests: 1)
- **L496** — method `test_search_by_prefix` (src: 1, tests: 0)
- **L505** — method `test_search_with_filter` (src: 1, tests: 0)
- **L514** — method `test_search_filter_operators` (src: 1, tests: 0)
- **L525** — method `test_search_limit_offset` (src: 1, tests: 1)
- **L535** — method `test_list_namespaces` (src: 1, tests: 1)
- **L542** — method `test_list_namespaces_max_depth` (src: 1, tests: 1)
- **L551** — method `test_namespace_isolation` (src: 1, tests: 1)
- **L561** — method `test_ttl_expiration` (src: 1, tests: 1)

### `src/agent_orchestrator/core/task_queue.py`

- **L49** — method `enqueue` (src: 0, tests: 17)
- **L55** — method `dequeue` (src: 0, tests: 11)
- **L121** — method `get_task` (src: 0, tests: 5)
- **L124** — method `get_pending` (src: 1, tests: 3)
- **L127** — method `get_running` (src: 0, tests: 1)

### `src/agent_orchestrator/core/usage.py`

- **L77** — method `check_budget` (src: 0, tests: 3)
- **L146** — method `get_cost_by_provider` (src: 1, tests: 1)
- **L153** — method `get_cost_by_agent` (src: 1, tests: 1)
- **L161** — method `get_records` (src: 0, tests: 1)
- **L167** — method `get_cost_breakdown` (src: 0, tests: 1)

### `src/agent_orchestrator/core/users.py`

- **L86** — method `create_user` (src: 0, tests: 23)
- **L114** — method `get_user` (src: 0, tests: 7)
- **L118** — method `get_by_username` (src: 1, tests: 2)
- **L125** — method `get_by_api_key` (src: 0, tests: 5)
- **L132** — method `authenticate` (src: 0, tests: 4)
- **L148** — method `update_role` (src: 0, tests: 2)
- **L156** — method `deactivate` (src: 1, tests: 5)
- **L172** — method `regenerate_api_key` (src: 0, tests: 2)
- **L185** — method `delete_user` (src: 1, tests: 5)
- **L195** — method `has_permission` (src: 1, tests: 6)
- **L203** — method `check_permission` (src: 0, tests: 1)

### `src/agent_orchestrator/core/webhook.py`

- **L49** — method `unregister` (src: 1, tests: 5)
- **L60** — method `get_by_path` (src: 0, tests: 2)
- **L67** — method `list_webhooks` (src: 0, tests: 1)
- **L75** — method `receive` (src: 1, tests: 14)
- **L85** — method `validate_signature` (src: 0, tests: 4)
- **L103** — method `get_events` (src: 0, tests: 3)
- **L116** — method `mark_processed` (src: 1, tests: 6)

## Dashboard

### `src/agent_orchestrator/dashboard/app.py`

- **L818** — method `team_status` (src: 0, tests: 0)

### `src/agent_orchestrator/dashboard/events.py`

- **L103** — method `subscribe` (src: 1, tests: 5)
- **L108** — method `unsubscribe` (src: 1, tests: 1)

### `src/agent_orchestrator/dashboard/instrument.py`

- **L35** — method `patched_execute` (src: 1, tests: 0)
- **L92** — method `patched_run` (src: 1, tests: 0)
- **L138** — method `patched_single` (src: 1, tests: 0)
- **L169** — method `patched_parallel` (src: 1, tests: 0)
- **L189** — method `patched_assign` (src: 1, tests: 0)
- **L213** — method `patched_complete` (src: 1, tests: 0)
- **L243** — method `patched_get` (src: 1, tests: 0)

### `src/agent_orchestrator/dashboard/job_logger.py`

- **L77** — method `cleanup_empty_sessions` (src: 1, tests: 1)
- **L161** — method `list_sessions` (src: 1, tests: 2)
- **L214** — method `switch_session` (src: 1, tests: 0)

### `src/agent_orchestrator/dashboard/oauth_routes.py`

- **L232** — function `admin_list_users` (src: 0, tests: 0)
- **L240** — function `admin_approve_user` (src: 0, tests: 0)
- **L262** — function `admin_update_role` (src: 0, tests: 0)
- **L283** — function `admin_deactivate` (src: 0, tests: 0)
- **L300** — function `admin_list_pending` (src: 0, tests: 0)
- **L308** — function `admin_approve_pending` (src: 0, tests: 0)
- **L327** — function `admin_reject_pending` (src: 0, tests: 0)

### `src/agent_orchestrator/dashboard/usage_db.py`

- **L288** — method `get_totals` (src: 1, tests: 3)
- **L300** — method `get_summary` (src: 1, tests: 1)
- **L352** — method `get_recent_errors` (src: 1, tests: 1)
- **L368** — method `get_error_summary` (src: 1, tests: 1)
- **L392** — method `create_conversation` (src: 1, tests: 2)
- **L427** — method `get_recent_messages` (src: 1, tests: 1)

### `src/agent_orchestrator/dashboard/user_store.py`

- **L251** — function `get_or_create_user` (src: 1, tests: 15)
- **L557** — function `delete_user` (src: 1, tests: 5)
- **L595** — function `get_permissions` (src: 0, tests: 0)

## Providers

### `src/agent_orchestrator/providers/anthropic.py`

- **L21** — class `AnthropicProvider` (src: 0, tests: 0)

### `src/agent_orchestrator/providers/google.py`

- **L18** — class `GoogleProvider` (src: 0, tests: 0)

## Skills

### `src/agent_orchestrator/skills/github_skill.py`

- **L11** — class `GitHubSkill` (src: 0, tests: 0)

### `src/agent_orchestrator/skills/web_reader.py`

- **L49** — class `WebReaderSkill` (src: 0, tests: 4)

### `src/agent_orchestrator/skills/webhook_skill.py`

- **L10** — class `WebhookSkill` (src: 0, tests: 9)
- **L59** — method `get_sent` (src: 0, tests: 1)

---
*Generated by `scripts/dead_code_report.py` — 437 covered, 54 need tests, 223 dead*