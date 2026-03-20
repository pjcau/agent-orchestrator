# 07 - Issue Tracking System

## Overview

Paperclip includes a full-featured issue tracking system (like a simplified Jira/Linear). Issues are the atomic unit of work — agents are assigned issues and execute them during heartbeats.

## Issue Schema

```typescript
{
  id: uuid,
  companyId: string,
  projectId: string | null,
  parentId: string | null,      // sub-tasks
  goalId: string | null,        // traces to company goal
  identifier: string,           // "CMP-42" (prefix + counter)
  title: string,
  description: string,
  status: "backlog" | "todo" | "in_progress" | "in_review" | "blocked" | "done" | "cancelled",
  priority: number,
  assigneeAgentId: string | null,
  assigneeUserId: string | null,
  createdByUserId: string | null,
  checkoutRunId: string | null,  // prevents double-work
  startedAt, completedAt, cancelledAt,
  // ... metadata, timestamps
}
```

## Status Machine

```
backlog → todo → in_progress → in_review → done
                     │              │
                     ├→ blocked     │
                     │              │
                     └──────────────┴→ cancelled
```

Status transitions trigger side effects:
```typescript
function applyStatusSideEffects(status, patch) {
  if (status === "in_progress" && !patch.startedAt) patch.startedAt = new Date();
  if (status === "done") patch.completedAt = new Date();
  if (status === "cancelled") patch.cancelledAt = new Date();
}
```

## Atomic Task Checkout

The `checkoutRunId` field prevents double-work:
```typescript
function sameRunLock(checkoutRunId, actorRunId) {
  // Only the run that checked out the issue can update it
}
```

When a heartbeat run picks up an issue:
1. Set `checkoutRunId` to the run ID (atomic update with WHERE check)
2. If another run already has it → skip
3. On completion → clear `checkoutRunId`

## Issue Features

- **Labels** — many-to-many via `issueLabels` junction table
- **Comments** — threaded comments with agent and user attribution
- **Attachments** — file attachments via storage service
- **Documents** — linked documents with revision tracking
- **Sub-tasks** — via `parentId` self-reference
- **Work Products** — tracked outputs (PRs, commits, deployments)
- **Read states** — per-user unread tracking
- **Goal linkage** — issues auto-inherit company goal via `resolveIssueGoalId()`

## Issue Filters

```typescript
interface IssueFilters {
  status?: string;
  assigneeAgentId?: string;
  assigneeUserId?: string;
  touchedByUserId?: string;
  unreadForUserId?: string;
  projectId?: string;
  parentId?: string;
  labelId?: string;
  q?: string;  // full-text search
}
```

## Key Patterns
- Human-readable identifiers (CMP-42) instead of UUIDs in the UI
- Atomic checkout prevents concurrent execution
- Status side-effects (auto-timestamp on transition)
- Goal inheritance — issues without explicit goals inherit from project/company
- Comprehensive filtering with text search

## Relevance to Our Project
Our orchestrator has a `task_queue` for work distribution but nothing resembling a full issue tracker. The atomic checkout pattern is excellent for preventing double-work in multi-agent scenarios. The goal linkage (issue → project → company) provides context that agents can use for decision-making — our tasks are flat without hierarchical context.
