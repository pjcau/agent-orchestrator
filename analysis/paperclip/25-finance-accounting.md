# 25 - Finance and Accounting

## Overview

Beyond API cost tracking, Paperclip includes a double-entry finance ledger. This allows modeling real business costs: infrastructure, SaaS subscriptions, revenue, and estimated expenses — not just LLM API calls.

## Cost Events (API Tracking)

```typescript
{
  companyId, agentId,
  provider: "anthropic",
  model: "claude-3-opus",
  inputTokens: 1500,
  outputTokens: 800,
  cachedInputTokens: 200,
  costCents: 42,
  biller: "anthropic",
  billingType: "metered_api" | "subscription_included" | "subscription_overage",
  occurredAt: timestamp
}
```

**Billing types**:
- `metered_api` — Pay-per-use API calls
- `subscription_included` — Included in subscription quota
- `subscription_overage` — Over subscription limits

## Finance Events (Double-Entry)

```typescript
{
  companyId,
  direction: "debit" | "credit",
  amountCents: number,
  currency: "USD",
  estimated: boolean,
  category: string,        // "api_cost", "infrastructure", "revenue"
  // Linked to: agentId, issueId, projectId, goalId, heartbeatRunId, costEventId
}
```

This enables:
- **Revenue tracking** — Credit entries for income
- **Infrastructure costs** — Debit entries for hosting, tools
- **Estimated costs** — Budget projections
- **P&L by entity** — Costs rolled up to agents, projects, goals

## Spend Aggregation

```typescript
// Monthly spend per company
const debitExpr = sql`coalesce(sum(case when direction = 'debit' then amount else 0 end), 0)::int`;
const creditExpr = sql`coalesce(sum(case when direction = 'credit' then amount else 0 end), 0)::int`;
```

## Dashboard Integration

Finance UI components:
- `BillerSpendCard` — Spend by billing provider
- `FinanceKindCard` — Spend by category
- `FinanceTimelineCard` — Spend over time
- `AccountingModelCard` — Financial model overview
- `BudgetPolicyCard` — Budget status
- `BudgetIncidentCard` — Budget alerts

## Key Patterns
- Separation of API costs (costEvents) and business finance (financeEvents)
- Double-entry accounting (debit/credit balance)
- Entity linkage (every cost traces to agent, issue, project, goal)
- Estimated vs actual amounts
- Billing type classification for accurate cost modeling

## Relevance to Our Project
Our `UsageTracker` only tracks API costs (tokens, cost per request). Paperclip's separation of API costs from business finance is forward-thinking — as AI companies scale, infrastructure and subscription costs matter beyond just LLM API calls. The entity linkage (cost → agent → issue → project → goal) provides complete cost attribution.
