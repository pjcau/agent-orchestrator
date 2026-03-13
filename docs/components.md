# Component Interaction Graph

## High-Level Architecture

```mermaid
graph TD
    subgraph Dashboard["Dashboard (FastAPI)"]
        REST["REST API /api/*"]
        WS["WebSocket /ws"]
        STREAM["Streaming /ws/stream"]
        UI["Static UI (JS/CSS)"]
        REST --> EB
        WS --> EB
        STREAM --> EB
        EB["EventBus (events.py)"]
        JL["JobLogger"]
    end

    subgraph Execution["Execution Layer"]
        RT["run_team() (multi-agent)"]
        RA["run_agent() (single agent)"]
        GR["graphs.py (StateGraph)"]
        RT --> RA
        RT --> SR
        RA --> SR
        GR --> SR
        subgraph SR["SkillRegistry"]
            FR["FileRead"]
            FW["FileWrite"]
            GS["GlobSearch"]
            SE["ShellExec (sandboxed)"]
        end
    end

    subgraph Providers["Provider Layer"]
        AN["Anthropic (Claude)"]
        OA["OpenAI (GPT)"]
        GO["Google (Gemini)"]
        OR["OpenRouter (free)"]
        LO["Local (Ollama)"]
        AN & OA & GO & OR --> INT["Internet (cloud APIs)"]
        LO --> LOC["host.docker.internal:11434"]
    end

    EB --> RT
    EB --> RA
    Execution --> Providers
```

## Core Module Interactions

```mermaid
graph TD
    P["provider.py — ABC"] --> A["agent.py"]
    A --> SK["skill.py — SkillRegistry"]
    A --> CO["cooperation.py — Messaging"]
    CO --> OR["orchestrator.py — Coordination"]
    OR --> RO["router.py — 6 strategies"]
    RO --> US["usage.py — Cost & budgets"]
    RO --> HE["health.py — Health monitoring"]
    A --> G["graph.py — StateGraph engine"]
    G --> LN["llm_nodes.py — LLM node factories"]
    G --> RE["reducers.py — State merge"]
    G --> GP["graph_patterns.py — Retry, loop, map-reduce"]
    G --> GT["graph_templates.py — Versioned templates"]
    G --> CP["checkpoint.py — State persistence"]
    CP --> PG["checkpoint_postgres.py"]

    subgraph Infra["Infrastructure (standalone)"]
        RL["rate_limiter.py"]
        AU["audit.py — 11 event types"]
        TQ["task_queue.py — Priority queue"]
        ME["metrics.py — Prometheus"]
        AL["alerts.py — Spend alerts"]
        BM["benchmark.py"]
    end

    subgraph Ext["Extensions"]
        PL["plugins.py — Plugin loader"]
        WH["webhook.py — HMAC validation"]
        MC["mcp_server.py — MCP registry"]
        OF["offline.py — Local-only filter"]
        CM["config_manager.py — Config rollback"]
        PR["project.py — Multi-project"]
        UR["users.py — RBAC"]
        PP["provider_presets.py"]
        MI["migration.py — Import LangGraph/CrewAI"]
        AP["api.py — REST API (OpenAPI 3.0)"]
    end
```

## Dashboard Request Flow

```mermaid
sequenceDiagram
    participant B as Browser
    participant F as FastAPI
    participant T as run_team()
    participant A as run_agent()

    rect rgb(230, 240, 255)
        Note over B,A: Multi-Agent Mode (Async)
        B->>F: POST /api/team/run
        F-->>B: {job_id, status: "started"}
        F->>T: asyncio.create_task(run_team())
        T-->>B: WebSocket: team.started
        T-->>B: WebSocket: graph.start (plan → sub-agents → review)
        T->>T: team-lead plans (LLM)
        T->>A: run_agent() (parallel sub-agents)
        A-->>B: WebSocket: agent.spawn, agent.tool_call, agent.tool_result
        T->>T: team-lead validates & summarizes
        T-->>B: WebSocket: team.complete (full result)
    end

    rect rgb(240, 255, 240)
        Note over B,A: Single Agent Mode
        B->>F: POST /api/agent/run
        F->>A: run_agent()
        A-->>B: WebSocket events
        F-->>B: JSON response
    end

    rect rgb(255, 245, 230)
        Note over B,F: Simple Prompt Mode
        B->>F: WS /ws/stream
        F-->>B: token, token, ..., done
    end
```

## Multi-Agent Team Flow

```mermaid
graph TD
    TL1["team-lead (planner)"] -->|"Plan decomposition (LLM)"| SPLIT{" "}
    SPLIT --> BE["backend-dev<br/>Tools: file_read, file_write,<br/>glob_search, shell_exec"]
    SPLIT --> FE["frontend-dev<br/>Tools: file_read, file_write,<br/>glob_search, shell_exec"]
    BE -->|"Agent output"| TL2["team-lead (summarizer)<br/>Reads all agent output → summary"]
    FE -->|"Agent output"| TL2

    style TL1 fill:#4a90d9,color:#fff
    style TL2 fill:#4a90d9,color:#fff
    style BE fill:#7bc67e,color:#fff
    style FE fill:#7bc67e,color:#fff
```

## Event Flow (EventBus)

```mermaid
sequenceDiagram
    participant AG as Agent Execution
    participant EB as EventBus
    participant WS as WebSocket
    participant UI as Dashboard UI

    AG->>EB: AGENT_SPAWN
    EB->>WS: emit()
    WS->>UI: Agent badge appears

    AG->>EB: AGENT_STEP
    EB->>WS: emit()
    WS->>UI: Activity: "Step N"

    AG->>EB: AGENT_TOOL_CALL
    EB->>WS: emit()
    WS->>UI: Activity: "file_write(...)"

    AG->>EB: AGENT_TOOL_RESULT
    EB->>WS: emit()
    WS->>UI: Activity: "Success/Error"

    AG->>EB: TOKEN_UPDATE
    EB->>WS: emit()
    WS->>UI: Header: token count + cost

    AG->>EB: AGENT_COMPLETE
    EB->>WS: emit()
    WS->>UI: Activity: "Done"

    Note over AG,UI: Cooperation events
    AG->>EB: TASK_ASSIGNED
    EB->>WS: emit()
    WS->>UI: Delegation log

    AG->>EB: TASK_COMPLETED
    EB->>WS: emit()
    WS->>UI: Completion log
```

## File System Layout (Runtime)

```
agent-orchestrator/
├── jobs/                           # Session-based job persistence
│   ├── job_20260307_100733_a1b2/   # One folder per session
│   │   ├── 0001_prompt.json        # User prompts (logged)
│   │   ├── 0002_agent_run.json     # Agent execution results
│   │   ├── 0003_team_run.json      # Team execution results
│   │   ├── index.html              # Files created by agents
│   │   ├── style.css               │
│   │   └── app.py                  │
│   └── job_20260307_113045_c3d4/   # New session after inactivity
│       └── ...
└── src/...                         # Source code (read-only for agents)
```
