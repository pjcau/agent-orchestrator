# 09 - Budget and Cost System

## Overview

Paperclip treats cost control as a first-class concern with budget policies, enforcement, incidents, and a full double-entry finance ledger. This is one of the most sophisticated subsystems.

## Budget Policies

```typescript
interface BudgetPolicy {
  companyId: string;
  scopeType: "company" | "agent" | "project";
  scopeId: string;
  windowKind: "monthly" | "lifetime";
  thresholdType: "spend" | "token_count";
  amount: number;           // threshold value
  warnPercent: number;      // e.g., 80 → warn at 80%
  action: "warn" | "hard_stop" | "pause";
}
```

## Budget Enforcement Flow

```
Cost Event Recorded
       │
       ▼
Calculate monthly spend (agent + company)
       │
       ▼
For each applicable budget policy:
  ├─ observedAmount < warnPercent × amount → "ok"
  ├─ observedAmount >= warnPercent × amount → "warning"
  └─ observedAmount >= amount → "hard_stop"
       │
       ▼
If hard_stop:
  ├─ Pause the agent (pauseReason: "budget")
  ├─ Create budget incident
  └─ Cancel in-flight work via hook
```

## Budget Windows

```typescript
function resolveWindow(windowKind: BudgetWindowKind) {
  if (windowKind === "lifetime") {
    return { start: epoch, end: far-future };
  }
  return currentUtcMonthWindow(); // 1st of month to 1st of next
}
```

Monthly windows reset automatically. Lifetime windows accumulate forever.

## Cost Events

```typescript
// Each API call generates a cost event
{
  companyId, agentId,
  provider: "anthropic",
  model: "claude-3-opus",
  inputTokens: 1500,
  outputTokens: 800,
  cachedInputTokens: 200,
  costCents: 42,
  biller: "anthropic",      // who charges
  billingType: "metered_api" | "subscription_included" | "subscription_overage",
  occurredAt: timestamp
}
```

## Finance Ledger

Beyond API costs, Paperclip has a double-entry finance system:

```typescript
{
  companyId,
  direction: "debit" | "credit",
  amountCents: number,
  currency: "USD",
  estimated: boolean,
  category: string,
  // Links to: agentId, issueId, projectId, goalId, heartbeatRunId, costEventId
}
```

This enables accounting beyond just API costs — e.g., SaaS subscriptions, infrastructure costs, revenue tracking.

## Budget Incidents

When a policy triggers, an incident is created:
```typescript
{
  policyId, companyId, scopeType, scopeId,
  observedAmount, thresholdAmount,
  severity: "warning" | "critical",
  resolvedAt, resolvedByUserId,
  resolutionNote
}
```

Incidents require human resolution (acknowledge, adjust budget, dismiss).

## Key Patterns
- Budget as execution gate (pre-check) and circuit breaker (post-event)
- Multi-scope policies (company, agent, project)
- Double-entry finance ledger separate from API cost tracking
- Budget incidents as governance mechanism (require human resolution)
- Atomic spend updates (prevent race conditions)

## Relevance to Our Project
Our `UsageTracker` tracks costs but with simpler enforcement (per-task, per-session, per-day). Paperclip's multi-scope budget policies with warn/hard_stop levels are more sophisticated. The budget incident pattern (requiring human resolution) is governance we lack entirely. The double-entry finance ledger goes beyond what we need today but shows where cost tracking can evolve.
