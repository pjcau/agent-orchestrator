---
sidebar_position: 7
title: Component Interactions
---

# Component Interaction Graph

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Dashboard (FastAPI)                              │
│  ┌──────────┐  ┌───────────┐  ┌────────────┐  ┌──────────────────────┐ │
│  │ REST API │  │ WebSocket │  │ Streaming  │  │ Static UI (JS/CSS)   │ │
│  │ /api/*   │  │ /ws       │  │ /ws/stream │  │ index.html + app.js  │ │
│  └────┬─────┘  └─────┬─────┘  └─────┬──────┘  └──────────────────────┘ │
│       │               │              │                                   │
│  ┌────▼───────────────▼──────────────▼──────┐  ┌──────────────────────┐ │
│  │            EventBus (events.py)          │  │  JobLogger           │ │
│  │  emit() → WebSocket broadcast to UI      │  │  jobs/job_<session>/ │ │
│  └────┬─────────────────────────────────────┘  └──────────────────────┘ │
└───────┼─────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      Execution Layer                                     │
│                                                                          │
│  ┌─────────────────┐    ┌──────────────────┐    ┌────────────────────┐  │
│  │   run_team()    │    │   run_agent()    │    │   graphs.py        │  │
│  │  (multi-agent)  │    │  (single agent)  │    │  (graph prompts)   │  │
│  │                 │    │                  │    │                    │  │
│  │ 1. team-lead    │    │ LLM loop with   │    │ StateGraph-based   │  │
│  │    plans        │──▶ │ tool execution   │    │ orchestration      │  │
│  │ 2. sub-agents   │    │                  │    │                    │  │
│  │    execute      │    │                  │    │                    │  │
│  │ 3. team-lead    │    │                  │    │                    │  │
│  │    summarizes   │    │                  │    │                    │  │
│  └────────┬────────┘    └────────┬─────────┘    └────────┬───────────┘  │
│           │                      │                       │               │
│           ▼                      ▼                       ▼               │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    SkillRegistry                                  │   │
│  │  ┌───────────┐ ┌────────────┐ ┌───────────┐ ┌────────────────┐  │   │
│  │  │ FileRead  │ │ FileWrite  │ │ GlobSearch│ │ ShellExec      │  │   │
│  │  │           │ │            │ │           │ │ (sandboxed)    │  │   │
│  │  └───────────┘ └────────────┘ └───────────┘ └────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      Provider Layer                                      │
│                                                                          │
│  ┌─────────────┐ ┌──────────┐ ┌────────────┐ ┌────────────┐ ┌───────┐ │
│  │ Anthropic   │ │ OpenAI   │ │  Google    │ │ OpenRouter │ │ Local │ │
│  │ (Claude)    │ │ (GPT)    │ │ (Gemini)   │ │ (free)     │ │(Ollama│ │
│  └──────┬──────┘ └────┬─────┘ └─────┬──────┘ └─────┬──────┘ └───┬───┘ │
│         │              │             │               │            │      │
│         └──────────────┴─────────────┴───────────────┘            │      │
│                        │ (cloud APIs)                             │      │
│                        ▼                                          ▼      │
│                   Internet                              host.docker      │
│                                                        .internal:11434   │
└─────────────────────────────────────────────────────────────────────────┘
```

## Core Module Interactions

```
┌──────────────────────────────────────────────────────────────────┐
│                         core/                                     │
│                                                                   │
│  provider.py ◄──────────── All providers implement this ABC      │
│       │                                                           │
│       ▼                                                           │
│  agent.py ◄───── AgentConfig + Task + TaskResult                 │
│       │                                                           │
│       ├──▶ skill.py ◄──── SkillRegistry (tools for agents)      │
│       │                                                           │
│       ├──▶ cooperation.py ◄── Inter-agent messaging protocol     │
│       │         │                                                 │
│       │         ▼                                                 │
│       ├──▶ orchestrator.py ◄── Coordinates agents + routing      │
│       │         │                                                 │
│       │         ▼                                                 │
│       │    router.py ◄── 6 routing strategies                    │
│       │         │                                                 │
│       │         ├──▶ usage.py ◄── Cost tracking + budgets        │
│       │         └──▶ health.py ◄── Provider health monitoring    │
│       │                                                           │
│       ▼                                                           │
│  graph.py ◄──── StateGraph engine                                │
│       │                                                           │
│       ├──▶ llm_nodes.py ◄── Node factories for LLM calls        │
│       ├──▶ reducers.py ◄── State merge strategies                │
│       ├──▶ graph_patterns.py ◄── Retry, loop, map-reduce        │
│       ├──▶ graph_templates.py ◄── Versioned template store       │
│       └──▶ checkpoint.py ◄── State persistence                   │
│                 │                                                 │
│                 └──▶ checkpoint_postgres.py ◄── Postgres backend │
│                                                                   │
│  ┌─── Infrastructure modules (standalone) ───────────────────┐   │
│  │ rate_limiter.py  — Per-provider rate limiting             │   │
│  │ audit.py         — Structured audit logging (11 events)   │   │
│  │ task_queue.py    — Priority queue with retries            │   │
│  │ metrics.py       — Prometheus-compatible metrics          │   │
│  │ alerts.py        — Spend alert rules                      │   │
│  │ benchmark.py     — Model benchmarking suite               │   │
│  └───────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─── Extension modules ─────────────────────────────────────┐   │
│  │ plugins.py        — Plugin manifest & loader              │   │
│  │ webhook.py        — Webhook registry + HMAC validation    │   │
│  │ mcp_server.py     — MCP tool/resource registry            │   │
│  │ offline.py        — Local-only provider filtering         │   │
│  │ config_manager.py — Config load/save/validate/rollback    │   │
│  │ project.py        — Multi-project support                 │   │
│  │ users.py          — RBAC: admin, developer, viewer        │   │
│  │ provider_presets.py — One-click provider presets           │   │
│  │ migration.py      — Import from LangGraph/CrewAI/AutoGen  │   │
│  │ api.py            — Versioned REST API (OpenAPI 3.0)      │   │
│  └───────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

## Dashboard Request Flow

```
User Browser                    FastAPI (app.py)              Agent Layer
─────────────                   ────────────────              ───────────

  ┌─────────────┐
  │ Select mode │
  │ + provider  │
  │ + model     │
  └──────┬──────┘
         │
         │  Multi-Agent mode
         ├──────────────────────▶ POST /api/team/run
         │                              │
         │                              ├──▶ run_team()
         │                              │      │
         │                              │      ├──▶ team-lead: LLM plan
         │                              │      │         │
         │                              │      │    ┌────▼────────┐
         │                              │      │    │ Sub-tasks:  │
         │                              │      │    │ - BACKEND   │
         │                              │      │    │ - FRONTEND  │
         │                              │      │    └────┬────────┘
         │                              │      │         │
         │                              │      ├──▶ run_agent("backend-dev")
         │                              │      │    ├── LLM → tool_call → file_write
         │                              │      │    ├── LLM → tool_call → shell_exec
         │  ◀── WebSocket events ───────│──────│    └── ... (max_steps iterations)
         │  (agent_spawn, tool_call,    │      │
         │   tool_result, complete)     │      ├──▶ run_agent("frontend-dev")
         │                              │      │    ├── LLM → tool_call → file_write
         │                              │      │    └── ...
         │                              │      │
         │                              │      └──▶ team-lead: LLM summary
         │                              │
         │  ◀────────────────────────── │ JSON response
         │                              │
         │  Single Agent mode           │
         ├──────────────────────▶ POST /api/agent/run
         │                              │
         │                              └──▶ run_agent()
         │  ◀── WebSocket events ──────────── (tool calls, results)
         │  ◀── JSON response ─────────────
         │
         │  Simple Prompt mode
         ├──────────────────────▶ WS /ws/stream  (if streaming)
         │  ◀── token, token, done ────────
         │
         └──────────────────────▶ POST /api/graph/run  (if not streaming)
            ◀── JSON response ─────────────
```

## Multi-Agent Team Flow

```
                    ┌─────────────┐
                    │  team-lead  │
                    │  (planner)  │
                    └──────┬──────┘
                           │
                    Plan decomposition
                    (LLM call, no tools)
                           │
              ┌────────────┼────────────┐
              │                         │
              ▼                         ▼
    ┌──────────────────┐     ┌──────────────────┐
    │   backend-dev    │     │   frontend-dev   │
    │                  │     │                  │
    │ Tools:           │     │ Tools:           │
    │ - file_read      │     │ - file_read      │
    │ - file_write     │     │ - file_write     │
    │ - glob_search    │     │ - glob_search    │
    │ - shell_exec     │     │ - shell_exec     │
    │                  │     │                  │
    │ working_dir:     │     │ working_dir:     │
    │ jobs/job_<sess>/ │     │ jobs/job_<sess>/ │
    └────────┬─────────┘     └────────┬─────────┘
             │                        │
             │   Agent outputs        │
             └────────┬───────────────┘
                      │
               ┌──────▼──────┐
               │  team-lead  │
               │ (summarizer)│
               │             │
               │ Reads all   │
               │ agent output│
               │ → summary   │
               └─────────────┘
```

## Event Flow (EventBus)

```
Agent Execution                    EventBus                  Dashboard UI
───────────────                    ────────                  ────────────

run_agent() starts
  │
  ├──▶ AGENT_SPAWN ──────────▶ emit() ──▶ WebSocket ──▶ Agent badge appears
  │                                                       Graph node: "spawned"
  │
  ├──▶ AGENT_STEP ───────────▶ emit() ──▶ WebSocket ──▶ Activity: "Step N"
  │
  ├──▶ AGENT_TOOL_CALL ──────▶ emit() ──▶ WebSocket ──▶ Activity: "file_write(...)"
  │                                                       Graph node: "working"
  │
  ├──▶ AGENT_TOOL_RESULT ────▶ emit() ──▶ WebSocket ──▶ Activity: "Success/Error"
  │
  ├──▶ TOKEN_UPDATE ─────────▶ emit() ──▶ WebSocket ──▶ Header: token count + cost
  │
  └──▶ AGENT_COMPLETE ───────▶ emit() ──▶ WebSocket ──▶ Activity: "Done"
       or AGENT_ERROR                                     Graph node: "done"/"error"
       or AGENT_STALLED                                   Badge: status color

Cooperation events:
  TASK_ASSIGNED ──────────────▶ emit() ──▶ WebSocket ──▶ Activity: delegation log
  TASK_COMPLETED ─────────────▶ emit() ──▶ WebSocket ──▶ Activity: completion log
```
