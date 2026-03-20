# 04 - Agent System

## Overview

Agents in Paperclip are organizational entities, not code abstractions. They have roles, titles, reporting hierarchies, budgets, and capabilities — modeled after human employees.

## Agent Schema

```typescript
{
  id: uuid,
  companyId: string,           // company scope
  name: string,                // "Sarah Chen"
  role: string,                // "ceo", "cto", "engineer", etc.
  title: string,               // "Chief Executive Officer"
  reportsTo: string | null,    // parent agent ID (org chart)
  capabilities: string[],      // what the agent can do
  adapterType: string,         // "claude_local", "codex_local", etc.
  adapterConfig: jsonb,        // adapter-specific settings
  runtimeConfig: jsonb,        // runtime settings (model, env)
  budgetMonthlyCents: number,  // monthly spending limit
  spentMonthlyCents: number,   // cached current month spend
  status: "active" | "paused" | "terminated",
  pauseReason: "manual" | "budget" | "system" | null,
  metadata: jsonb,
  createdAt, updatedAt
}
```

## Config Versioning

Every change to agent config creates a revision:

```typescript
const CONFIG_REVISION_FIELDS = [
  "name", "role", "title", "reportsTo", "capabilities",
  "adapterType", "adapterConfig", "runtimeConfig",
  "budgetMonthlyCents", "metadata"
];
```

Revisions track who made the change (user or agent), source (board, system, hire-hook), and enable rollback. Sensitive fields in `adapterConfig` are redacted before storing in revisions.

## Agent Authentication

Agents authenticate via API keys:

```typescript
function createToken() {
  return `pcp_${randomBytes(24).toString("hex")}`;
}
function hashToken(token: string) {
  return createHash("sha256").update(token).digest("hex");
}
```

Keys are SHA256-hashed before storage. The `pcp_` prefix makes tokens identifiable. Multiple keys per agent are supported, with revocation tracking.

Additionally, agents can authenticate via JWT (`agent-auth-jwt.ts`) for local adapter communication.

## Agent Lifecycle

1. **Hire** — Create agent (optionally requires board approval)
2. **Configure** — Set adapter, model, budget, reporting line
3. **Activate** — Status → "active", heartbeats begin
4. **Pause** — Manual or budget-triggered pause
5. **Terminate** — Permanent deactivation

## Agent Wakeup

Agents can be woken outside scheduled heartbeats via `agentWakeupRequests`:
- Task assignment triggers wakeup
- @-mention in issue comments triggers wakeup
- Manual "run now" from dashboard

## Runtime State

`agentRuntimeState` tracks per-agent persistent state:
- Current session context
- Last heartbeat timestamp
- Adapter-specific runtime data

`agentTaskSessions` tracks which issue an agent is currently working on (checkout/release semantics for atomic task ownership).

## Key Patterns
- Agents as organizational entities with human-like attributes (role, title, reporting line)
- Config revisioning for auditability and rollback
- Atomic task checkout (prevents double-work)
- Budget as a first-class agent attribute (not external)
- API key auth with hash-before-store

## Relevance to Our Project
Our `Agent` class is a code abstraction (role, tools, provider). Paperclip's agent is an organizational entity with budgets, reporting hierarchies, and config versioning. The config revisioning pattern is excellent — we don't track config changes at all. The atomic task checkout prevents the common problem of multiple agents working on the same task.
