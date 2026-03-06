---
sidebar_position: 3
title: "v0.5.0: Smart Routing"
---

# v0.5.0 — Smart Routing & Cost Optimization ✅

Intelligent model selection and cost control across local and cloud.

**Status:** Completed — all 12 features implemented and tested (201 tests passing).

## New Modules

| Module | Description |
|--------|-------------|
| `core/router.py` | TaskRouter with 6 routing strategies + TaskComplexityClassifier |
| `core/usage.py` | UsageTracker with budget enforcement (task/session/day) |
| `core/health.py` | HealthMonitor with sliding-window latency + error rate tracking |
| `core/benchmark.py` | BenchmarkSuite for comparing models on latency/throughput/cost |

## Features

### Local (Ollama)

| ID | Feature | Status | Detail |
|----|---------|--------|--------|
| ROUTE-01 | Local-first routing | ✅ | `TaskRouter` with `LOCAL_FIRST` strategy — always tries Ollama first |
| ROUTE-02 | Model benchmarking | ✅ | `BenchmarkSuite.compare_models()` runs same task across providers |
| ROUTE-03 | Ollama health monitoring | ✅ | `HealthMonitor` tracks latency, error rate, availability per provider |
| ROUTE-04 | Auto-model selection | ✅ | `TaskComplexityClassifier` + `CAPABILITY_BASED` routing matches task to model |

### Cloud (OpenRouter)

| ID | Feature | Status | Detail |
|----|---------|--------|--------|
| ROUTE-05 | Cost budgets | ✅ | `UsageTracker.check_budget()` with `BudgetConfig` (task/session/day limits) |
| ROUTE-06 | Fallback chains | ✅ | `FALLBACK_CHAIN` strategy skips unhealthy providers in configured order |
| ROUTE-07 | Provider health monitoring | ✅ | `HealthMonitor` with sliding window, consecutive error tracking |
| ROUTE-08 | Cost dashboard | ✅ | `UsageTracker.get_cost_breakdown()` — local vs cloud split, by-provider/agent |
| ROUTE-09 | Model price comparison | ✅ | `BenchmarkSuite` results include cost_usd per benchmark run |

### Hybrid

| ID | Feature | Status | Detail |
|----|---------|--------|--------|
| ROUTE-10 | Complexity-based routing | ✅ | `COMPLEXITY_BASED` strategy: low→local, medium→mid-tier, high→top-tier |
| ROUTE-11 | Automatic failover | ✅ | Router checks `HealthMonitor.is_available()` before selecting provider |
| ROUTE-12 | Split execution | ✅ | Interface stub — delegates to complexity_based routing for now |

## Key APIs

### TaskRouter

```python
from agent_orchestrator.core.router import TaskRouter, RouterConfig, RoutingStrategy

router = TaskRouter(
    providers={"local-ollama": ollama, "openrouter": cloud, "anthropic": claude},
    health_monitor=health,
    config=RouterConfig(strategy=RoutingStrategy.COMPLEXITY_BASED),
)
provider = router.route("build a REST API with auth")
```

### UsageTracker

```python
from agent_orchestrator.core.usage import UsageTracker, UsageRecord, BudgetConfig

tracker = UsageTracker()
tracker.record(UsageRecord(provider="openrouter", model="qwen", input_tokens=1000, output_tokens=500, cost_usd=0.01))
status = tracker.check_budget(BudgetConfig(max_per_session=1.0))
breakdown = tracker.get_cost_breakdown()  # local vs cloud split
```

### HealthMonitor

```python
from agent_orchestrator.core.health import HealthMonitor

monitor = HealthMonitor(max_consecutive_errors=5, error_rate_threshold=0.5)
monitor.record_success("openrouter", latency_ms=250.0)
monitor.record_error("local-ollama", "connection refused")
best = monitor.get_best_provider(["local-ollama", "openrouter"])
```
