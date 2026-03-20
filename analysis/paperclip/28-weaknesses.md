# 28 - Weaknesses

## Overview

Gaps and limitations in Paperclip's current implementation.

## 1. No Graph Execution Engine
Paperclip relies on heartbeat-based serial execution. There's no graph execution for complex workflows — no parallel node execution, no conditional branching, no map-reduce patterns. Complex multi-step tasks require manual decomposition by the CEO agent.

## 2. No Smart Task Routing
Task assignment is manual (human assigns issues to agents) or via CEO delegation. There's no automatic routing based on task complexity, agent capabilities, cost optimization, or load balancing.

## 3. No Conversation Memory
Agents don't have thread-based conversation memory. Each heartbeat is somewhat independent (sessions help, but there's no summarization or memory management). Long-running tasks may lose context.

## 4. No MCP Integration
No MCP server for tool exposure. External AI tools can't discover or invoke Paperclip's agents and skills through a standard protocol.

## 5. Single-Process Architecture
The in-process EventEmitter for live events works for single-process deployments but doesn't scale horizontally. Multiple Paperclip instances can't share events without adding Redis or similar.

## 6. External Service Stubs
AWS SSM and HashiCorp Vault secret providers are stubs (not implemented). S3 storage is the only cloud storage option. Cloud-native deployment needs more work.

## 7. No LLM Abstraction
Paperclip doesn't abstract LLM APIs. Each adapter wraps a complete agent runtime (Claude Code CLI, Codex CLI, etc.). You can't just point an agent at a raw LLM API — you need a supported runtime.

## 8. TypeScript Only
No Python SDK or agents. The ecosystem is TypeScript-only. This limits adoption in the Python-heavy AI/ML community.

## 9. Limited Documentation
The README is good but there's limited documentation for production deployment, plugin development, or adapter creation. The `.agents/skills/doc-maintenance/` skill exists but the docs themselves are sparse.

## 10. No Observability Integration
No OpenTelemetry, no Prometheus metrics, no structured tracing beyond pino logs. For production deployments, observability is critical and currently missing.

## 11. Embedded Postgres Limitations
The embedded Postgres is great for local dev but may have reliability concerns for long-running production deployments. No replication, no failover, no connection pooling.
