# Observability Upgrade — OpenTelemetry + Fine-Grained Monitoring

> **Status: Implemented.** Phases 1 and 2 (distributed tracing, metrics bridge, alert pipeline) are live in production. See section 11 for the alert-to-PR pipeline added after initial implementation.

## 1. Executive Summary

The current observability stack (custom MetricsRegistry + Prometheus + Grafana) covers aggregate metrics well but has **zero distributed tracing**. This means there is no way to answer:

- "Which graph node is hanging on this team run?"
- "Was the failure caused by the frontend, backend, or an LLM timeout?"
- "Which agent in a 24-agent delegation chain caused the slowdown?"

This document proposes adding **OpenTelemetry tracing** to the orchestrator, enabling 24-hour fine-grained diagnostics across all layers.

---

## 2. Current State — What We Have

### 2.1 Metrics (40+ metric types)

| Component | File | What it tracks |
|-----------|------|---------------|
| MetricsRegistry | `core/metrics.py` | Counters, gauges, histograms per agent/provider |
| HealthMonitor | `core/health.py` | Provider latency (sliding window), error rate, availability |
| UsageTracker | `core/usage.py` | Token counts, cost per task/session/day, budget enforcement |
| AuditLog | `core/audit.py` | 11 event types (agent.start, tool.call, provider.error, etc.) |
| AlertManager | `core/alerts.py` | Spend rules (per task/session/day), webhook dispatch |

### 2.2 Prometheus Stack

| Service | Port | What it scrapes |
|---------|------|----------------|
| Dashboard `/metrics` | 5005 | 11 metric families (requests, tokens, cost, errors, cache) |
| Node Exporter | 9100 | Host CPU/RAM/disk |
| cAdvisor | 8080 | Container metrics |
| AWS Cost Exporter | 9101 | Daily/monthly AWS costs, S3 stats |
| Prometheus self | 9090 | Internal health |

### 2.3 Grafana Dashboards (8)

orchestrator, errors, agents, cost-analysis, api-calls, infrastructure, aws-costs, s3

### 2.4 Alerting Rules (8)

HighErrorRate, AgentStalled, HighCostSpike, HighCPU, HighMemory, LowDiskSpace, S3HighErrorRate, S3BucketLarge

### 2.5 Error Tracking

- PostgreSQL `agent_errors` table with 5 classifications: `command_not_found`, `exit_code_error`, `timeout`, `not_allowed`, `tool_error`
- API: `GET /api/errors` (recent 100 + summary by agent/type)

---

## 3. Current Gaps

### 3.1 No Distributed Tracing

There is no way to follow a single request through the full execution chain:

```
HTTP request → run_team() → team-lead plan → agent.execute() × N
  → graph node → Provider.complete() (LLM call) → tool execution
```

Today these are all isolated events. If something hangs or fails, you must manually correlate timestamps across logs and metrics.

### 3.2 No Frontend Error Reporting

Client-side JavaScript errors go to `console.error()` and are never sent back to the server. There is no visibility into:

- UI rendering failures
- WebSocket disconnections
- Failed API calls from the browser

### 3.3 Graph Node Timing Not in Prometheus

`StreamEvent.elapsed_ms` is emitted during execution but never aggregated into Prometheus metrics. You cannot query "p95 latency of graph node X over the last 24 hours."

### 3.4 LLM Call Latency Not Isolated

`Provider.complete()` latency is mixed into agent execution time. If an agent takes 45 seconds, you cannot tell whether 40 seconds was the LLM call or the tool execution.

### 3.5 Unbounded Histogram Memory

`metrics.py` `Histogram._observations` is a `list[float]` that grows forever. At 1000 tasks/day × 5 histograms = 5000 floats/day, never freed.

### 3.6 Missing Alerts for 24h Granularity

No alerts for:

- Individual graph node hanging (only overall agent stall at 300s)
- LLM calls without response
- Frontend error spikes
- Per-category agent stall rate

---

## 4. Proposed Solution: OpenTelemetry

### 4.1 Why OpenTelemetry

