# Dashboard

Real-time monitoring UI for the orchestrator. Shows agent interactions, technical metrics, task plan, and graph visualization.

```bash
docker compose up dashboard    # https://localhost:5005
```

## Layout

The dashboard body is a flex row with (left → right):

1. **Left rail** (`.left-rail`, ~88 px) — always-visible narrow column. Hosts **History**, **Prompts**, **Sandbox** toggle buttons stacked at the bottom. Replaced the previous `position: fixed` floating-actions pattern that overlapped the agent selector and prompt input.
2. **Left panel** (conditional) — `HistorySidebar` or `PromptsPanel`, shown when the matching button is active.
3. **Main** (`.dashboard__main`) — Agent Interactions (graph), Chat, optional Sandbox workspace.
4. **Right panel** (`.sidebar`, conditional) — event logs, activity, cache stats.

## Modular Architecture

`app.py` is a composition root (~282 lines) that includes two routers. Can run as single process or split.

- **`app.py`** — composition root: middleware, shared state, router composition
- **`gateway_api.py`** — REST management: config, users, jobs, MCP, metrics, memory, sandbox
- **`agent_runtime_router.py`** — execution: `/api/prompt`, `/api/agent/run`, `/api/team/*`, WebSocket, SSE
- **Single process** (default): `python -m agent_orchestrator.dashboard.server` — includes both routers
- **Split process**: `--mode gateway` (port 5006) or `--mode runtime` (port 5007)
- **Docker split**: `docker compose -f docker-compose.prod.yml -f docker-compose.split.yml up`
- **Nginx routing**: `nginx-split.conf` routes `/api/prompt`, `/api/agent/*`, `/api/team/*`, `/ws*` to runtime; everything else to gateway

## Multi-Category Agent Routing

The dashboard routes tasks to the correct agent category based on keyword detection:

| Category | Agents | Example keywords |
|----------|--------|-----------------|
| **software-engineering** | backend, frontend | code, api, database, docker |
| **finance** | financial-analyst, risk-analyst | stock, portfolio, trading, valuation |
| **data-science** | data-analyst, ml-engineer | dataset, machine learning, regression |
| **marketing** | content-strategist, growth-hacker | seo, campaign, social media, funnel |

Both `agent_runner.py` (team execution) and `graphs.py` (graph composition) use category-aware routing. Falls back to software-engineering if no keywords match.

## Conversation Persistence

Conversation memory persists across restarts and session reloads:

- **PostgresCheckpointer** — used when `DATABASE_URL` is set (production). Falls back to `InMemoryCheckpointer` otherwise.
- **Session restore** — `POST /api/jobs/{session_id}/restore` re-hydrates conversation context from job records when loading a historical session.
- **Frontend integration** — `loadSessionIntoChat()` calls the restore endpoint automatically, preserving `conversation_id` for continuity.

## MCP Integration (Server)

The dashboard exposes all agents and skills as MCP (Model Context Protocol) tools, enabling external AI tools to discover and invoke them.

- **Manifest**: `GET /api/mcp/manifest` — full MCP server manifest for client discovery
- **Tool list**: `GET /api/mcp/tools` — all registered tools with input schemas
- **Invoke**: `POST /api/mcp/tools/{name}/invoke` — execute a tool (skill or agent)
- **Orchestrator bridge**: `Orchestrator.register_mcp_tools()` populates an `MCPServerRegistry` from all configured agents and skills
- **UI**: MCP tool count shown in dashboard header

## MCP Client — connecting to external servers

The dashboard also acts as an MCP **client**, connecting outbound to external MCP servers.

- **List servers**: `GET /api/mcp/servers` — connected external servers with tool counts
- **Add server**: `POST /api/mcp/servers` — connect to a new external server (body: `name`, `transport`, `command`/`url`, `env`, `headers`)
- **Remove server**: `DELETE /api/mcp/servers/{name}` — disconnect and remove a server
- **Read resource**: `GET /api/mcp/resources/{server_name}/{uri}` — fetch resource content from an external server
- **Transports**: `stdio` (subprocess stdin/stdout) and `sse` (Server-Sent Events + HTTP POST)
- **Tool injection**: `SkillRegistry.register_mcp_tools(manager)` registers all external tools as local skills (prefixed `{server}/{tool}`)
- **Implementation**: `core/mcp_client.py` — `MCPClientManager`, `MCPClient`, `StdioTransport`, `SSETransport`

