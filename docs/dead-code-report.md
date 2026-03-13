# Dead Code Report — Dual View

Two comparisons of definitions in `src/`:

- **Section A — Dead in production** (28): not used anywhere in `src/`. Candidates for removal.
- **Section B — Test-only** (131): not used in `src/` but used in `tests/`. Scaffolding written but never wired into production code.

---

# A — Dead in Production (not used in src/)

These definitions exist in `src/` but are **never referenced by any other production code**. Safe candidates for removal or implementation.

**Total: 28**

| Category | Count | Breakdown |
|----------|-------|-----------|
| Core | 24 | 3 attributes, 1 class, 4 functions, 5 methods, 1 unreachable, 10 variables |
| Dashboard | 1 | 1 function |
| Providers | 2 | 1 class, 1 unreachable |
| Skills | 1 | 1 class |

## Core

### `src/agent_orchestrator/core/bookmark_tracker.py`

- ⚪ **L12** — unused variable `DEFAULT_STATE_FILE` (60%)
- ⚪ **L13** — unused variable `DEFAULT_BOOKMARKS_FILE` (60%)

### `src/agent_orchestrator/core/channels.py`

- ⚪ **L233** — unused class `ChannelConfig` (60%)

### `src/agent_orchestrator/core/checkpoint_postgres.py`

- ⚪ **L108** — unused method `delete_thread` (60%)

### `src/agent_orchestrator/core/cooperation.py`

- ⚪ **L13** — unused variable `LOW` (60%)
- ⚪ **L15** — unused variable `HIGH` (60%)
- ⚪ **L16** — unused variable `CRITICAL` (60%)
- ⚪ **L132** — unused method `get_reports` (60%)
- ⚪ **L159** — unused method `subscribe_messages` (60%)
- ⚪ **L164** — unused method `unsubscribe_messages` (60%)

### `src/agent_orchestrator/core/graph.py`

- ⚪ **L66** — unused variable `CUSTOM` (60%)
- ⚪ **L163** — unused attribute `_compiled` (60%)
- ⚪ **L200** — unused attribute `_compiled` (60%)

### `src/agent_orchestrator/core/orchestrator.py`

- 🔴 **L254** — unused unreachable `while` (100%)

### `src/agent_orchestrator/core/project.py`

- ⚪ **L16** — unused variable `root_path` (60%)

### `src/agent_orchestrator/core/reducers.py`

- ⚪ **L32** — unused function `replace_reducer` (60%)
- ⚪ **L44** — unused function `append_unique_reducer` (60%)
- ⚪ **L52** — unused function `max_reducer` (60%)
- ⚪ **L59** — unused function `last_non_none_reducer` (60%)

### `src/agent_orchestrator/core/router.py`

- ⚪ **L183** — unused variable `min_coding_quality` (60%)
- 🔴 **L219** — unused variable `required_capabilities` (100%)

### `src/agent_orchestrator/core/skill.py`

- ⚪ **L127** — unused method `to_tool_definitions` (60%)

### `src/agent_orchestrator/core/usage.py`

- ⚪ **L63** — unused attribute `_session_start` (60%)

### `src/agent_orchestrator/core/webhook.py`

- ⚪ **L29** — unused variable `received_at` (60%)

## Dashboard

### `src/agent_orchestrator/dashboard/user_store.py`

- ⚪ **L595** — unused function `get_permissions` (60%)

## Providers

### `src/agent_orchestrator/providers/google.py`

- ⚪ **L18** — unused class `GoogleProvider` (60%)
- 🔴 **L74** — unused unreachable `raise` (100%)

## Skills

### `src/agent_orchestrator/skills/github_skill.py`

- ⚪ **L11** — unused class `GitHubSkill` (60%)

---

# B — Test-Only (used in tests/ but not in src/)

These definitions exist in `src/` and have **test coverage**, but are **never called from production code**. They are scaffolding — features written and tested but never integrated. Wire them up or remove them.

**Total: 131**

| Category | Count | Breakdown |
|----------|-------|-----------|
| Core | 125 | 1 attribute, 1 class, 6 functions, 105 methods, 3 unknowns, 9 variables |
| Dashboard | 2 | 2 functions |
| Providers | 1 | 1 class |
| Skills | 3 | 2 classs, 1 method |

## Core

### `src/agent_orchestrator/core/alerts.py`

- ⚪ **L111** — unused method `get_triggered_alerts` (60%)
- ⚪ **L119** — unused method `clear_alerts` (60%)
- ⚪ **L124** — unused method `add_rule` (60%)
- ⚪ **L128** — unused method `remove_rule` (60%)

### `src/agent_orchestrator/core/api.py`

- ⚪ **L65** — unused method `list_endpoints` (60%)
- ⚪ **L71** — unused method `get_endpoint` (60%)
- ⚪ **L105** — unused method `export_openapi_spec` (60%)

### `src/agent_orchestrator/core/audit.py`

