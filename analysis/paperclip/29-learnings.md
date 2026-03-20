# 29 - Learnings and Adoption Roadmap

## Overview

Actionable takeaways from the Paperclip analysis and how they can improve our agent-orchestrator.

## Priority 1: Adopt Immediately

### 1.1 Embedded Database for Local Dev
**What**: Bundle an embedded PostgreSQL (or SQLite) for zero-config local development.
**Why**: Our current setup requires Docker for Postgres, adding friction. `pip install agent-orchestrator && python -m agent_orchestrator` should just work.
**How**: Use `aiosqlite` for local dev, keep Postgres for production. Auto-detect and use appropriate backend.
**Files**: `src/agent_orchestrator/core/checkpoint.py`, `dashboard/app.py`

### 1.2 Config Versioning
**What**: Track every agent/orchestrator config change with revision history and rollback.
**Why**: Currently config changes are destructive — no way to see what changed or revert.
**How**: Add `config_revisions` table. On every config update, snapshot the old config.
**Files**: `src/agent_orchestrator/core/config_manager.py`

### 1.3 Atomic Task Checkout
**What**: Add `checkout_run_id` to task assignments to prevent double-work.
**Why**: Multiple agents can currently pick up the same task.
**How**: Add checkout field to task queue. Atomic update with WHERE clause.
**Files**: `src/agent_orchestrator/core/task_queue.py`

## Priority 2: Adopt Soon

### 2.1 Goal Hierarchy
**What**: Add hierarchical goals (mission → objective → task) to provide agents with "why" context.
**Why**: Agents currently see tasks without organizational context. Goals help them prioritize and make better decisions.
**How**: Add `goals` table with `parent_id`. Link tasks to goals. Include goal ancestry in agent prompts.
**Files**: New `src/agent_orchestrator/core/goals.py`

### 2.2 Multi-Scope Budget Policies
**What**: Budget policies at company/agent/project scope with warn/hard_stop thresholds.
**Why**: Our usage tracker is per-task/session/day. No way to set agent-level monthly budgets.
**How**: Add `budget_policies` table. Check policies on every cost event.
**Files**: `src/agent_orchestrator/core/usage.py`, new `core/budget_policies.py`

### 2.3 Rich Agent Personas
**What**: Support SOUL.md-style persona definitions for agents beyond just system prompts.
**Why**: Organizational behavior requires detailed persona definition (strategic posture, voice, execution protocols).
**How**: Add optional `persona_path` to agent config. Load persona as part of system prompt construction.
**Files**: `src/agent_orchestrator/core/agent.py`

## Priority 3: Consider for Future

### 3.1 Company/Organization Model
**What**: Multi-tenant organizational model with org charts, roles, reporting lines.
**Why**: Enterprise adoption needs organizational structure around agents.
**How**: Add `organizations`, `org_memberships`, `agent_hierarchies` tables.

### 3.2 Approval Workflow
**What**: Human-in-the-loop governance for critical agent actions.
**Why**: Enterprises need oversight of agent decisions beyond just clarification.
**How**: Extend `ClarificationManager` with approval workflow.

### 3.3 Plugin SDK with Worker Processes
**What**: Full plugin SDK with process isolation, events, jobs, tools, UI.
**Why**: Our plugin system is minimal. A full SDK enables an ecosystem.
**How**: Add worker process management, event bus, job scheduler.

### 3.4 Company Portability
**What**: Export/import entire orchestrator configurations as shareable templates.
**Why**: Enables configuration marketplace, team sharing, backup/restore.
**How**: Extend config manager with export/import including secret scrubbing.

### 3.5 Heartbeat Execution Mode
**What**: Add schedule-driven agent execution alongside on-demand.
**Why**: Autonomous agents need periodic check-in, not just on-demand triggering.
**How**: Add heartbeat scheduler service with configurable intervals.

## Summary

| Priority | Item | Effort | Impact |
|----------|------|--------|--------|
| P1 | Embedded DB | Medium | High (DX) |
| P1 | Config versioning | Low | Medium (reliability) |
| P1 | Atomic task checkout | Low | High (correctness) |
| P2 | Goal hierarchy | Medium | High (agent quality) |
| P2 | Multi-scope budgets | Medium | Medium (cost control) |
| P2 | Rich personas | Low | Medium (agent quality) |
| P3 | Organization model | High | High (enterprise) |
| P3 | Approval workflow | Medium | Medium (governance) |
| P3 | Plugin SDK | High | High (ecosystem) |
| P3 | Company portability | Medium | Medium (sharing) |
| P3 | Heartbeat mode | Medium | Medium (autonomy) |
