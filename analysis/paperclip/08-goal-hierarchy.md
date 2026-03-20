# 08 - Goal Hierarchy

## Overview

Goals provide the "why" behind every task. Paperclip models goals as a hierarchical tree from company mission down to specific deliverables. This ensures agents always understand context beyond just "what to do."

## Goal Schema

```typescript
{
  id: uuid,
  companyId: string,
  parentId: string | null,  // tree structure
  level: "company" | "team" | "project" | "individual",
  title: string,
  description: string,
  status: "active" | "completed" | "archived",
  createdAt, updatedAt
}
```

## Goal Tree

```
Company Goal: "Build the #1 AI note-taking app to $1M MRR"
  ├─ Team Goal: "Ship mobile app by Q2"
  │   ├─ Project Goal: "iOS app MVP"
  │   │   ├─ Issue: "Implement voice-to-text"
  │   │   └─ Issue: "Add cloud sync"
  │   └─ Project Goal: "Android app MVP"
  └─ Team Goal: "Grow to 10K MAU"
      ├─ Issue: "Launch referral program"
      └─ Issue: "SEO content strategy"
```

## Goal Resolution

When an issue doesn't have an explicit goal, the system resolves one:

```typescript
// issue-goal-fallback.ts
async function resolveIssueGoalId(db, issue) {
  // 1. Explicit goal on the issue
  // 2. Project's default goal
  // 3. Company's default active root goal
  // 4. Any company root goal
}
```

This ensures every issue always has a goal context available to the agent.

## Goal Service

Simple CRUD with hierarchical queries:

```typescript
export function goalService(db: Db) {
  return {
    list: (companyId) => db.select().from(goals).where(eq(goals.companyId, companyId)),
    getById: (id) => ...,
    getDefaultCompanyGoal: (companyId) => getDefaultCompanyGoal(db, companyId),
    create, update, remove,
  };
}
```

## Goal-Aware Execution

During heartbeat execution, the agent receives goal context:
- Current issue's goal and its ancestry
- Company mission
- Project objectives

This means an engineer agent coding a feature knows *why* that feature matters to the business.

## Key Patterns
- Hierarchical goal tree with fallback resolution
- Goals as first-class entities (not just metadata)
- Goal context injected into agent execution
- Company → Team → Project → Individual levels

## Relevance to Our Project
Our orchestrator has no concept of goals or mission alignment. Tasks are standalone work items without organizational context. Adding goal hierarchy would help agents make better decisions — e.g., a backend agent could prioritize a database migration over a refactor if it knows the goal is "ship feature X by Thursday." The fallback resolution pattern (issue → project → company) is elegant.
