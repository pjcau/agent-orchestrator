# 03 - Company Model

## Overview

The "company" is the fundamental organizational unit in Paperclip. Everything — agents, goals, projects, issues, budgets, secrets — is scoped to a company. One Paperclip deployment can run multiple companies with complete data isolation.

## Schema

The `companies` table is the root entity:

```typescript
// Key fields from the schema
{
  id: uuid,
  name: string,
  description: string,
  status: "active" | "paused",
  issuePrefix: string,       // e.g. "CMP" — for issue identifiers like CMP-42
  issueCounter: number,      // auto-increment for issue numbering
  budgetMonthlyCents: number, // company-wide monthly budget
  spentMonthlyCents: number,  // cached current month spend
  requireBoardApprovalForNewAgents: boolean,
  brandColor: string,
  createdAt, updatedAt
}
```

## Company Service

The `companyService(db)` factory pattern is used throughout:

```typescript
export function companyService(db: Db) {
  return {
    list, getById, create, update, remove,
    getMonthlySpendByCompanyIds,
    // ...
  };
}
```

This pattern:
- Injects the database connection
- Returns a plain object of methods (no classes)
- Each method is a standalone async function
- Used consistently across all 30+ service modules

## Multi-Company Isolation

Every entity has a `companyId` foreign key. Queries always filter by company:

```typescript
// Typical pattern
db.select().from(agents).where(eq(agents.companyId, companyId))
```

This is enforced at the service layer, not the database layer (no row-level security). The middleware extracts the company context from the URL path (`/api/companies/:companyId/...`).

## Company Portability

Full export/import of companies:

```typescript
interface CompanyPortabilityExport {
  manifest: CompanyPortabilityManifest; // agents, projects, skills, goals
  files: CompanyPortabilityFileEntry[];  // attached files
}
```

Export scrubs secrets, maps internal IDs to slugs, generates org chart PNG, and includes a README. Import handles collision detection (rename, replace, skip strategies).

## Company Onboarding

Default company creation includes:
1. A CEO agent with SOUL.md (persona), HEARTBEAT.md (execution checklist), AGENTS.md (instructions)
2. Default company-level goal
3. Issue prefix derived from company name

## Key Patterns
- Company as the multi-tenancy boundary (not separate databases — single DB, company-scoped queries)
- Service factory pattern: `serviceModule(db)` returns method object
- Portability-first design — companies are exportable/importable artifacts
- Issue prefix + counter for human-readable identifiers (like JIRA's PROJECT-123)

## Relevance to Our Project
Our `ProjectManager` handles multi-project support but lacks the organizational concepts (org charts, hierarchical goals, budgets per entity). The company portability feature is sophisticated — our config export/import is simpler. The service factory pattern (`serviceModule(db)`) is cleaner than our class-based approach and worth considering.
