---
sidebar_position: 6
title: "v0.8.0: Integrations"
---

# v0.8.0 — External Integrations

Connect to the real world.

## Local (Ollama)

| ID | Feature | Files | Detail |
|----|---------|-------|--------|
| INTEG-01 | Local RAG pipeline | `skills/rag.py` (new) | Vector search over project docs using local embeddings (nomic-embed via Ollama) |
| INTEG-02 | Local code indexing | `skills/code_index.py` (new) | Build codebase index with local model for context-aware agents |
| INTEG-03 | Offline mode | `core/router.py` | Full functionality without internet — local models + local tools only |

## Cloud (OpenRouter)

| ID | Feature | Files | Detail |
|----|---------|-------|--------|
| INTEG-04 | GitHub integration | `skills/github.py` (new) | Create PRs, review code, respond to issues (cloud model for quality) |
| INTEG-05 | Slack/Discord bot | `skills/slack.py` (new) | Trigger orchestrator from chat, choose local or cloud execution |
| INTEG-06 | Webhook triggers | `skills/webhook.py` (new), `dashboard/app.py` | Start graphs from external events (CI, cron, API calls) |
| INTEG-07 | MCP server | `core/mcp.py` (new) | Expose orchestrator as a Model Context Protocol server |

## Both

| ID | Feature | Files | Detail |
|----|---------|-------|--------|
| INTEG-08 | Plugin system | `core/plugins.py` (new) | Drop-in skills/providers without modifying core code |
| INTEG-09 | Provider marketplace | `dashboard/static/`, `dashboard/app.py` | Browse and add new OpenRouter models or Ollama model configs from UI |
| INTEG-10 | Unified RAG | `skills/rag.py` | Combine local embeddings with cloud reranking for best results |

## Implementation Notes

**INTEG-01 (Local RAG)** — uses Ollama embeddings:

```python
# skills/rag.py
class RAGSkill(Skill):
    name = "rag_search"
    # 1. Index project files with nomic-embed-text via Ollama
    # 2. On query: embed query → cosine similarity → top-k chunks
    # 3. Return relevant chunks as context for the agent
    # Storage: SQLite with vector extension or simple numpy
```

**INTEG-07 (MCP server)** — expose agents as MCP tools:

```python
# core/mcp.py
# Expose each agent as an MCP tool:
#   - agent_run(agent_name, task) → result
#   - graph_run(graph_type, prompt) → result
#   - skill_invoke(skill_name, params) → result
# This lets Claude Code or other MCP clients use the orchestrator
```

**INTEG-08 (Plugin system)**:

```
plugins/
├── my-custom-skill/
│   ├── plugin.yaml       # name, version, type: skill
│   └── skill.py          # class that extends Skill
├── my-custom-provider/
│   ├── plugin.yaml       # name, version, type: provider
│   └── provider.py       # class that extends Provider
```
