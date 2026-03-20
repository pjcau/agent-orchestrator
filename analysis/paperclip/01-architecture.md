# 01 - Architecture

## Overview

Paperclip follows a classic monorepo architecture with clear package boundaries. The system is structured as a Node.js API server (Express 5) with a React frontend, backed by PostgreSQL.

## High-Level Component Diagram

```
┌─────────────────────────────────────────────────┐
│                  React UI (Vite)                 │
│   React 19, Tailwind 4, TanStack Query, Radix   │
├─────────────────────────────────────────────────┤
│              Express 5 API Server                │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐│
│  │  Routes   │ │Middleware│ │ Live Events (WS) ││
│  └────┬─────┘ └──────────┘ └──────────────────┘│
│       ▼                                          │
│  ┌──────────────────────────────────────────────┤
│  │               Services Layer                  │
│  │  companies, agents, issues, heartbeat,        │
│  │  budgets, costs, goals, approvals, plugins    │
│  └──────┬───────────────────────────────────────┤
│         ▼                                        │
│  ┌──────────────┐  ┌───────────────────────────┤
│  │  Drizzle ORM  │  │   Adapter Registry         │
│  │  (PostgreSQL)  │  │   claude, codex, cursor... │
│  └──────────────┘  └───────────────────────────┤
│                                                   │
│  ┌──────────────────────────────────────────────┤
│  │            Plugin System                      │
│  │  Worker Manager, Event Bus, Job Scheduler,    │
│  │  Tool Dispatcher, Host Services               │
│  └──────────────────────────────────────────────┘
└─────────────────────────────────────────────────┘
          │                    │
          ▼                    ▼
   ┌──────────┐     ┌──────────────────┐
   │PostgreSQL│     │ Agent Runtimes    │
   │(embedded │     │ (Claude Code,     │
   │ or ext.) │     │  Codex, Cursor,   │
   └──────────┘     │  Gemini, etc.)    │
                    └──────────────────┘
```

## Package Boundaries

| Package | Purpose |
|---------|---------|
| `server/` | Express API, middleware, routes, services, adapters |
| `ui/` | React SPA (Vite + Tailwind) |
| `packages/db/` | Drizzle schema + migrations |
| `packages/shared/` | Shared TypeScript types |
| `packages/adapter-utils/` | Adapter utilities |
| `packages/adapters/*` | Per-agent-runtime adapters (7 adapters) |
| `packages/plugins/sdk/` | Plugin authoring SDK |
| `cli/` | CLI tool (`npx paperclipai`) |

## Data Flow

1. **User creates company** → DB record + default CEO agent
2. **User configures agents** → Adapter + budget + reporting hierarchy
3. **User sets goals** → Company → Project → Issue hierarchy
4. **Heartbeat scheduler ticks** → Wakes agents with pending work
5. **Agent executes** → Adapter spawns runtime (Claude Code, etc.)
6. **Runtime reports results** → Cost events recorded, budget enforced
7. **Live events emitted** → WebSocket pushes to dashboard

## Request Flow

```
Browser → Express → actorMiddleware (auth) → boardMutationGuard → Route Handler → Service → Drizzle → PostgreSQL
```

## Key Architectural Decisions

1. **Embedded PostgreSQL** for zero-config local development — no separate DB setup needed
2. **Express 5** (not Fastify or Hono) — stable, battle-tested, Express 5 has proper async support
3. **Drizzle ORM** over Prisma — type-safe SQL without code generation, better raw SQL support
4. **pnpm monorepo** — strict dependency isolation, workspace linking
5. **WebSocket for live events** — not SSE, allows bidirectional communication
6. **Adapter pattern** for agents — new runtimes can be added without touching core logic

## Key Patterns
- Service-oriented architecture within the server (each domain has a service module)
- Company-scoped everything (multi-tenancy without separate databases)
- Adapter registry for pluggable agent runtimes
- EventEmitter-based pub/sub for real-time events

## Relevance to Our Project
The monorepo structure with clear package boundaries is something we could adopt. Our single-directory approach works but lacks the isolation between db, shared types, and adapters that Paperclip achieves. The embedded PostgreSQL pattern is also worth studying — we currently require Docker for Postgres.