## SSE Streaming Runs

HTTP Server-Sent Events (SSE) for graph execution — an alternative to WebSocket streaming compatible with LangGraph SDK patterns.

- **Module**: `dashboard/sse.py` — `RunManager`, `HITLConfig`, `RunInfo`, SSE formatting helpers
- **Endpoints** (registered in `app.py`):
  - `POST /api/runs` — create and start a graph run; returns `{"run_id": "..."}` immediately
  - `GET /api/runs/{run_id}` — poll run status (`pending/running/interrupted/completed/failed`)
  - `GET /api/runs/{run_id}/stream` — `text/event-stream`; streams `data:` JSON lines in real-time
  - `POST /api/runs/{run_id}/resume` — resume an interrupted (HITL) run with `{"human_input": {...}}`
- **stream_mode**: `"events"` (node-level, default) or `"values"` (full state snapshot per step)
- **RunManager**: max 100 active runs; TTL eviction after 30 min; fans events out to multiple SSE subscribers
- **HITLConfig**: `enabled`, `timeout_seconds` (default 300), `auto_approve` (useful for tests)
- **Reconnection**: `Last-Event-ID` header triggers a reconnect comment; each event carries an `id:` field
- **EventBus integration**: SSE events are also mirrored to the EventBus so WebSocket clients see them
- **Tests**: `tests/test_sse.py` — 44 tests covering lifecycle, formatting, HITL, TTL, stream modes, integration

## Async Team Run

Multi-agent team runs execute as background tasks to prevent HTTP timeouts:

- **Non-blocking**: `POST /api/team/run` returns immediately with `{"job_id", "status": "started"}`
- **Background execution**: `run_team()` runs as `asyncio.Task`, streams events via WebSocket
- **Event lifecycle**: `team.started` → `agent.*` events → `team.complete` (with full result)
- **Graph visualization**: `run_team()` emits `GRAPH_START`/`GRAPH_NODE_ENTER`/`GRAPH_NODE_EXIT`/`GRAPH_END` for 3-phase workflow (plan → sub-agents → review)
- **Polling fallback**: `GET /api/team/status/{job_id}` returns current status and result
- **Memory safety**: completed jobs are evicted (keeps last 20) to prevent unbounded growth

## Session Explorer

Built-in file browser for navigating agent-created artifacts per session. Access via the **Explorer** button in the header.

- **3-pane layout**: Sessions list → File list → File preview with syntax highlighting
- **Syntax highlighting**: via highlight.js (CDN) — supports Python, JS, JSON, Markdown, etc.
- **Download**: individual files or entire session as ZIP archive
- **API endpoints**:
  - `GET /api/jobs/{session_id}/files` — list files in a session
  - `GET /api/jobs/{session_id}/files/{filename}` — read file content
  - `GET /api/jobs/{session_id}/download` — download session as ZIP
- **Security**: path traversal protection, 500KB file size limit

## Session Management

- **Delete sessions**: hover over a session in History → click X → confirm. Files are removed but DB metrics (tokens, cost) are preserved.
- **Lazy directory creation**: session directories are created only when the first file is written, not on session init.
- **Auto-cleanup**: empty session directories are automatically removed after 30 seconds.
- **API**: `DELETE /api/jobs/{session_id}` — cannot delete the current active session.

## Agent Error Tracking

Tool and LLM errors from sub-agents are persisted to PostgreSQL (`agent_errors` table) for analysis.

- **Storage**: `usage_db.record_error()` — persists session, agent, tool, error type/message, step, model, provider
- **Classification**: Errors auto-classified as `command_not_found`, `exit_code_error`, `timeout`, `not_allowed`, or generic `tool_error`
- **Hooks**: `agent_runner._instrumented_execute()` logs errors when `result.success == False`
- **API**: `GET /api/errors` — returns recent errors (last 100) and summary grouped by agent/error_type
- **Graceful**: Falls back silently if DB unavailable (no crash, in-memory only)

## Agent Memory System

Cross-thread long-term memory for agents, backed by PostgreSQL (durable) or InMemoryStore (dev).

