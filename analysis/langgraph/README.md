# LangGraph Analysis

Deep analysis of [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) — 30 files covering architecture, implementation, and lessons for our agent-orchestrator.

## Index

### Core Engine (00-09)
| # | File | Topic |
|---|------|-------|
| 00 | [overview](00-overview.md) | Repository structure, ecosystem, dependencies |
| 01 | [architecture](01-architecture.md) | Design philosophy, abstractions, execution flow |
| 02 | [state-graph](02-state-graph.md) | StateGraph builder, schema derivation, compile() |
| 03 | [channels](03-channels.md) | Channel types, reducers, trigger-based scheduling |
| 04 | [pregel-engine](04-pregel-engine.md) | BSP model, superstep phases, parallelism |
| 05 | [pregel-loop](05-pregel-loop.md) | Main loop, tick/after_tick, checkpoint ordering |
| 06 | [pregel-runner](06-pregel-runner.md) | Concurrent execution, FuturesDict, commit |
| 07 | [conditional-edges](07-conditional-edges.md) | Routing, BranchSpec, Send API |
| 08 | [functional-api](08-functional-api.md) | @task, @entrypoint decorators |
| 09 | [managed-values](09-managed-values.md) | Computed state, IsLastStep, RemainingSteps |

### Persistence (10-16)
| # | File | Topic |
|---|------|-------|
| 10 | [checkpoint-base](10-checkpoint-base.md) | Data model, interface, UUIDv6, write semantics |
| 11 | [checkpoint-serialization](11-checkpoint-serialization.md) | msgpack, encryption, allowlists |
| 12 | [checkpoint-sqlite](12-checkpoint-sqlite.md) | SQLite schema, monolithic storage |
| 13 | [checkpoint-postgres](13-checkpoint-postgres.md) | Postgres schema, blob architecture, migrations |
| 14 | [store](14-store.md) | Long-term memory, BaseStore, semantic search |
| 15 | [cache](15-cache.md) | Task caching, InMemory, Redis |
| 16 | [conformance-tests](16-conformance-tests.md) | Capability-based test harness |

### Prebuilt & SDK (17-24)
| # | File | Topic |
|---|------|-------|
| 17 | [prebuilt-agents](17-prebuilt-agents.md) | create_react_agent factory |
| 18 | [tool-node](18-tool-node.md) | ToolNode, injection, middleware, validation |
| 19 | [human-in-the-loop](19-human-in-the-loop.md) | Interrupts, resume, Command |
| 20 | [sdk-client](20-sdk-client.md) | Python SDK architecture, error hierarchy |
| 21 | [sdk-api](21-sdk-api.md) | All API endpoints |
| 22 | [sdk-auth](22-sdk-auth.md) | Auth system, encryption |
| 23 | [cli](23-cli.md) | CLI commands (up, build, dev, new) |
| 24 | [cli-config](24-cli-config.md) | langgraph.json format |

### Internals & Insights (25-29)
| # | File | Topic |
|---|------|-------|
| 25 | [internals](25-internals.md) | _internal/ utilities, config, runnable, cache |
| 26 | [retry-error-handling](26-retry-error-handling.md) | RetryPolicy, error hierarchy, traceback cleaning |
| 27 | [streaming](27-streaming.md) | 7 stream modes, SSE, debug output |
| 28 | [comparison](28-comparison.md) | LangGraph vs our orchestrator |
| 29 | [lessons-learned](29-lessons-learned.md) | Key takeaways and priority roadmap |

## Quick Start

Start with **[00-overview](00-overview.md)** for the big picture, then **[28-comparison](28-comparison.md)** for how it relates to our project, and **[29-lessons-learned](29-lessons-learned.md)** for actionable next steps.

---

*Analysis date: 2026-03-06*
*Source: github.com/langchain-ai/langgraph (shallow clone)*
