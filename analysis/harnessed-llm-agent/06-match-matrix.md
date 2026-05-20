# 06 — Match Matrix

One-shot view of every component in the diagram vs the current `agent-orchestrator` codebase.

## Legend

- ✅ **HAVE** — implemented and in use
- ⚠️ **PARTIAL** — foundation present, but a significant piece is missing
- ❌ **MISSING** — not implemented

## Full matrix

| # | Area | Component | Status | Code locations | Notes |
|---|------|-----------|--------|---------------|-------|
| 1 | Harness | Runtime loop | ✅ | `core/orchestrator.py`, `core/agent.py` | Mature |
| 2 | Harness | StateGraph | ✅ | `core/graph.py`, `llm_nodes.py`, `channels.py` | Parity with LangGraph basics |
| 3 | Harness | Checkpointing | ✅ | `core/checkpoint.py`, `checkpoint_postgres.py` | InMem + Postgres |
| 4 | Harness | Embedded client | ✅ | `client.py` | No HTTP required |
| 5 | Skills | Operational Procedure | ✅ | `core/skill.py`, `graph_templates.py`, `skills/*` | 19 skills, middleware chain |
| 6 | Skills | Normative Constraints | ✅ | `core/guardrails.py`, `core/audit.py`, `loop_detection.py`, `memory_filter.py` | Unified GuardrailManager with PIIScanner, SecretsScanner, PromptInjectionDetector, OutputSchemaGuard, CostGuard; YAML config; Agent.execute() pre/post hooks |
| 7 | Skills | Decision Heuristics | ✅ | `core/router.py`, `provider_presets.py`, `health.py` | 6 strategies, category-aware |
| 8 | Memory | Working Context | ✅ | `core/conversation.py`, `checkpoint_postgres.py` | Threads, fork, restore |
| 9 | Memory | Semantic Knowledge (RAG) | ❌ | — | No vector store, no embeddings, no retriever |
| 10 | Memory | Episodic Experience | ✅ | `core/store.py`, `store_postgres.py` | 30-day TTL, injection into prompt |
| 11 | Memory | Personalized Memory | ✅ | `core/personalized_memory.py`, `skills/profile_extractor_skill.py`, `dashboard/personalized_memory_routes.py` | `("user", id)` namespace, `<user_profile>` injection, GDPR wipe, HTTP API |
| 12 | Protocols | Agent ↔ User | ✅ | `core/clarification.py`, `dashboard/sse.py` | 5 typed categories, SSE HITL |
| 13 | Protocols | Agent ↔ Agent | ✅ | `core/cooperation.py`, `core/cooperation_messages.py`, `docs/cooperation-protocol.md`, `mcp_server.py`, `mcp_client.py` | Documented + typed messages (P5a). A2A adapter (P5b) still deferred. |
| 14 | Orbital | Sub-Agent Orchestration | ✅ | `core/orchestrator.py`, team-lead, `graph_patterns.py` | 30 agents, 5 categories |
| 15 | Orbital | Sandbox | ✅ | `core/sandbox.py`, `dashboard/sandbox_manager.py` | Docker + local, port pool |
| 16 | Orbital | Observability | ✅ | `core/tracing.py`, `metrics.py`, `audit.py`, Tempo, `core/observability/` | OTel + Prometheus + Grafana + Langfuse (opt-in) + Phoenix (opt-in) |
| 17 | Orbital | Compression | ✅ | `SummarizationConfig`, progressive skill loading | Trigger 50, retain 10 |
| 18 | Orbital | Approval Loop | ✅ | `clarification.py`, SSE `RunManager` | Blocking + non-blocking |
| 19 | Orbital | Evaluator | ✅ | `core/evaluator.py`, `evals/datasets/smoke.json`, `evals/runners/cli.py`, `dashboard/evals_routes.py` | RubricEvaluator + LLMJudge + EvalSuite + REST API — P2 complete |

## Scoreboard

- **Total components**: 19
- **✅ HAVE**: 15 (79%)
- **⚠️ PARTIAL**: 3 (16%)
- **❌ MISSING**: 1 (5%)

## Biggest gaps, ranked by unlock

1. **#9 Semantic Knowledge (RAG)** — the only ❌ remaining. Unlocks document Q&A, code search, and domain knowledge for every agent. High.
2. **#11 Personalized Memory** — tiny diff on top of the existing store. Low effort, decent UX win. Medium.
3. **#13 Agent-Agent protocol formalization (strategic)** — tactical part shipped (typed messages + spec, P5a). Strategic A2A adapter (P5b) still wait-and-see. Low urgency.

Already shipped in this round: #6 Guardrails (P3) and #19 Evaluator (P2).
