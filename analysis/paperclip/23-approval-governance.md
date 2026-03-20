# 23 - Approval and Governance

## Overview

Paperclip implements governance through an approval system. Certain actions require board (human) approval before execution. The human is "the board" — they approve hires, override strategy, and control agent behavior.

## Approval Flow

```
Action Triggered (e.g., hire new agent)
       │
       ▼
Company setting: requireBoardApprovalForNewAgents?
       │
       ├─ No → Execute immediately
       └─ Yes → Create approval request
              │
              ▼
         Approval (status: pending)
              │
              ├─ Board approves → Execute action (hire hook)
              ├─ Board requests revision → Agent adjusts
              └─ Board rejects → Action cancelled
```

## Approval Schema

```typescript
{
  id: uuid,
  companyId: string,
  type: string,           // "hire_agent", "strategy_change", etc.
  status: "pending" | "revision_requested" | "approved" | "rejected",
  requestedByAgentId: string | null,
  requestedByUserId: string | null,
  decidedByUserId: string | null,
  decisionNote: string | null,
  payload: jsonb,         // action-specific data
  decidedAt: timestamp,
  createdAt, updatedAt
}
```

## Approval Comments

Discussion threads on approvals:
```typescript
{
  id: uuid,
  approvalId: string,
  body: string,           // markdown content
  authorAgentId: string | null,
  authorUserId: string | null,
  createdAt
}
```

## Idempotency

```typescript
async function resolveApproval(id, targetStatus, decidedByUserId, decisionNote) {
  // If already in target status, return existing (no-op)
  if (existing.status === targetStatus) {
    return { approval: existing, applied: false };
  }
  // Only pending or revision_requested can be resolved
  if (!canResolveStatuses.has(existing.status)) throw unprocessable(...);
  // Atomic update with WHERE clause for race condition safety
  const updated = await db.update(approvals)
    .set({ status: targetStatus, ... })
    .where(and(eq(id), inArray(status, resolvableStatuses)))
    .returning();
}
```

## Hire Hook

When a hire approval is approved:
```typescript
notifyHireApproved(db, approval)
// → Activates the agent
// → Triggers first heartbeat
```

## Key Patterns
- Human-in-the-loop governance (board metaphor)
- Threaded discussion on approvals
- Idempotent resolution (safe to approve twice)
- Atomic status transitions (race-condition safe)
- Configurable per-company (some companies require approval, others don't)

## Relevance to Our Project
Our `ClarificationManager` provides human-in-the-loop for ambiguous tasks, but not for governance. Paperclip's approval system is about organizational control — the board approving agent hires and strategy changes. This is a pattern we'd need for enterprise adoption where humans need oversight of agent actions.
