---
sidebar_position: 4
title: "v0.6.0: Production Hardening"
---

# v0.6.0 — Production Hardening ✅

Make it reliable enough for real workloads.

**Status:** Core features completed — rate limiting, audit log, task queue, metrics, alerts, and auth middleware all implemented and tested (201 tests passing).

## New Modules

| Module | Description |
|--------|-------------|
| `core/rate_limiter.py` | Per-provider sliding-window rate limiter (requests + tokens) |
| `core/audit.py` | Structured audit log with filtering and JSON export |
| `core/task_queue.py` | Priority task queue with retry logic (Postgres-ready interface) |
| `core/metrics.py` | Counter, Gauge, Histogram + MetricsRegistry with Prometheus export |
| `core/alerts.py` | Spend alert rules with dedup and webhook support |
| `dashboard/auth.py` | API key authentication middleware for FastAPI |

## Features

### Local (Ollama)

| ID | Feature | Status | Detail |
|----|---------|--------|--------|
| PROD-01 | Local model registry | Planned | Track pulled models, sizes, last used date |
| PROD-02 | Ollama auto-pull | Planned | Auto-pull missing models before execution |
| PROD-03 | GPU memory management | Planned | Monitor VRAM, prevent OOM by queuing |
| PROD-04 | Local inference metrics | ✅ | `MetricsRegistry` with `default_metrics()` — tok/s, latency histograms |

### Cloud (OpenRouter)

| ID | Feature | Status | Detail |
|----|---------|--------|--------|
| PROD-05 | API key rotation | Planned | Multiple keys with round-robin |
| PROD-06 | Rate limiting | ✅ | `RateLimiter` with 60s sliding window per provider |
| PROD-07 | Retry with backoff | Partial | Basic 429 fallback exists; exponential backoff planned |
| PROD-08 | Spend alerts | ✅ | `AlertManager` with configurable rules, dedup, webhook support |

### Both (Core)

| ID | Feature | Status | Detail |
|----|---------|--------|--------|
| PROD-09 | Persistent task queue | ✅ | `TaskQueue` in-memory with Postgres-ready interface |
| PROD-10 | Authentication | ✅ | `APIKeyMiddleware` — header/query param, static bypass, dev mode |
| PROD-11 | Audit log | ✅ | `AuditLog` with 11 event types, filtering, task trace, JSON export |
| PROD-12 | Health checks | Planned | `/health` endpoint with per-provider status |
| PROD-13 | Metrics export | ✅ | `MetricsRegistry.export_prometheus()` — valid Prometheus text format |

## Key APIs

### RateLimiter

```python
from agent_orchestrator.core.rate_limiter import RateLimiter, RateLimitConfig

limiter = RateLimiter([
    RateLimitConfig(requests_per_minute=60, tokens_per_minute=100000, provider_key="openrouter"),
])
if await limiter.acquire("openrouter", estimated_tokens=2000):
    # make the request
    limiter.record_usage("openrouter", tokens=1500)
```

### AuditLog

```python
from agent_orchestrator.core.audit import AuditLog, EVENT_AGENT_START

log = AuditLog()
log.log_action(EVENT_AGENT_START, "backend", "Starting API build", task_id="t1")
trace = log.get_task_trace("t1")  # all events for task t1
exported = log.export_json()       # JSON-serializable list
```

### TaskQueue

```python
from agent_orchestrator.core.task_queue import TaskQueue, QueuedTask

queue = TaskQueue()
queue.enqueue(QueuedTask(task_id="t1", description="Build API", priority=10))
task = queue.dequeue()  # highest priority first
queue.complete("t1", "API built successfully")
stats = queue.get_stats()  # pending, running, completed, failed
```

### Metrics

```python
from agent_orchestrator.core.metrics import MetricsRegistry, default_metrics

registry = default_metrics()
registry.counter("agent_tasks_total", labels={"agent": "backend", "status": "completed"}).inc()
registry.histogram("agent_latency_seconds", labels={"agent": "backend"}).observe(2.5)
print(registry.export_prometheus())
```

### AlertManager

```python
from agent_orchestrator.core.alerts import AlertManager, AlertRule

manager = AlertManager([
    AlertRule(name="daily_limit", threshold_usd=5.0, period="day", action="log"),
    AlertRule(name="task_limit", threshold_usd=0.50, period="task", action="webhook",
             webhook_url="https://hooks.slack.com/..."),
])
alerts = manager.check(current_spend=6.0, period="day")
```

### APIKeyMiddleware

```python
from agent_orchestrator.dashboard.auth import APIKeyMiddleware

# In FastAPI app setup:
app.add_middleware(APIKeyMiddleware, api_keys=["secret-key-1", "secret-key-2"])
# No keys = dev mode (all requests allowed)
```