- **Store**: `src/agent_orchestrator/core/store_postgres.py` — `PostgresStore(pool)` implements BaseStore on `store_items` table (JSONB values, dot-encoded namespaces, lazy TTL expiry)
- **Wiring**: Dashboard startup creates `PostgresStore` when `DATABASE_URL` is set, `InMemoryStore` otherwise. Accessible as `app.state.store` and via `store_holder[0]`
- **Namespaces**: `("agent", agent_name)` for per-agent memory, `("shared",)` for cross-agent facts
- **Injection**: Before each `run_agent` call, recent memories from both namespaces are prepended to the system prompt as a `<memory>` block (capped at 2000 chars)
- **Persistence**: After a successful agent run, a task summary is stored under `("agent", agent_name)` with a 30-day TTL
- **Summarization**: `ConversationManager` is configured with `SummarizationConfig(threshold=50, retain_last=10)` — triggers at 50 messages, keeps 10 most recent verbatim
- **API**: `GET /api/memory/namespaces`, `GET /api/memory/{namespace}`, `DELETE /api/memory/{namespace}/{key}`, `GET /api/memory/stats`

## Usage Metrics

The dashboard header shows two metric groups:

- **Session metrics** (left): tokens, cost, and speed for the current server session
- **Cumulative metrics** (right): all-time totals from PostgreSQL — tokens, cost, avg speed, requests
- **Speed tracking**: `avg_speed` (total average output tok/s from DB), `session_speed` (current server session)
- **DB indicator**: green dot = PostgreSQL connected, metrics persisted; red = in-memory only
- **Debug**: `GET /auth/debug` — shows OAuth config (base_url, redirect_uri, client_id prefix)

## Ported features (parity with vanilla UI, removed)

The vanilla JS dashboard at `src/agent_orchestrator/dashboard/static/` was removed once the React frontend reached feature parity. The following components were ported from the legacy `app.js` to React; they are listed here so the surface area is documented in one place.

| Component | File | Rendered in | Description |
|-----------|------|-------------|-------------|
| **PresetsBar** | `frontend/src/components/prompts/PresetsBar.tsx` | Above `ChatInput` inside `ChatPanel` | Fetches `GET /api/presets` and renders pill buttons. Clicking a preset substitutes `{context}` with the current attached-file context and calls `onApply` to set the textarea. Shows an inline notice if a file is required but not attached. |
| **ComparePanel** | `frontend/src/components/compare/ComparePanel.tsx` | Right `Sidebar`, "Compare Models" section | Two model selects + a "Go" button. POSTs the last user message to `/api/prompt` twice in parallel and shows side-by-side outputs with tok/s and elapsed time. |
| **PricingPanel** | `frontend/src/components/pricing/PricingPanel.tsx` | Right `Sidebar`, "Pricing" section | Fetches `GET /api/openrouter/pricing` (staleTime 60 s, no auto-refetch). Search input filters by model id/name; shows up to 50 rows; free models are highlighted. |
| **WorkspaceFilePicker** | `frontend/src/components/files/WorkspaceFilePicker.tsx` | Modal opened from `ChatInput` "Browse" button | Browses server-side workspace via `GET /api/files?path=...`. Breadcrumb navigation into directories; clicking a file fetches `GET /api/file?path=...` and pushes it into `attachedFiles`. |
| **InteractionTimeline** | `frontend/src/components/graph/InteractionTimeline.tsx` | Inside `graph-section` in `DashboardPage`, below `GraphVisualizer` | Renders `interactions` from the Zustand store (populated by `useWebSocket.ts`). Auto-scrolls to bottom on new items. Status dot coloured by status (pending/running/completed/failed). Shows "No interactions yet" empty state. |
| **Fallback log** | `frontend/src/hooks/useWebSocket.ts`, `team.complete` handler | As a system message in the chat | When `result.fallback_log` is non-empty, adds a system message before the assistant reply listing each entry as `✓ agent → model [ok] detail` or `✗ ... [failed] detail`. |

New API hooks added to `frontend/src/api/hooks.ts`: `usePresets`, `useFiles`, `fetchFileContent` (async helper), `usePricing`. Query keys added: `presets`, `files`, `pricing`.

## File context transparency (D)

To remove ambiguity about what the model is actually receiving, every attached file shows:

- A **kind badge** (`PDF`, `CSV`, `DOC`, `TXT`, …) — derived from `file_type` returned by `/api/upload`, or from the extension as a fallback.
- The **byte size** in B / KB / MB.
- A **source colour**: blue (`source: "upload"`) for local uploads, purple (`source: "workspace"`) for files picked from the server-side workspace.
- A **truncation warning** (`!` chip) when the server clipped the content (e.g. `/api/file` rejects > 100 KB).
- A `title` attribute combining all of the above for screen readers and hover.

