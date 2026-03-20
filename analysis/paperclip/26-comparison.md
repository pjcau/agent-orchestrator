# 26 - Comparison: Paperclip vs Agent Orchestrator

## Overview

Paperclip and our agent-orchestrator operate at different abstraction levels but share common concerns. This comparison identifies overlaps, gaps, and synergy opportunities.

## Abstraction Level

| Concern | Paperclip | Agent Orchestrator |
|---------|-----------|-------------------|
| **Primary focus** | Running a company of agents | LLM provider abstraction & agent coordination |
| **Agent model** | Organizational entity (role, title, reporting line, budget) | Code abstraction (role, tools, provider) |
| **Execution model** | Heartbeat (schedule-driven) | On-demand (user-triggered) |
| **Task model** | Full issue tracker (statuses, labels, comments, sub-tasks) | Task queue with priority & retry |
| **Goal model** | Hierarchical (company → project → issue) | None |
| **Cost model** | Multi-scope budgets + double-entry finance | Usage tracker (per-task/session/day) |
| **Auth model** | OAuth2 + agent JWT + API keys | OAuth2 + API key |
| **Plugin model** | Full SDK with worker processes, events, jobs, tools, UI | Manifest-based loader (skills, providers) |

## Feature Comparison

| Feature | Paperclip | Agent Orchestrator |
|---------|-----------|-------------------|
| Multi-provider | Via adapters (Claude, Codex, Cursor, Gemini, OpenCode, Pi) | Via providers (Claude, GPT, Gemini, OpenRouter, Local) |
| Agent coordination | Org chart hierarchy + delegation | Orchestrator + cooperation protocols |
| Task routing | Manual assignment + org chart delegation | Smart routing (6 strategies, category-aware) |
| State persistence | PostgreSQL (55+ tables) | PostgreSQL + in-memory fallback |
| Real-time UI | WebSocket per company | WebSocket per session |
| Graph execution | None (heartbeat-based) | StateGraph engine (nodes, edges, parallel, HITL) |
| MCP | Not implemented | Full MCP server registry |
| Conversation memory | Agent task sessions | Thread-based with summarization |
| Sandbox | Git worktree | Docker sandbox |
| Telegram/Slack | Not built-in (via plugins) | Native integrations |
| Config management | JSON + env vars | YAML + JSON + env vars |
| Company templates | Export/import with collision handling | Config export/import |

## Architectural Comparison

### Paperclip Advantages
1. **Business abstraction** — Org charts, goals, budgets, governance are first-class
2. **Zero-config local dev** — Embedded Postgres, no Docker needed
3. **Plugin ecosystem** — Full SDK with events, jobs, tools, UI panels
4. **Company portability** — Export/import entire organizational structures
5. **Config versioning** — Agent config changes are tracked with rollback
6. **Finance ledger** — Double-entry beyond just API costs
7. **Monorepo structure** — Clean package boundaries

### Agent Orchestrator Advantages
1. **Provider abstraction** — True LLM API abstraction (Paperclip delegates to adapters)
2. **Graph execution** — StateGraph for complex workflows (parallel nodes, HITL, checkpoints)
3. **Smart routing** — 6 routing strategies with category awareness
4. **MCP integration** — Full MCP server for tool exposure
5. **Conversation memory** — Thread-based with summarization and forking
6. **Native integrations** — Slack, Telegram built-in
7. **Conformance tests** — Provider/checkpointer test suites

## Synergy Opportunities

1. **Paperclip as the business layer, our orchestrator as the execution engine**
   - Paperclip manages the org chart, goals, budgets
   - Our orchestrator handles the actual LLM orchestration, graph execution, routing
   - Bridge: our orchestrator could be an adapter in Paperclip

2. **Adopt Paperclip patterns**
   - Goal hierarchy for context-aware task routing
   - Budget policies (multi-scope, warn/hard_stop)
   - Config versioning for agent settings
   - Embedded Postgres for local dev
   - Company portability for config templates

3. **Contribute our patterns back**
   - Graph execution engine
   - Smart routing strategies
   - MCP integration
   - Conversation memory with summarization

## Key Takeaway

Paperclip answers "how do you run a company made of agents?" while we answer "how do you abstract and coordinate LLM agents?" These are complementary, not competing. The ideal system would combine Paperclip's organizational layer with our technical orchestration layer.