- ⚪ **L54** — unused method `log_action` (60%)
- ⚪ **L88** — unused method `get_entries` (60%)
- ⚪ **L111** — unused method `get_agent_history` (60%)
- ⚪ **L115** — unused method `get_task_trace` (60%)
- ⚪ **L123** — unused method `export_json` (60%)

### `src/agent_orchestrator/core/benchmark.py`

- ⚪ **L74** — unused method `compare_models` (60%)
- ⚪ **L98** — unused method `get_results` (60%)
- ⚪ **L102** — unused method `get_best_for_task` (60%)

### `src/agent_orchestrator/core/bookmark_tracker.py`

- ⚪ **L17** — unused function `load_state` (60%)
- ⚪ **L29** — unused function `save_state` (60%)
- ⚪ **L37** — unused function `load_bookmarks` (60%)
- ⚪ **L59** — unused function `filter_unprocessed` (60%)
- ⚪ **L93** — unused function `mark_processed` (60%)
- ⚪ **L109** — unused function `cleanup_old_entries` (60%)

### `src/agent_orchestrator/core/channels.py`

- ⚪ **L256** — unused method `get_state` (60%)
- ⚪ **L264** — unused method `apply_writes` (60%)
- ⚪ **L277** — unused method `reset_ephemeral` (60%)

### `src/agent_orchestrator/core/config_manager.py`

- ⚪ **L72** — unused method `rollback` (60%)
- ⚪ **L130** — unused method `export_json` (60%)
- ⚪ **L134** — unused method `import_json` (60%)
- ⚪ **L138** — unused method `add_agent` (60%)
- ⚪ **L144** — unused method `remove_agent` (60%)
- ⚪ **L153** — unused method `add_provider` (60%)
- ⚪ **L159** — unused method `remove_provider` (60%)
- ⚪ **L168** — unused method `get_agent` (60%)
- ⚪ **L175** — unused method `get_provider` (60%)

### `src/agent_orchestrator/core/cooperation.py`

- ⚪ **L74** — unused variable `task_ids` (60%)
- ⚪ **L115** — unused method `list_artifacts` (60%)
- ⚪ **L121** — unused method `subscribe_artifacts` (60%)
- ⚪ **L126** — unused method `unsubscribe_artifacts` (60%)
- ⚪ **L147** — unused method `get_messages` (60%)
- ⚪ **L174** — unused method `resolve_conflict` (60%)
- ⚪ **L204** — unused method `get_pending` (60%)
- ⚪ **L233** — unused method `get_completed` (60%)

### `src/agent_orchestrator/core/graph.py`

- ⚪ **L64** — unused variable `HUMAN_INPUT` (60%)
- ⚪ **L65** — unused variable `APPROVAL` (60%)
- ⚪ **L73** — unused variable `interrupt_type` (60%)
- ⚪ **L76** — unused variable `options` (60%)

### `src/agent_orchestrator/core/graph_templates.py`

- ⚪ **L170** — unused method `list_templates` (60%)
- ⚪ **L174** — unused method `get_versions` (60%)
- ⚪ **L218** — unused method `export_yaml` (60%)
- ⚪ **L226** — unused method `import_yaml` (60%)

### `src/agent_orchestrator/core/health.py`

- ⚪ **L19** — unused variable `total_errors` (60%)
- ⚪ **L55** — unused method `record_success` (60%)
- ⚪ **L76** — unused attribute `total_errors` (60%)
- ⚪ **L93** — unused method `get_health` (60%)
- ⚪ **L97** — unused method `get_all_health` (60%)

### `src/agent_orchestrator/core/mcp_server.py`

- ⚪ **L55** — unused method `get_tool` (60%)
- ⚪ **L59** — unused method `list_tools` (60%)
- ⚪ **L63** — unused method `unregister_tool` (60%)
- ⚪ **L74** — unused method `register_resource` (60%)
- ⚪ **L78** — unused method `get_resource` (60%)
- ⚪ **L82** — unused method `list_resources` (60%)
- ⚪ **L86** — unused method `unregister_resource` (60%)
- ⚪ **L97** — unused method `register_agent_tools` (60%)
- ⚪ **L123** — unused method `register_skill_tools` (60%)
- ⚪ **L145** — unused method `export_manifest` (60%)

### `src/agent_orchestrator/core/metrics.py`

- ⚪ **L24** — unused method `inc` (60%)
- ⚪ **L48** — unused method `inc` (60%)
- ⚪ **L51** — unused method `dec` (60%)
- ⚪ **L68** — unused method `observe` (60%)
- ⚪ **L154** — unused method `get_all` (60%)
- ⚪ **L175** — unused method `export_prometheus` (60%)

### `src/agent_orchestrator/core/migration.py`

