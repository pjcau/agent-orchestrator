# 02 - Architecture

## System Architecture

```
                          ┌──────────────────────┐
                          │    Client (Browser)   │
                          └──────────┬───────────┘
                                     │
                          ┌──────────▼───────────┐
                          │   Nginx (port 2026)   │
                          │   Reverse Proxy        │
                          └──┬─────┬─────┬───────┘
                             │     │     │
               ┌─────────────┘     │     └─────────────┐
               ▼                   ▼                   ▼
    ┌───────────────────┐ ┌────────────────┐ ┌──────────────────┐
    │  LangGraph Server │ │  Gateway API   │ │    Frontend      │
    │   (port 2024)     │ │  (port 8001)   │ │   (port 3000)    │
    │                   │ │                │ │                  │
    │  Agent Runtime    │ │  Models API    │ │  Next.js 16      │
    │  Thread Mgmt      │ │  MCP Config    │ │  React 19        │
    │  SSE Streaming    │ │  Skills Mgmt   │ │  Chat Interface  │
    │  Checkpointing    │ │  File Uploads  │ │  Artifacts View  │
    └───────────────────┘ │  Artifacts     │ └──────────────────┘
                          │  Memory API    │
                          │  Suggestions   │
                          └────────────────┘
```

## Nginx Routing

| Path | Destination |
|------|-------------|
| `/api/langgraph/*` | LangGraph Server (2024) |
| `/api/*` | Gateway API (8001) |
| `/*` | Frontend (3000) |

## Two-Layer Backend Split (Harness / App)

This is one of DeerFlow's most important architectural decisions:

### Harness (`packages/harness/deerflow/`)
- **Publishable** package (`deerflow-harness`)
- Import prefix: `deerflow.*`
- Contains: agents, tools, sandbox, models, MCP, skills, config, memory
- Everything needed to build and run agents

### App (`app/`)
- **Unpublished** application code
- Import prefix: `app.*`
- Contains: FastAPI Gateway API, IM channel integrations

### Dependency Rule
```
App → Harness  ✅ (allowed)
Harness → App  ❌ (FORBIDDEN — enforced by test_harness_boundary.py in CI)
```

This strict boundary enables:
1. Using the harness as a standalone library (DeerFlowClient)
2. Clean separation of concerns
3. No circular dependencies
4. Independent testability

## Key Components

1. **Lead Agent** — Main LangGraph agent with 11 middlewares
2. **Sub-agents** — Background workers (general-purpose, bash)
3. **Sandbox** — Isolated execution environment (local/Docker/K8s)
4. **Skills** — Markdown workflow definitions, progressively loaded
5. **Memory** — LLM-based fact extraction, cross-session persistence
6. **MCP** — Model Context Protocol for external tool integration
7. **Channels** — Telegram, Slack, Feishu IM integrations