| Criteria | Custom metrics (current) | OpenTelemetry |
|----------|------------------------|---------------|
| Distributed tracing | Impossible | Native |
| Correlate spike → specific request | No | Yes (exemplars) |
| Agent delegation chain visibility | No | Full waterfall |
| Async Python support | N/A | Native (contextvars) |
| FastAPI auto-instrumentation | Manual | One-line setup |
| Coexists with Prometheus | N/A | Yes, side by side |
| LLM semantic conventions | Custom | Standardized (`gen_ai.*`) |

### 4.2 Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Browser                              │
│  window.onerror → POST /api/errors/client                   │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│              FastAPI (auto-instrumented)                     │
│  Every route → automatic span with HTTP attributes          │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                  Execution Layer                             │
│                                                             │
│  run_team() ─── span: team.run                              │
│    │                                                        │
│    ├── team-lead plan ─── span: agent.run (planner)         │
│    │     └── Provider.complete() ─── span: llm.call         │
│    │                                                        │
│    ├── agent.execute() × N ─── span: agent.run (parallel)   │
│    │     ├── graph node A ─── span: graph.node              │
│    │     │     └── Provider.complete() ─── span: llm.call   │
│    │     ├── graph node B ─── span: graph.node              │
│    │     │     └── SkillRegistry.execute() ─── span: tool   │
│    │     └── cooperation.send() ─── span: agent.message     │
│    │           (traceparent injected into AgentMessage)      │
│    │                                                        │
│    └── team-lead review ─── span: agent.run (summarizer)    │
│          └── Provider.complete() ─── span: llm.call         │
│                                                             │
└──────────────────────┬──────────────────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
    ┌──────────┐ ┌──────────┐ ┌──────────┐
    │ Tempo    │ │Prometheus│ │ Postgres │
    │ (traces) │ │(metrics) │ │ (errors) │
    └────┬─────┘ └────┬─────┘ └──────────┘
         │            │
         └─────┬──────┘
               ▼
         ┌──────────┐
         │ Grafana  │
         │ unified  │
         └──────────┘
```

### 4.3 What Each Span Captures

#### LLM Calls (`Provider.complete()`)

| Attribute | Example |
|-----------|---------|
| `gen_ai.system` | `anthropic` |
| `gen_ai.request.model` | `claude-sonnet-4-5` |
| `gen_ai.usage.input_tokens` | `1523` |
| `gen_ai.usage.output_tokens` | `847` |
| `gen_ai.operation.name` | `chat` |
| span duration | `3200ms` |
| error (if timeout) | `TimeoutError: 30s exceeded` |

#### Graph Nodes (`CompiledGraph._run_node()`)

| Attribute | Example |
|-----------|---------|
| `graph.node.name` | `analyze_data` |
| `graph.node.type` | `llm` / `tool` / `router` |
| `graph.run_id` | `uuid` |
| `graph.step` | `3` |
| span duration | `5400ms` |
| error (if hung) | `TimeoutError: node timed out after 300s` |

#### Agent Execution (`Agent.execute()`)

| Attribute | Example |
|-----------|---------|
| `agent.name` | `backend` |
| `agent.category` | `software-engineering` |
| `agent.provider` | `anthropic` |
| `agent.tools` | `["file_read", "shell_exec"]` |
| span duration | `12000ms` |

#### Inter-Agent Messages (`CooperationProtocol`)

| Attribute | Example |
|-----------|---------|
| `agent.from` | `team-lead` |
| `agent.to` | `backend` |
| `agent.message.type` | `delegation` |
| `agent.task_id` | `task-42` |

The `traceparent` header is injected into `AgentMessage.metadata`, so when agent B receives a delegation from agent A, B's spans appear as children of A's trace. The full delegation chain is one waterfall in Grafana.

---

## 5. Diagnostic Scenarios (24h window)

### Scenario 1: "The UI is broken"

**Today**: nothing visible server-side.

**With OTel**: Frontend sends errors to `POST /api/errors/client`. Grafana alert fires on `rate(frontend_errors_total[5m]) > 1`. The error payload includes component, stack trace, and session ID. You can correlate it with the backend trace for that session.

### Scenario 2: "A backend piece is stuck"

**Today**: you see `AgentStalled` alert after 300s. You don't know which part of the execution is stuck.

**With OTel**: open the trace in Grafana Tempo. The waterfall shows:

```
team.run ─────────────────────────────── 300s (timeout)
  ├── agent.run (backend) ──────────── 298s ← this one
  │     ├── graph.node (plan) ──────── 2s ✓
  │     ├── graph.node (execute) ───── 296s ← stuck here
  │     │     └── llm.call ─────────── 296s ← LLM not responding
  │     └── graph.node (review) ────── (never reached)
  └── agent.run (frontend) ─────────── 15s ✓
