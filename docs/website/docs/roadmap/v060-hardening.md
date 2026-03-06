---
sidebar_position: 4
title: "v0.6.0: Production Hardening"
---

# v0.6.0 — Production Hardening

Make it reliable enough for real workloads.

## Local (Ollama)

| ID | Feature | Files | Detail |
|----|---------|-------|--------|
| PROD-01 | Local model registry | `core/model_registry.py` (new) | Track pulled models, sizes, last used date, performance stats |
| PROD-02 | Ollama auto-pull | `providers/local.py` | If a required model isn't available, pull it automatically before execution |
| PROD-03 | GPU memory management | `providers/local.py`, `core/health.py` | Monitor VRAM usage, prevent OOM by queuing requests when memory is low |
| PROD-04 | Local inference metrics | `core/metrics.py` (new) | Prometheus-compatible metrics: tok/s, queue depth, model load times |

## Cloud (OpenRouter)

| ID | Feature | Files | Detail |
|----|---------|-------|--------|
| PROD-05 | API key rotation | `providers/openrouter.py` | Support multiple API keys with round-robin to spread rate limits |
| PROD-06 | Rate limiting | `core/rate_limiter.py` (new) | Per-provider token rate limits to avoid API throttling |
| PROD-07 | Retry with backoff | `core/provider.py`, `providers/*.py` | Exponential backoff on provider errors (429, 500, timeout) — replace current simple retry |
| PROD-08 | Spend alerts | `core/alerts.py` (new) | Email/webhook notification when daily/weekly spend exceeds threshold |

## Both (Core)

| ID | Feature | Files | Detail |
|----|---------|-------|--------|
| PROD-09 | Persistent task queue | `core/task_queue.py` (new) | Tasks survive server restarts, backed by Postgres |
| PROD-10 | Authentication | `dashboard/auth.py` (new) | API key or OAuth for dashboard and API access |
| PROD-11 | Audit log | `core/audit.py` (new) | Full trace of every agent action, tool call, decision, provider used |
| PROD-12 | Health checks | `dashboard/app.py` | `/health` endpoint with per-provider status (Ollama up? OpenRouter reachable?) |
| PROD-13 | Metrics export | `core/metrics.py` | Prometheus endpoint `/metrics` — tokens, latency, cost, errors by provider |

## Implementation Notes

**PROD-09 (Persistent task queue)** is critical for production:

```python
# core/task_queue.py
class TaskQueue:
    async def enqueue(self, task: Task, priority: int = 0) -> str  # returns task_id
    async def dequeue(self) -> Task | None
    async def complete(self, task_id: str, result: TaskResult)
    async def get_status(self, task_id: str) -> TaskStatus
    async def list_pending(self) -> list[Task]
    # Backed by Postgres — tasks persist across restarts
```

**PROD-10 (Authentication)** — minimal viable auth:

```python
# dashboard/auth.py
class AuthMiddleware:
    # Check Authorization: Bearer <key> header
    # Resolve key to user (or reject)
    # Skip auth for static files and /health
```

**PROD-13 (Metrics)** — expose via `/metrics` for Grafana/Prometheus scraping:
- `orchestrator_tokens_total{provider, model, direction}` — counter
- `orchestrator_request_duration_seconds{provider, model}` — histogram
- `orchestrator_cost_usd_total{provider, model}` — counter
- `orchestrator_errors_total{provider, model, error_type}` — counter
