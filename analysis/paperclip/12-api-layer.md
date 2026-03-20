# 12 - API Layer

## Overview

Paperclip exposes a REST API via Express 5, organized as route modules mounted on `/api`. Each route module creates an Express Router and delegates to service functions.

## Route Organization

```typescript
// server/src/app.ts
const api = Router();
api.use(boardMutationGuard());
api.use("/health", healthRoutes(db));
api.use("/companies", companyRoutes(db, storageService));
api.use(companySkillRoutes(db));
api.use(agentRoutes(db));
api.use(projectRoutes(db));
api.use(issueRoutes(db));
api.use(executionWorkspaceRoutes(db));
api.use(goalRoutes(db));
api.use(approvalRoutes(db));
api.use(secretRoutes(db));
api.use(costRoutes(db));
api.use(activityRoutes(db));
api.use(dashboardRoutes(db));
api.use(sidebarBadgeRoutes(db));
api.use(instanceSettingsRoutes(db));
api.use(pluginRoutes(db, ...));
api.use(accessRoutes(db, ...));
app.use("/api", api);
```

## Route Modules

| Module | Key Endpoints |
|--------|--------------|
| `companies.ts` | CRUD companies, onboarding, branding |
| `agents.ts` | CRUD agents, config revisions, API keys, wakeup |
| `issues.ts` | CRUD issues, comments, labels, checkout, wakeup |
| `goals.ts` | CRUD goals |
| `projects.ts` | CRUD projects, workspaces |
| `approvals.ts` | Create/resolve approvals, comments |
| `costs.ts` | Record cost events, query spend |
| `secrets.ts` | CRUD secrets (encrypted) |
| `plugins.ts` | Plugin management, tool invocation, jobs |
| `execution-workspaces.ts` | Workspace management |
| `activity.ts` | Activity feed |
| `dashboard.ts` | Dashboard aggregations |
| `health.ts` | Health check |
| `llms.ts` | LLM model listing |
| `assets.ts` | File upload/download |
| `access.ts` | User access management |
| `instance-settings.ts` | Global settings |
| `sidebar-badges.ts` | Sidebar notification counts |
| `company-skills.ts` | Skill management |

## URL Structure

Company-scoped routes follow a consistent pattern:
```
/api/companies/:companyId/agents
/api/companies/:companyId/agents/:agentId
/api/companies/:companyId/issues
/api/companies/:companyId/goals
/api/companies/:companyId/events/ws  (WebSocket)
```

## Error Handling

Custom error factories:
```typescript
// server/src/errors.ts
function notFound(message) → 404
function unprocessable(message) → 422
function conflict(message) → 409
function badRequest(message) → 400
```

Express 5's async error handling catches thrown errors automatically.

## Validation

Zod schemas validate request bodies:
```typescript
// middleware/validate.ts
app.use(validate(schema));
```

## Key Patterns
- Route module per domain (not per HTTP verb)
- Service layer separation (routes are thin, services hold logic)
- Company-scoped URL patterns for multi-tenancy
- Custom error factories (not raw status codes)
- Express 5 async error handling (no try/catch wrappers needed)

## Relevance to Our Project
Our FastAPI router uses similar patterns but with Pydantic validation instead of Zod. The company-scoped URL pattern (`/companies/:id/...`) is more explicit than our flat routes. The thin routes + fat services pattern is good practice we already follow. The sidebar badges endpoint is a nice UX touch — real-time notification counts without polling.