```

Immediately visible: the Anthropic API is not responding for the backend agent's execute step.

### Scenario 3: "A graph dependency didn't respond"

**Today**: timeout after 300s, no detail on which node.

**With OTel**: the trace shows parallel nodes as concurrent spans. The one that timed out is visually obvious in the waterfall. Its child span (`llm.call` or `tool.exec`) shows the specific failure.

### Scenario 4: "LLM calls are slow today"

**Today**: you can see overall agent latency but not LLM-specific latency.

**With OTel**: query Tempo:

```
{ span.name = "llm.call" && gen_ai.system = "anthropic" } | quantile_over_time(duration, 0.95) > 10s
```

Or in Grafana: histogram panel on `llm_call_duration_seconds` with provider/model labels. 24h view shows exactly when latency spiked and for which provider.

---

## 6. New Alerts (24h granularity)

```yaml
# Graph node hung > 60s (catches stuck nodes before the 300s overall timeout)
- alert: GraphNodeHung
  expr: histogram_quantile(0.99, graph_node_duration_seconds_bucket) > 60
  for: 1m
  labels:
    severity: warning
  annotations:
    summary: "Graph node p99 latency > 60s"

# LLM call without response > 30s
- alert: LLMCallSlow
  expr: histogram_quantile(0.95, llm_call_duration_seconds_bucket) > 30
  for: 2m
  labels:
    severity: warning
  annotations:
    summary: "LLM call p95 latency > 30s (provider: {{ $labels.provider }})"

# Frontend error spike
- alert: FrontendErrorSpike
  expr: rate(frontend_errors_total[5m]) > 1
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Frontend errors > 5/min"

# Per-category agent stall rate
- alert: CategoryStallRate
  expr: rate(agent_stalls_total[1h]) > 0.1
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "Agent stall rate > 10%/h in {{ $labels.category }}"

# Provider degradation (error rate spike in 1h window)
- alert: ProviderDegraded
  expr: rate(llm_call_errors_total[1h]) / rate(llm_call_total[1h]) > 0.2
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Provider {{ $labels.provider }} error rate > 20%"
```

---

## 7. Implementation Plan

### Phase 1 — Distributed Tracing (high value, low risk)

**Packages to add in `pyproject.toml`:**

```toml
# OpenTelemetry core
"opentelemetry-api>=1.27",
"opentelemetry-sdk>=1.27",

# Auto-instrumentation
"opentelemetry-instrumentation-fastapi>=0.48b0",
"opentelemetry-instrumentation-httpx>=0.48b0",
"opentelemetry-instrumentation-asyncpg>=0.48b0",

# Export
"opentelemetry-exporter-otlp-proto-http>=1.27",

# LLM conventions
"opentelemetry-semantic-conventions>=0.49b0",
```

**Custom instrumentation points (5 files to modify):**

| File | What to wrap | Span name |
|------|-------------|-----------|
| `core/provider.py` | `complete()` method | `llm.call` |
| `core/graph.py` | Node execution in `_run_node()` | `graph.node` |
| `core/agent.py` | `execute()` method | `agent.run` |
| `core/cooperation.py` | `send()` / `receive()` | `agent.message` |
| `dashboard/agent_runner.py` | `run_team()` | `team.run` |

**New infrastructure (1 container):**

```yaml
# docker-compose.prod.yml
tempo:
  image: grafana/tempo:2.6
  command: ["-config.file=/etc/tempo.yaml"]
  volumes:
    - ./docker/tempo/tempo.yaml:/etc/tempo.yaml
    - tempo_data:/var/tempo
  ports:
    - "127.0.0.1:3200:3200"   # Tempo API (internal only)
    - "127.0.0.1:4318:4318"   # OTLP HTTP receiver
  restart: unless-stopped