- ⚪ **L16** — unused variable `nodes_imported` (60%)
- ⚪ **L17** — unused variable `edges_imported` (60%)
- ⚪ **L38** — unused unknown `supported_formats` (60%)
- ⚪ **L61** — unused method `import_config` (60%)
- ⚪ **L217** — unused method `export_langgraph` (60%)

### `src/agent_orchestrator/core/offline.py`

- ⚪ **L27** — unused method `enable` (60%)
- ⚪ **L31** — unused method `disable` (60%)
- ⚪ **L44** — unused method `filter_providers` (60%)

### `src/agent_orchestrator/core/plugins.py`

- ⚪ **L42** — unused method `load_from_dict` (60%)
- ⚪ **L56** — unused method `get_manifest` (60%)
- ⚪ **L60** — unused method `list_plugins` (60%)
- ⚪ **L67** — unused method `unregister` (60%)
- ⚪ **L81** — unused method `register_skill_instance` (60%)
- ⚪ **L85** — unused method `register_provider_instance` (60%)
- ⚪ **L89** — unused method `get_loaded_skills` (60%)
- ⚪ **L93** — unused method `get_loaded_providers` (60%)

### `src/agent_orchestrator/core/project.py`

- ⚪ **L49** — unused method `list_projects` (60%)
- ⚪ **L72** — unused method `set_current` (60%)
- ⚪ **L85** — unused unknown `current_id` (60%)
- ⚪ **L90** — unused method `archive` (60%)
- ⚪ **L98** — unused method `unarchive` (60%)

### `src/agent_orchestrator/core/provider_presets.py`

- ⚪ **L121** — unused method `list_presets` (60%)
- ⚪ **L129** — unused method `get_builtin_names` (60%)
- ⚪ **L133** — unused method `add_custom` (60%)
- ⚪ **L163** — unused unknown `active_name` (60%)
- ⚪ **L168** — unused method `get_provider_configs` (60%)
- ⚪ **L191** — unused method `get_default_provider_key` (60%)

### `src/agent_orchestrator/core/rate_limiter.py`

- ⚪ **L72** — unused method `record_usage` (60%)

### `src/agent_orchestrator/core/router.py`

- ⚪ **L243** — unused class `get_classifier` (60%)

### `src/agent_orchestrator/core/skill.py`

- ⚪ **L40** — unused method `override` (60%)

### `src/agent_orchestrator/core/task_queue.py`

- ⚪ **L49** — unused method `enqueue` (60%)
- ⚪ **L55** — unused method `dequeue` (60%)
- ⚪ **L121** — unused method `get_task` (60%)
- ⚪ **L124** — unused method `get_pending` (60%)
- ⚪ **L127** — unused method `get_running` (60%)

### `src/agent_orchestrator/core/usage.py`

- ⚪ **L43** — unused variable `by_provider` (60%)
- ⚪ **L77** — unused method `check_budget` (60%)
- ⚪ **L161** — unused method `get_records` (60%)
- ⚪ **L167** — unused method `get_cost_breakdown` (60%)

### `src/agent_orchestrator/core/users.py`

- ⚪ **L86** — unused method `create_user` (60%)
- ⚪ **L114** — unused method `get_user` (60%)
- ⚪ **L125** — unused method `get_by_api_key` (60%)
- ⚪ **L132** — unused method `authenticate` (60%)
- ⚪ **L148** — unused method `update_role` (60%)
- ⚪ **L156** — unused method `deactivate` (60%)
- ⚪ **L172** — unused method `regenerate_api_key` (60%)
- ⚪ **L185** — unused method `delete_user` (60%)
- ⚪ **L203** — unused method `check_permission` (60%)

### `src/agent_orchestrator/core/webhook.py`

- ⚪ **L49** — unused method `unregister` (60%)
- ⚪ **L60** — unused method `get_by_path` (60%)
- ⚪ **L67** — unused method `list_webhooks` (60%)
- ⚪ **L75** — unused method `receive` (60%)
- ⚪ **L85** — unused method `validate_signature` (60%)
- ⚪ **L103** — unused method `get_events` (60%)
- ⚪ **L116** — unused method `mark_processed` (60%)

## Dashboard

### `src/agent_orchestrator/dashboard/user_store.py`

- ⚪ **L251** — unused function `get_or_create_user` (60%)
- ⚪ **L557** — unused function `delete_user` (60%)

## Providers

### `src/agent_orchestrator/providers/anthropic.py`

- ⚪ **L21** — unused class `AnthropicProvider` (60%)

## Skills

### `src/agent_orchestrator/skills/web_reader.py`

- ⚪ **L49** — unused class `WebReaderSkill` (60%)

### `src/agent_orchestrator/skills/webhook_skill.py`

- ⚪ **L10** — unused class `WebhookSkill` (60%)
- ⚪ **L59** — unused method `get_sent` (60%)

---

**Legend:** 🔴 ≥90% confidence · 🟡 70-89% · ⚪ 60-69%

*Generated by `scripts/dead_code_report.py` using vulture*