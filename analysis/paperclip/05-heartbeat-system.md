# 05 - Heartbeat System

## Overview

The heartbeat is Paperclip's core execution engine. Instead of agents running continuously, they operate on a wake-check-act-sleep cycle. This is the most complex service in the codebase (~780+ lines).

## Execution Cycle

```
Scheduler Tick (default: 30s)
  │
  ▼
For each active agent with pending work:
  │
  ├─ Check budget → hard_stop if over limit
  ├─ Check concurrent runs → skip if at max (default 1, max 10)
  ├─ Resolve execution workspace (git worktree or project primary)
  ├─ Build adapter execution context (env vars, skills, JWT)
  ├─ Start adapter process (Claude Code, Codex, etc.)
  ├─ Stream logs via WebSocket (chunked at 8KB)
  ├─ Record cost events
  ├─ Summarize results (via LLM)
  └─ Update runtime state
```

## Concurrency Control

```typescript
const HEARTBEAT_MAX_CONCURRENT_RUNS_DEFAULT = 1;
const HEARTBEAT_MAX_CONCURRENT_RUNS_MAX = 10;
const startLocksByAgent = new Map<string, Promise<void>>();
```

Per-agent mutex prevents race conditions during start. The `startLocksByAgent` map ensures only one start operation per agent at a time.

## Session Management

Sessioned adapters (Claude, Codex, Cursor, Gemini, OpenCode, Pi) support:

```typescript
const SESSIONED_LOCAL_ADAPTERS = new Set([
  "claude_local", "codex_local", "cursor",
  "gemini_local", "opencode_local", "pi_local",
]);
```

Sessions enable:
- **Resume** — Agent continues from where it left off
- **Compaction** — Old context is summarized to fit context windows
- **Task context** — Sessions are tied to specific issues

## Workspace Management

Before execution, the heartbeat service realizes a workspace:

1. **Project primary** — Agent works in the project's main directory
2. **Git worktree** — Agent gets an isolated branch for the task
3. **Agent home** — Default fallback workspace

Managed workspaces handle git clone, branch creation, and worktree cleanup.

## Budget Enforcement

Before each run:
```
1. Check agent monthly spend < agent budget
2. Check company monthly spend < company budget
3. Check budget policies (per-scope thresholds)
4. If over limit → pause agent, create incident, skip run
```

After each cost event:
```
1. Update agent.spentMonthlyCents
2. Update company.spentMonthlyCents
3. Re-check budget policies → may pause mid-run
```

## Log Streaming

Run logs are streamed in real-time:
```typescript
const MAX_LIVE_LOG_CHUNK_BYTES = 8 * 1024; // 8KB chunks
```

Logs are:
1. Written to persistent run log store
2. Chunked and sent via WebSocket live events
3. Available for post-run review in the dashboard

## Run Summary

After execution, the heartbeat service generates a structured JSON summary of results using LLM analysis (`heartbeat-run-summary.ts`).

## Key Patterns
- Schedule-driven execution (heartbeat) vs continuous agent loops
- Per-agent mutex for concurrency safety
- Session continuity across heartbeats (agents don't restart from scratch)
- Git worktree isolation per task
- Budget as execution gate (pre-check) and circuit breaker (mid-run)
- Chunked log streaming via WebSocket

## Relevance to Our Project
Our orchestrator runs agents on-demand, not on heartbeats. The heartbeat model is better for autonomous operation (24/7 companies). The session continuity pattern — agents resuming context across invocations — is exactly what our `ConversationManager` does, but Paperclip integrates it more tightly with task ownership. The git worktree isolation per task is a pattern we should consider for our sandbox system.