```

Tempo config (~20 lines): receive OTLP, store locally, 7-day retention, ~200MB disk per million traces.

**Grafana datasource addition:**

```yaml
# docker/grafana/provisioning/datasources/tempo.yml
apiVersion: 1
datasources:
  - name: Tempo
    type: tempo
    url: http://tempo:3200
    jsonData:
      tracesToMetrics:
        datasourceUid: prometheus
      nodeGraph:
        enabled: true
```

**Frontend error endpoint (1 new route):**

```python
# dashboard/app.py
@app.post("/api/errors/client")
async def report_client_error(payload: dict):
    # Store in agent_errors table with source="frontend"
    ...
```

**Frontend hook (5 lines in app.js):**

```javascript
window.addEventListener('error', (e) => {
    fetch('/api/errors/client', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            component: 'ui',
            message: e.message,
            source: e.filename,
            line: e.lineno,
            session_id: currentSessionId
        })
    }).catch(() => {});
});
```

### Phase 2 — Metrics Bridge (optional, fixes memory leak)

**Package:**

```toml
"opentelemetry-exporter-prometheus>=1.27",
```

**Work:** Write a thin subclass of `MetricsRegistry` that delegates `inc()`, `set()`, `observe()` to OTel meters internally. All existing call sites remain unchanged. This replaces the unbounded `list[float]` in `Histogram` with OTel's bucketed histogram aggregation.

---

## 8. Resource Overhead

| Component | CPU | Memory | Disk | Network |
|-----------|-----|--------|------|---------|
| OTel SDK (tracing) | ~1-3μs per span | ~5-15MB buffer | 0 | Async batch export |
| OTel SDK (metrics) | Negligible for 360 time series | ~2MB | 0 | Via Prometheus scrape |
| Tempo container | Minimal (write-path only) | ~100MB | ~200MB per 1M traces | OTLP ingest |
| Prometheus (unchanged) | Unchanged | Unchanged | Unchanged | +1 scrape target |

Total additional resource cost: **~120MB RAM, ~200MB disk/week**.

---

## 9. What NOT to Do

- **Don't replace MetricsRegistry immediately.** It works fine. Phase 2 is optional.
- **Don't add Jaeger.** Tempo does the same thing and integrates natively with your Grafana.
- **Don't add `opentelemetry-distro`.** It conflicts with explicit FastAPI startup.
- **Don't add log instrumentation** (`opentelemetry-instrumentation-logging`) without a log backend (Loki). Injecting trace IDs into logs nobody queries adds noise.
- **Don't over-instrument.** Start with the 5 custom span points listed above. Add more only when a specific diagnostic need arises.

---

## 10. Expected Outcome

After Phase 1 implementation:

| Problem | Before | After |
|---------|--------|-------|
| "Which graph node is stuck?" | Unknown until 300s timeout | Visible in trace waterfall within seconds |
| "Is it the LLM or the tool?" | Cannot distinguish | Separate spans with independent timing |
| "Frontend broken?" | Invisible server-side | `frontend_errors_total` metric + alert |
| "Which agent in the chain failed?" | Manual log correlation | Single trace across all 24 agents |
| "Provider X slow today?" | Aggregate latency only | Per-provider p95 histogram, 24h view |
| "Click Grafana spike → see what happened" | Impossible | Exemplar links to Tempo trace |

---

## 11. Alert-to-PR Pipeline (Implemented)

When alerts fire, the system automatically creates GitHub issues with diagnostic context
and triggers LLM-powered root-cause analysis:

1. Grafana webhook → `POST /api/alerts/webhook`
2. Dashboard collects diagnostics (recent errors, usage, metrics) via `dashboard/alert_webhook.py`
3. `gh issue create` with structured diagnostic report (labels: `alert`, `automated`)
4. `.github/workflows/alert-analysis.yml` triggers on the `alert` label
5. LLM (qwen3-235b via OpenRouter) analyzes the diagnostic report and comments with root cause and recommended actions
6. Issue gets `needs-triage` label for human review

New alerts added alongside this pipeline: `GraphNodeHung`, `LLMCallSlow`, `FrontendErrorSpike`, `ProviderDegraded` (see section 6 for full alert definitions).
