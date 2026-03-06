---
sidebar_position: 3
title: "v0.5.0: Smart Routing"
---

# v0.5.0 — Smart Routing & Cost Optimization

Intelligent model selection and cost control across local and cloud.

## Local (Ollama)

| ID | Feature | Files | Detail |
|----|---------|-------|--------|
| ROUTE-01 | Local-first routing | `core/orchestrator.py`, `core/router.py` (new) | Always try Ollama first, only go to cloud when local fails or task requires it |
| ROUTE-02 | Model benchmarking | `core/benchmark.py` (new), `dashboard/app.py` | Run same task on multiple Ollama models, compare quality/speed in dashboard |
| ROUTE-03 | Ollama health monitoring | `providers/local.py`, `dashboard/app.py` | Track inference speed (tok/s), memory usage, model load status |
| ROUTE-04 | Auto-model selection | `core/router.py` | Match task type to best local model (coding→coder, reasoning→deepseek, general→llama) |

## Cloud (OpenRouter)

| ID | Feature | Files | Detail |
|----|---------|-------|--------|
| ROUTE-05 | Cost budgets | `core/usage.py` (new), `core/provider.py` | Max spend per task/session/day; auto-switch to cheaper model or local on limit |
| ROUTE-06 | Fallback chains | `core/router.py`, `core/orchestrator.py` | Configurable chain: Ollama → OpenRouter → direct API per agent |
| ROUTE-07 | Provider health monitoring | `core/health.py` (new), `dashboard/app.py` | Track latency, error rates, availability per OpenRouter model |
| ROUTE-08 | Cost dashboard | `dashboard/static/`, `dashboard/app.py` | Real-time cost tracking with projections, alerts, local-vs-cloud breakdown |
| ROUTE-09 | Model price comparison | `dashboard/static/` | Cost/quality matrix across local and cloud models |

## Hybrid

| ID | Feature | Files | Detail |
|----|---------|-------|--------|
| ROUTE-10 | Complexity-based routing | `core/router.py` | Classify task difficulty → simple=local, medium=Qwen3.5, hard=DeepSeek R1 |
| ROUTE-11 | Automatic failover | `core/router.py`, `providers/*.py` | If Ollama is down or too slow, transparently route to OpenRouter |
| ROUTE-12 | Split execution | `core/orchestrator.py`, `core/router.py` | Decompose task → run cheap sub-tasks locally, expensive ones on cloud |

## Implementation Notes

**ROUTE-01 + ROUTE-10 + ROUTE-11** form the core routing engine:

```python
# core/router.py
class TaskRouter:
    async def route(self, task: Task) -> Provider:
        complexity = self.classify(task)
        if complexity == "low" and self.ollama_healthy():
            return self.local_provider
        elif complexity == "high":
            return self.cloud_provider
        else:
            return self.try_local_then_cloud()
```

**ROUTE-05 (Cost budgets)** requires a persistent usage tracker:

```python
# core/usage.py
class UsageTracker:
    async def record(self, model, input_tokens, output_tokens, cost_usd)
    async def get_spend(self, period="day") -> float
    async def check_budget(self, budget_usd) -> bool
    async def get_history(self, days=30) -> list[UsageRecord]
```
