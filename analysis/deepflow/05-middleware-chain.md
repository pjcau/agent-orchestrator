# 05 - Middleware Chain

## Overview

DeerFlow uses an 11-middleware chain that processes every agent invocation. This is the most sophisticated part of the architecture.

## Execution Order

| # | Middleware | Phase | Purpose |
|---|-----------|-------|---------|
| 1 | ThreadDataMiddleware | before_agent | Creates per-thread directories |
| 2 | UploadsMiddleware | before_agent | Injects uploaded files into conversation |
| 3 | SandboxMiddleware | before_agent | Acquires sandbox environment |
| 4 | DanglingToolCallMiddleware | before_agent | Patches missing ToolMessages |
| 5 | SummarizationMiddleware | before_agent | Context reduction (configurable) |
| 6 | TodoMiddleware | before/after | Task tracking in plan mode |
| 7 | TitleMiddleware | after_agent | Auto-generates thread title |
| 8 | MemoryMiddleware | after_agent | Queues for async memory update |
| 9 | ViewImageMiddleware | before_agent | Injects base64 image data |
| 10 | SubagentLimitMiddleware | after_model | Truncates excess task calls |
| 11 | LoopDetectionMiddleware | after_model | Detects repetitive tool calls |
| 12 | ClarificationMiddleware | after_model | Intercepts clarification requests (LAST) |

## Notable Middlewares

### LoopDetectionMiddleware (P0 Safety)

Prevents infinite tool call loops:
- Hashes tool calls (name + args)
- Sliding window of last 20 calls
- Warn at 3 identical calls (inject system message)
- Hard stop at 5 (strip all tool_calls, force text output)
- Per-thread tracking with LRU eviction (max 100 threads)

This is a pattern we should adopt — our orchestrator doesn't have loop detection.

### SummarizationMiddleware

Uses LangChain's built-in `SummarizationMiddleware`:
- Trigger types: tokens (15564), messages, fraction
- Keeps last 10 messages, summarizes older ones
- Uses lightweight model for cost efficiency

### SubagentLimitMiddleware

Enforces `MAX_CONCURRENT_SUBAGENTS = 3`:
- Runs `after_model` — intercepts the model's response
- If model generates >3 `task` tool calls, truncates the excess
- Prevents resource exhaustion from parallel subagents

### MemoryMiddleware

- Filters messages: only user inputs + final AI responses
- Queues for async processing (30s debounce, per-thread dedup)
- Background thread invokes LLM to extract facts

### DanglingToolCallMiddleware

Handles interrupted conversations:
- Scans for AIMessage `tool_calls` without matching ToolMessage responses
- Injects placeholder ToolMessages so the model doesn't get confused
- Critical for user-interrupted flows

## Architecture Insight

The middleware pattern is more composable than graph nodes:
- Each middleware is self-contained
- Easy to add/remove without changing the graph structure
- Middlewares can be conditionally included based on config
- Order matters and is explicitly documented

vs our approach:
- We use graph nodes (plan -> sub-agents -> review)
- More visible in graph visualization
- But harder to add cross-cutting concerns
