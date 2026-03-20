# 17 - Testing Strategy

## Overview

Paperclip uses vitest for unit/integration tests and Playwright for E2E tests. The test suite is extensive with 100+ test files covering services, routes, adapters, and edge cases.

## Test Organization

```
server/src/__tests__/
├── adapter-models.test.ts
├── adapter-session-codecs.test.ts
├── agent-auth-jwt.test.ts
├── agent-instructions-routes.test.ts
├── agent-instructions-service.test.ts
├── agent-permissions-routes.test.ts
├── agent-shortname-collision.test.ts
├── agent-skill-contract.test.ts
├── agent-skills-routes.test.ts
├── approvals-service.test.ts
├── board-mutation-guard.test.ts
├── budgets-service.test.ts
├── claude-local-adapter.test.ts
├── claude-local-adapter-environment.test.ts
├── claude-local-skill-sync.test.ts
├── codex-local-adapter.test.ts
├── company-branding-route.test.ts
├── company-portability.test.ts
├── company-portability-routes.test.ts
├── company-skills.test.ts
├── costs-service.test.ts
├── cursor-local-adapter.test.ts
├── documents.test.ts
├── error-handler.test.ts
├── forbidden-tokens.test.ts
├── gemini-local-adapter.test.ts
├── health.test.ts
├── heartbeat-process-recovery.test.ts
├── heartbeat-run-summary.test.ts
├── heartbeat-workspace-session.test.ts
├── hire-hook.test.ts
├── instance-settings-routes.test.ts
├── invite-accept-gateway-defaults.test.ts
├── invite-accept-replay.test.ts
├── invite-expiry.test.ts
├── issue-goal-fallback.test.ts
├── issues-checkout-wakeup.test.ts
├── issues-user-context.test.ts
├── log-redaction.test.ts
├── monthly-spend-service.test.ts
├── opencode-local-adapter.test.ts
├── openclaw-gateway-adapter.test.ts
├── paperclip-env.test.ts
├── pi-local-adapter-environment.test.ts
├── plugin-dev-watcher.test.ts
├── plugin-worker-manager.test.ts
├── private-hostname-guard.test.ts
├── project-shortname-resolution.test.ts
├── quota-windows.test.ts
├── redaction.test.ts
├── storage-local-provider.test.ts
├── ui-branding.test.ts
├── work-products.test.ts
└── workspace-runtime.test.ts

tests/e2e/
├── onboarding.spec.ts
└── playwright.config.ts

tests/release-smoke/
├── docker-auth-onboarding.spec.ts
└── playwright.config.ts
```

## Testing Tools

| Tool | Purpose |
|------|---------|
| vitest | Unit + integration tests |
| supertest | HTTP endpoint testing |
| Playwright | E2E browser testing |

## Test Patterns

### Service Tests
Test business logic in isolation:
```typescript
describe("budgets-service", () => {
  it("creates hard_stop when spend exceeds threshold", ...);
  it("pauses agent on budget breach", ...);
});
```

### Adapter Tests
Test each adapter independently:
```typescript
describe("claude-local-adapter", () => {
  it("builds correct execution command", ...);
  it("parses output tokens correctly", ...);
});
```

### Edge Case Coverage
Specific tests for:
- Agent shortname collisions
- Invite expiry
- Heartbeat process recovery
- Workspace session management
- Log redaction
- Issue checkout wakeup

## UI Tests

```
ui/src/context/LiveUpdatesProvider.test.ts
ui/src/components/transcript/RunTranscriptView.test.tsx
ui/src/components/MarkdownBody.test.tsx
ui/src/lib/*.test.ts  (8+ lib test files)
ui/src/adapters/transcript.test.ts
```

## Key Patterns
- Co-located tests (`__tests__/` in server, `.test.ts` next to source in UI)
- Extensive adapter testing (one test file per adapter)
- Edge case tests for complex business logic
- E2E tests for critical user flows (onboarding)
- Release smoke tests in Docker

## Relevance to Our Project
We use pytest with similar patterns. Paperclip's adapter-per-adapter testing is thorough — we test providers but not as individually. The release smoke tests (Playwright in Docker) are a pattern we don't have. The sheer number of edge case tests (invite expiry, shortname collisions, log redaction) shows mature testing discipline.
