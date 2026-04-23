# 05 — Orbital Modules

The 6 items floating around the central *Harness* in the diagram. They are *cross-cutting* — each one touches multiple layers.

## 5a. Sub-Agent Orchestration

### Motivation
Split work across specialized agents. One coordinator, many workers. The coordinator decomposes, delegates, reviews, merges.

### Reference implementations
- [crewAIInc/crewAI](https://github.com/crewAIInc/crewAI)
- [microsoft/autogen](https://github.com/microsoft/autogen)
- [joaomdmoura/crewai](https://github.com/joaomdmoura/crewai)
- [OpenBMB/ChatDev](https://github.com/OpenBMB/ChatDev)

### Match — ✅ HAVE
- `core/orchestrator.py`, `Orchestrator.run_team()`
- `team-lead` agent — plan → delegate → review workflow
- `core/graph_patterns.py` — `SubGraphNode`, map-reduce, retry loops
- 30 agents across 5 categories (software, data-science, finance, marketing, tooling)

---

## 5b. Sandbox

### Motivation
Agents generate code and commands — **never** run them on the host. Sandbox them in a container, network-controlled, filesystem-isolated, time-limited.

### Reference implementations
- [e2b-dev/e2b](https://github.com/e2b-dev/e2b) — cloud sandbox for AI
- [modal-labs/modal](https://github.com/modal-labs/modal) — serverless execution
- [microsoft/autogen](https://github.com/microsoft/autogen) — docker code executor

### Match — ✅ HAVE
- `core/sandbox.py` — Docker + local backends
- `SandboxConfig` — image, timeout, memory, CPU, network policy, writable paths, exposed ports, startup command, env vars
- `PortMapping` — auto-assign or explicit port forwarding
- `dashboard/sandbox_manager.py` — session-scoped, lazy init, LRU eviction, port pool (9000-9099)
- `SandboxedShellSkill` — drop-in sandboxed shell for agents
- Dashboard terminal: `WS /ws/sandbox/{session_id}/terminal` (xterm.js)
- SSE log streaming, introspection API

---

## 5c. Observability

### Motivation
Agents are non-deterministic. If you can't see what happened, you can't debug, improve, or audit.

### Reference implementations
- [langfuse/langfuse](https://github.com/langfuse/langfuse) — LLM-native traces + evals
- [Arize-ai/phoenix](https://github.com/Arize-ai/phoenix) — OSS observability for LLMs
- [open-telemetry/opentelemetry-python](https://github.com/open-telemetry/opentelemetry-python) — generic OTel
- [traceloop/openllmetry](https://github.com/traceloop/openllmetry) — OTel conventions for LLM apps

### Match — ✅ HAVE
- `core/tracing.py` — OpenTelemetry setup (opt-in, no-op fallback)
- OTLP export to **Tempo** (7-day retention) — running in prod
- `core/metrics.py` — Prometheus metrics, exported at `/metrics`
- `core/audit.py` — 11 event types, filtering, task traces
- `dashboard/tracing_metrics.py` — lightweight collector (LLM durations, node durations, stall counts)
- Grafana dashboards + alerts → GitHub issues + LLM root-cause analysis
- Real-time cache hit/miss/rate in dashboard

### Minor improvement
Add a **Langfuse exporter** alongside OTel — gives LLM-native trace viewer UX (prompt diffs, response comparison). Also consider **Phoenix** for eval-friendly traces.

---

## 5d. Compression

### Motivation
Context windows are finite. Even 1M-token models degrade at long contexts. You need strategies to compress: summarize old turns, drop irrelevant skills, chunk documents.

### Reference implementations
- [cpacker/MemGPT](https://github.com/cpacker/MemGPT) — recursive summarization
- [microsoft/autogen](https://github.com/microsoft/autogen) — summarization strategies
- [langchain-ai/langchain](https://github.com/langchain-ai/langchain) — `ConversationSummaryMemory`

### Match — ✅ HAVE
- `SummarizationConfig` in `ConversationManager` — triggers at 50 messages, retains last 10 verbatim, replaces the rest with a system summary
- Metrics: `conversation_summarization_total`, `conversation_tokens_saved`
- **Progressive skill loading** — only `SkillSummary` in base prompt, `load_skill` fetches details on demand
- **Memory filter** — drops session-scoped file paths from persisted messages

---

## 5e. Approval Loop

### Motivation
Humans stay in the loop on high-stakes actions. Two flavors: **pre-approval** (block before action) and **post-review** (review, roll back if needed).

### Reference implementations
- [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) — `interrupt_before` / `interrupt_after`
- [humanlayer/humanlayer](https://github.com/humanlayer/humanlayer) — approval APIs, Slack escalation

### Match — ✅ HAVE
- HITL in `sse.py` / `RunManager` — interrupt + resume
- `clarification.py` blocking mode — agent pauses until human responds (5-min timeout)
- Dashboard UI: Approve/Reject buttons, option pills
- Resume API: `POST /api/runs/{run_id}/resume`
- `HITLConfig`: `enabled`, `timeout_seconds`, `auto_approve` (for tests)

---

## 5f. Evaluator

### Motivation
Without evaluation, you can't improve. You need:

- **Unit-level** evals (one prompt, one rubric)
- **Task-level** evals (whole agent runs)
- **Regression suites** (don't break what worked)
- **LLM-as-judge** when humans don't scale
- **Eval datasets** pinned and versioned

### Reference implementations

| Repo | Pattern |
|------|---------|
| [openai/evals](https://github.com/openai/evals) | Eval framework, dataset-driven |
| [confident-ai/deepeval](https://github.com/confident-ai/deepeval) | Pytest-style LLM evals |
| [explodinggradients/ragas](https://github.com/explodinggradients/ragas) | RAG-specific evaluation |
| [stanfordnlp/dspy](https://github.com/stanfordnlp/dspy) | Evaluation + compilation |
| [langchain-ai/langsmith](https://github.com/langchain-ai/langsmith) | Hosted eval + traces (paid) |
| [uptrain-ai/uptrain](https://github.com/uptrain-ai/uptrain) | Open-source eval platform |

### Match — ⚠️ PARTIAL

What we have:

- `core/benchmark.py` — throughput, latency, cost benchmarks
- `core/conformance.py` — Provider / Checkpointer / Store conformance suites
- `core/smoke_tester.py` — syntax-level check post-team-run (20 languages)
- Usage tracking + cost per agent in `usage_db`

What is missing:

- **No LLM-as-judge** framework
- **No eval datasets** (golden set of prompts/expected outputs)
- **No rubric-based scoring**
- **No regression tracking** across model/prompt changes
- **No eval UI** (compare runs side-by-side)

### Gap to close

Add `core/evaluator.py`:

```python
class Evaluator(ABC):
    async def evaluate(self, run: AgentRun, rubric: Rubric) -> EvalResult: ...

class LLMJudge(Evaluator):
    # Use a strong model to score against a rubric
    ...

class RubricEvaluator(Evaluator):
    # Deterministic checks (regex, contains, length, schema)
    ...

class EvalSuite:
    def run(self, dataset: Dataset, agent: Agent, evaluators: list[Evaluator]) -> EvalReport: ...
```

Plus:
- `evals/` directory with golden datasets
- `GET /api/evals/runs`, `GET /api/evals/runs/{id}` in the dashboard
- CI integration — fail PRs that regress eval scores
- Optionally: adopt `deepeval` or `ragas` instead of building from scratch
