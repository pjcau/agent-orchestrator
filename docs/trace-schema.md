# Trace Schema

This document inventories every span the agent orchestrator emits, the
attributes each span carries, the events (inner timestamps) it may record, and
where in the codebase it originates.

---

## Span inventory

### `llm.call`

Emitted by `Provider.traced_complete()` — one span per LLM API call.

| | |
|---|---|
| **File** | `src/agent_orchestrator/core/provider.py:146` |
| **Duration** | Wall time of the underlying `complete()` call, including network latency. |

**Required attributes**

| Attribute | Type | Example |
|-----------|------|---------|
| `gen_ai.system` | string | `anthropic` |
| `gen_ai.request.model` | string | `claude-sonnet-4-5` |
| `gen_ai.request.max_tokens` | int | `4096` |
| `gen_ai.operation.name` | string | `chat` |

**Optional attributes** (set only on success)

| Attribute | Type | Example |
|-----------|------|---------|
| `gen_ai.usage.input_tokens` | int | `1523` |
| `gen_ai.usage.output_tokens` | int | `847` |
| `gen_ai.usage.cost_usd` | float | `0.00342` |

**Events**

| Event | When |
|-------|------|
| `exception` | On any exception inside `complete()` (via `span.record_exception()`) |

**Status**: set to `ERROR` with the exception message on failure.

---

### `agent.run`

Emitted by `Agent._execute_with_provider()` — one span per agent task
execution (wraps the full multi-step loop).

| | |
|---|---|
| **File** | `src/agent_orchestrator/core/agent.py:171` |
| **Duration** | Total time for all steps including LLM calls and tool execution. |

**Required attributes**

| Attribute | Type | Example |
|-----------|------|---------|
| `agent.name` | string | `backend` |
| `agent.provider` | string | `claude-sonnet-4-5` |
| `agent.max_steps` | int | `20` |

**Optional attributes** (set at span end)

| Attribute | Type | Example |
|-----------|------|---------|
| `agent.steps_taken` | int | `7` |
| `agent.total_tokens` | int | `12450` |
| `agent.total_cost_usd` | float | `0.015` |
| `agent.status` | string | `completed` / `stalled` / `failed` |

**Events**: none (exceptions propagate to the caller).

---

### `graph.node`

Emitted by `CompiledGraph._run_node()` — one span per graph node execution.

| | |
|---|---|
| **File** | `src/agent_orchestrator/core/graph.py:649` (standard path), `graph.py:748` (conditional edge path) |
| **Duration** | Time to run the node function, excluding scheduling overhead. |

**Required attributes**

| Attribute | Type | Example |
|-----------|------|---------|
| `graph.node.name` | string | `analyze_data` |
| `graph.step` | int | `3` |

**Events**

| Event | When |
|-------|------|
| `exception` | On `asyncio.TimeoutError` or any unhandled node exception |

**Status**: set to `ERROR` with description on timeout or unhandled exception.

---

### `agent.message`

Emitted by `CooperationProtocol.send_message()` — one span per inter-agent
message sent.

| | |
|---|---|
| **File** | `src/agent_orchestrator/core/cooperation.py:143` |
| **Duration** | Near-zero (fire-and-forget; the span covers only the dispatch, not delivery). |

**Required attributes**

| Attribute | Type | Example |
|-----------|------|---------|
| `agent.from` | string | `team-lead` |
| `agent.to` | string | `backend` (or `broadcast`) |
| `agent.message.type` | string | `delegation` |

**Optional attributes**

| Attribute | Type | Example |
|-----------|------|---------|
| `agent.task_id` | string | `task-42` |

**Events**: none.

---

## How to view traces

### Tempo (existing)

Tempo is the primary trace backend for production deployments.

1. Open **Grafana** at `https://monitoring.agents-orchestrator.com`.
2. Navigate to **Explore → Tempo** datasource.
3. Query by service name (`agent-orchestrator`) or filter on span attributes:

   ```
   { span.name = "llm.call" && gen_ai.system = "anthropic" }
   ```

4. Click any trace to open the waterfall view showing the full execution chain
   (`agent.run` → `graph.node` → `llm.call`).

The OTLP endpoint is configured via `OTEL_EXPORTER_OTLP_ENDPOINT` on the
dashboard container (e.g. `http://tempo:4318`).  See
[deployment.md](deployment.md) for Tempo container configuration.

---

### Langfuse (optional)

Langfuse adds a prompt/completion-aware UI on top of the same OTel spans.

**Setup:**

1. Install the extra: `pip install "agent-orchestrator[langfuse]"`
2. Set environment variables on the dashboard container (or locally):

   ```env
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   LANGFUSE_HOST=https://cloud.langfuse.com   # optional; default shown
   ```

3. Restart the dashboard.  `register_langfuse_exporter()` is called
   automatically at the end of `setup_tracing()` when the vars are present.

**Viewing traces:**

- Open `https://cloud.langfuse.com` (or your self-hosted instance).
- All `llm.call` spans appear as **generations** with input/output token
  counts and cost automatically populated from the `gen_ai.*` attributes.
- `agent.run` spans appear as **traces** grouping all child LLM calls.

**No Tempo impact:** Langfuse runs as a second `BatchSpanProcessor` on the
same `TracerProvider`.  Tempo and Langfuse receive spans concurrently.

---

### Phoenix (optional)

Arize Phoenix provides an OTel-native LLM trace UI with latency heatmaps,
hallucination scoring, and prompt playground.

**Setup:**

1. Install the extra: `pip install "agent-orchestrator[phoenix]"`
2. Set environment variables:

   ```env
   PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006   # optional; default shown
   PHOENIX_API_KEY=<key>                              # optional; Arize cloud only
   ```

3. Run Phoenix locally:

   ```bash
   pip install arize-phoenix
   python -m phoenix.server.main
   ```

   Or use the Docker image:

   ```bash
   docker run -p 6006:6006 arizephoenix/phoenix:latest
   ```

4. Restart the dashboard.  `register_phoenix_exporter()` is called
   automatically at the end of `setup_tracing()` when the SDK TracerProvider
   is active (no key required for the local dev server).

**Viewing traces:**

- Open `http://localhost:6006` (Phoenix local UI).
- Spans are forwarded via OTLP/HTTP to `/v1/traces` on the configured endpoint.
- `llm.call` spans are surfaced with `gen_ai.*` attribute inspection.

**No Tempo impact:** Phoenix is a third `BatchSpanProcessor`.  All three
sinks (Tempo, Langfuse, Phoenix) run concurrently.

---

## Attribute naming conventions

All custom attributes follow the [OTel semantic conventions for generative AI](https://opentelemetry.io/docs/specs/semconv/gen-ai/) where applicable:

- `gen_ai.*` — LLM request/response metadata (model, tokens, cost)
- `agent.*` — agent execution metadata (name, provider, steps)
- `graph.*` — graph node metadata (name, step index)

This ensures compatibility with any OTel-aware backend without custom parsers.
