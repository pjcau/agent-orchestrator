# Dead Code Report

**Total findings:** 71

Definitions in `src/` that appear unused. Review each before removing —
some may be used dynamically, via reflection, or by external consumers.

## Summary

| Category | Count | Breakdown |
|----------|-------|-----------|
| Core | 66 | 3 attributes, 2 classs, 4 functions, 46 methods, 1 unreachable, 10 variables |
| Dashboard | 1 | 1 function |
| Providers | 2 | 1 class, 1 unreachable |
| Skills | 2 | 1 class, 1 method |

## Core

### `src/agent_orchestrator/core/alerts.py`

- ⚪ **L119** — unused method `clear_alerts` (60%)
- ⚪ **L124** — unused method `add_rule` (60%)
- ⚪ **L128** — unused method `remove_rule` (60%)

### `src/agent_orchestrator/core/api.py`

- ⚪ **L105** — unused method `export_openapi_spec` (60%)

### `src/agent_orchestrator/core/audit.py`

- ⚪ **L111** — unused method `get_agent_history` (60%)
- ⚪ **L115** — unused method `get_task_trace` (60%)

### `src/agent_orchestrator/core/benchmark.py`

- ⚪ **L74** — unused method `compare_models` (60%)
- ⚪ **L98** — unused method `get_results` (60%)

### `src/agent_orchestrator/core/bookmark_tracker.py`

- ⚪ **L12** — unused variable `DEFAULT_STATE_FILE` (60%)
- ⚪ **L13** — unused variable `DEFAULT_BOOKMARKS_FILE` (60%)

### `src/agent_orchestrator/core/channels.py`

- ⚪ **L233** — unused class `ChannelConfig` (60%)
- ⚪ **L277** — unused method `reset_ephemeral` (60%)

### `src/agent_orchestrator/core/checkpoint_postgres.py`

- ⚪ **L108** — unused method `delete_thread` (60%)

### `src/agent_orchestrator/core/config_manager.py`

- ⚪ **L134** — unused method `import_json` (60%)

### `src/agent_orchestrator/core/cooperation.py`

- ⚪ **L13** — unused variable `LOW` (60%)
- ⚪ **L15** — unused variable `HIGH` (60%)
- ⚪ **L16** — unused variable `CRITICAL` (60%)
- ⚪ **L115** — unused method `list_artifacts` (60%)
- ⚪ **L121** — unused method `subscribe_artifacts` (60%)
- ⚪ **L126** — unused method `unsubscribe_artifacts` (60%)
- ⚪ **L132** — unused method `get_reports` (60%)
- ⚪ **L159** — unused method `subscribe_messages` (60%)
- ⚪ **L164** — unused method `unsubscribe_messages` (60%)
- ⚪ **L174** — unused method `resolve_conflict` (60%)
- ⚪ **L233** — unused method `get_completed` (60%)

### `src/agent_orchestrator/core/graph.py`

- ⚪ **L66** — unused variable `CUSTOM` (60%)
- ⚪ **L163** — unused attribute `_compiled` (60%)
- ⚪ **L200** — unused attribute `_compiled` (60%)

### `src/agent_orchestrator/core/graph_templates.py`

- ⚪ **L170** — unused method `list_templates` (60%)
- ⚪ **L174** — unused method `get_versions` (60%)
- ⚪ **L218** — unused method `export_yaml` (60%)
- ⚪ **L226** — unused method `import_yaml` (60%)

### `src/agent_orchestrator/core/health.py`

- ⚪ **L97** — unused method `get_all_health` (60%)

### `src/agent_orchestrator/core/mcp_server.py`

- ⚪ **L78** — unused method `get_resource` (60%)
- ⚪ **L82** — unused method `list_resources` (60%)
- ⚪ **L97** — unused method `register_agent_tools` (60%)
- ⚪ **L123** — unused method `register_skill_tools` (60%)
- ⚪ **L145** — unused method `export_manifest` (60%)

### `src/agent_orchestrator/core/metrics.py`

- ⚪ **L51** — unused method `dec` (60%)
- ⚪ **L175** — unused method `export_prometheus` (60%)

### `src/agent_orchestrator/core/migration.py`

- ⚪ **L217** — unused method `export_langgraph` (60%)

### `src/agent_orchestrator/core/offline.py`

- ⚪ **L27** — unused method `enable` (60%)
- ⚪ **L31** — unused method `disable` (60%)

### `src/agent_orchestrator/core/orchestrator.py`

- 🔴 **L254** — unused unreachable `while` (100%)

### `src/agent_orchestrator/core/plugins.py`

- ⚪ **L42** — unused method `load_from_dict` (60%)
- ⚪ **L85** — unused method `register_provider_instance` (60%)
- ⚪ **L93** — unused method `get_loaded_providers` (60%)

### `src/agent_orchestrator/core/project.py`

- ⚪ **L16** — unused variable `root_path` (60%)
- ⚪ **L98** — unused method `unarchive` (60%)

### `src/agent_orchestrator/core/provider_presets.py`

- ⚪ **L121** — unused method `list_presets` (60%)
- ⚪ **L129** — unused method `get_builtin_names` (60%)

### `src/agent_orchestrator/core/reducers.py`

- ⚪ **L32** — unused function `replace_reducer` (60%)
- ⚪ **L44** — unused function `append_unique_reducer` (60%)
- ⚪ **L52** — unused function `max_reducer` (60%)
- ⚪ **L59** — unused function `last_non_none_reducer` (60%)

### `src/agent_orchestrator/core/router.py`

- ⚪ **L183** — unused variable `min_coding_quality` (60%)
- 🔴 **L219** — unused variable `required_capabilities` (100%)
- ⚪ **L243** — unused class `get_classifier` (60%)

### `src/agent_orchestrator/core/skill.py`

- ⚪ **L127** — unused method `to_tool_definitions` (60%)

### `src/agent_orchestrator/core/task_queue.py`

- ⚪ **L127** — unused method `get_running` (60%)

### `src/agent_orchestrator/core/usage.py`

- ⚪ **L63** — unused attribute `_session_start` (60%)
- ⚪ **L161** — unused method `get_records` (60%)
- ⚪ **L167** — unused method `get_cost_breakdown` (60%)

### `src/agent_orchestrator/core/users.py`

- ⚪ **L203** — unused method `check_permission` (60%)

### `src/agent_orchestrator/core/webhook.py`

- ⚪ **L29** — unused variable `received_at` (60%)
- ⚪ **L67** — unused method `list_webhooks` (60%)

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

### `src/agent_orchestrator/skills/webhook_skill.py`

- ⚪ **L59** — unused method `get_sent` (60%)

## Recommended Actions

1. **🔴 High confidence (90%+):** Likely safe to remove
2. **🟡 Medium confidence (70-89%):** Check if used dynamically or in tests
3. **⚪ Lower confidence (60-69%):** May be used via reflection, config, or external API

---
*Generated by `scripts/dead_code_report.py` using vulture*