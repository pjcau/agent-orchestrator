---
sidebar_position: 6
title: "v0.8.0: Integrations"
---

# v0.8.0 — External Integrations ✅

Connect the orchestrator to the real world.

## Status: Complete

| Feature | Status | Module |
|---------|--------|--------|
| Plugin system (manifest + loader) | ✅ | `core/plugins.py` |
| Webhook registry (HMAC-SHA256) | ✅ | `core/webhook.py` |
| MCP server interface (tools + resources) | ✅ | `core/mcp_server.py` |
| Offline mode (local-only filtering) | ✅ | `core/offline.py` |
| GitHub skill (via `gh` CLI) | ✅ | `skills/github_skill.py` |
| Webhook send skill | ✅ | `skills/webhook_skill.py` |
| 37 tests | ✅ | `tests/test_integrations.py` |

## Key APIs

### PluginLoader

Register and manage plugins at runtime:

```python
from agent_orchestrator.core.plugins import PluginLoader, PluginManifest

loader = PluginLoader()
loader.register(PluginManifest(
    name="my-skill",
    version="1.0.0",
    plugin_type="skill",
    description="A custom skill",
))
loader.register_skill_instance("my-skill", skill_instance)
print(loader.list_plugins(plugin_type="skill"))
```

### WebhookRegistry

Inbound webhook handling with HMAC signature validation:

```python
from agent_orchestrator.core.webhook import WebhookRegistry, WebhookConfig

reg = WebhookRegistry()
reg.register(WebhookConfig("deploy", "Deploy Hook", "/hooks/deploy", secret="my-secret"))

# Validate incoming signature
if reg.validate_signature("deploy", raw_body, signature_header):
    event = reg.receive("deploy", payload, headers)
    # Process event...
    reg.mark_processed(event_index, "success")
```

### MCPServerRegistry

Expose agents and skills as MCP tools:

```python
from agent_orchestrator.core.mcp_server import MCPServerRegistry

mcp = MCPServerRegistry(server_name="my-orchestrator", version="1.0")

# Auto-register from agent configs
mcp.register_agent_tools({"backend": {"role": "Backend dev"}, "frontend": {"role": "UI dev"}})

# Auto-register from skill registry
mcp.register_skill_tools(["webhook_send"], skill_registry)

manifest = mcp.export_manifest()
# Returns: {"name": ..., "version": ..., "tools": [...], "resources": [...]}
```

### OfflineManager

Filter providers to local-only when offline:

```python
from agent_orchestrator.core.offline import OfflineManager, OfflineConfig

mgr = OfflineManager(OfflineConfig(enabled=True, local_provider_keys=["ollama"]))
filtered = mgr.filter_providers({"ollama": local_p, "openrouter": cloud_p})
# filtered == {"ollama": local_p}
```

### GitHubSkill

GitHub operations via `gh` CLI:

```python
from agent_orchestrator.skills.github_skill import GitHubSkill

gh = GitHubSkill()
result = await gh.execute({
    "action": "create_pr",
    "title": "Add feature X",
    "body": "Implements feature X",
    "base": "main",
    "head": "feature-x",
})
```

## Not Yet Implemented

- Local RAG pipeline (vector search with nomic-embed)
- Local code indexing
- Slack/Discord bot
- Provider marketplace
- Unified RAG (local embeddings + cloud reranking)