At send time, `ChatPanel` emits a `system` bubble in the chat **before** the user message:

```
Sent with 2 files: report.pdf (2.0 KB) [upload], data.csv (12.3 KB) [workspace]
```

This addresses the original confusion ("did the model actually get my photo?") by making the included context visible turn-by-turn.

## Local file upload (C2)

The "+" button in `ChatInput` uploads the selected file to `POST /api/upload` (multipart) instead of reading it as UTF-8 in the browser.

- Backend (`gateway_api.py`) runs the file through `core.document_converter.DocumentConverter`, which converts PDF, DOCX, PPTX, XLSX/XLS, CSV, HTML/HTM, TXT to Markdown. Returns `{success, filename, file_type, markdown_content, markdown_path, page_count, row_count}`.
- The returned `markdown_content` is what gets attached and sent to the LLM — **no more binary-as-UTF-8 garbage** when an image is attached.
- Unsupported formats (`.jpg`, `.png`, `.zip`, …) get a 400 with `{"error":"Unsupported file format"}`. The UI surfaces the message in a red `attached-file--error` chip; the file is **not** attached.
- During the round-trip, an `attached-file--uploading` chip with a spinner is shown.

The "Browse" button next to it still browses the server-side workspace via `/api/files` + `/api/file` and adds the picked file with `source: "workspace"`.

## Reset behaviour (B)

The Reset button at the top right of the Agent Interactions section performs a **full** reset, not just the graph.

What it clears, in order:

1. `DELETE /api/conversation/{id}` — drops conversation memory on the server (best-effort; UI clears even on failure).
2. `POST /api/graph/reset` — clears the server-side graph snapshot.
3. `useAppStore.reset()` — wipes client state: messages, attached files, conversation id, graph nodes/edges, events, activity, interactions, task plan, stream buffer, pending team job. Also removes the `ao_conv_id` key from `localStorage` so the next send starts fresh.

`attachedFiles` is part of the store (not local to `ChatInput`) for exactly this reason — Reset can centrally clear them. `ChatInput` reads/writes through `useAppStore`'s `attachedFiles` slice (`addAttachedFile`, `removeAttachedFileAt`, `clearAttachedFiles`).

## Conversation persistence (A2)

The Simple Prompt mode keeps multi-turn memory automatically.

- The Zustand store (`frontend/src/stores/useAppStore.ts`) hydrates `conversationId` from `localStorage` (key `ao_conv_id`) when the app boots.
- `setConversationId(id)` mirrors changes back to `localStorage`; calling it with `null` clears the key.
- `ChatPanel.handleSend` lazily creates a conversation on the first send: if `conversationId` is null, it calls `POST /api/conversation/new`, persists the returned id, and only then issues the prompt request — so the very first turn is also recorded.
- At boot, `App.tsx` reads the persisted id and fetches `GET /api/conversation/{id}` to replay messages back into the chat. If the server no longer knows the id (404), the local id is cleared and the next send starts fresh.

This means a page reload, a new tab, or a server restart no longer drops the conversation thread.

## UI Enhancements (DeepFlow-Inspired)

Rich rendering capabilities in the React dashboard (Mermaid loaded from CDN, KaTeX via `rehype-katex`):

- **Mermaid.js** — renders ` ```mermaid ` code blocks as SVG diagrams in chat messages (CDN `mermaid@11`, wired in `frontend/src/components/common/MarkdownRenderer.tsx`)
- **KaTeX** — renders `$...$` (inline) and `$$...$$` (block) LaTeX math formulas via `rehype-katex` + `remark-math`
- **Progressive markdown streaming** — buffers streaming chunks and re-renders full markdown on each chunk, fixing broken code blocks and tables mid-stream
- **Reasoning/thinking accordion** — extracts `<thinking>` / `<reasoning>` tags into collapsible `<details>` blocks (auto-collapsed, purple left border)
- **Task Plan panel** — right sidebar section showing real-time graph execution progress (pending/in_progress/completed/failed) with elapsed time per node
- **HITL option buttons** — renders clarification options as clickable pill buttons; interrupt events show Approve/Reject buttons; clicks POST to `/api/runs/{run_id}/resume`
- **SSE toggle** — switch between WebSocket and EventSource for event streaming; indicator dot in header
