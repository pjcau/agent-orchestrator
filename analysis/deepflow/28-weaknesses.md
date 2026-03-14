# 28 - Weaknesses

## 1. No API Authentication
The Gateway API has zero authentication. Anyone with network access can read/write MCP config, install skills, access memory, and upload files. Critical gap for any non-local deployment.

## 2. Single Lead Agent Bottleneck
All tasks funnel through one lead agent. No specialized agent personas — sub-agents are generic "general-purpose" or "bash". For complex domains (finance, data science, marketing), this means the lead agent must be an expert in everything.

## 3. No Observability Stack
No Prometheus metrics, no Grafana dashboards, no distributed tracing beyond optional LangSmith. In production, you're flying blind on:
- Token usage and costs
- Agent error rates
- Latency percentiles
- Provider health

## 4. No Cost Tracking
No usage tracking, no budget enforcement, no cost-per-task metrics. LLM API costs can spiral without visibility or controls.

## 5. No Database
Everything is file-based: memory (JSON), checkpoints (SQLite), config (YAML), thread store (JSON). This works for single-user but doesn't scale:
- No concurrent writes safety
- No query capabilities
- No backup/restore strategy
- No data retention policies

## 6. LangGraph Dependency Lock-in
Tightly coupled to LangGraph's agent runtime, middleware system, and SSE protocol. If LangGraph's API changes or licensing shifts, migration would be massive.

## 7. No Rate Limiting
No rate limiting on API endpoints or LLM calls. A single user could:
- Exhaust API quotas
- Spawn unlimited sub-agents
- Upload unlimited files

## 8. Limited Multi-Agent Cooperation
Sub-agents can't communicate with each other — only with the lead agent. No delegation chains, no conflict resolution, no agent-to-agent messaging.

## 9. No Audit Logging
No structured audit trail for actions. In regulated environments, you can't answer "who did what, when, and why."

## 10. Frontend Has No Tests
The extensive Next.js frontend (~200 components) has no test suite. No jest, no vitest, no e2e tests.

## 11. Skills Are Instructions, Not Code
Skills are markdown instructions the agent follows. Pros: flexible, model-agnostic. Cons:
- No programmatic composition
- No retry/timeout middleware
- No cache layer
- Dependent on LLM accurately following instructions
- No type safety

## 12. Memory Token Cost
LLM-powered memory extraction means every conversation costs extra tokens for the memory update. The debounce (30s) helps but doesn't eliminate the overhead.

## 13. No Multi-User Support
No user management, no RBAC, no tenant isolation. Every conversation shares the same memory, skills, and config.

## 14. Local Sandbox Security
The local sandbox runs commands directly on the host machine. Even with path validation, a sophisticated prompt injection could potentially execute arbitrary code outside the allowed paths.

## 15. No Job Archiving
No mechanism to archive old sessions. Thread data accumulates on disk indefinitely.
