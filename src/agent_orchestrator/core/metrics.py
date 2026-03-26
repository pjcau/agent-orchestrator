"""In-memory metrics collection — Prometheus-compatible naming, no external deps.

Optionally accelerated by Rust via PyO3 when _agent_orchestrator_rust is installed.
"""

from __future__ import annotations

import math
import time
from typing import Any

# Rust acceleration (optional — falls back to pure Python)
try:
    from _agent_orchestrator_rust import RustMetricsRegistry as _RustMetricsRegistry  # noqa: F401

    _HAS_RUST_METRICS = True
except ImportError:
    _HAS_RUST_METRICS = False


# ---------------------------------------------------------------------------
# Metric primitives
# ---------------------------------------------------------------------------


class Counter:
    """Monotonically increasing counter."""

    def __init__(self, name: str, description: str = "", labels: dict | None = None) -> None:
        self.name = name
        self.description = description
        self.labels = labels or {}
        self._value: float = 0.0

    def inc(self, value: float = 1.0) -> None:
        if value < 0:
            raise ValueError("Counter can only be incremented by a non-negative value")
        self._value += value

    def get(self) -> float:
        return self._value

    def reset(self) -> None:
        self._value = 0.0


class Gauge:
    """Gauge that can go up and down."""

    def __init__(self, name: str, description: str = "", labels: dict | None = None) -> None:
        self.name = name
        self.description = description
        self.labels = labels or {}
        self._value: float = 0.0

    def set(self, value: float) -> None:
        self._value = value

    def inc(self, value: float = 1.0) -> None:
        self._value += value

    def dec(self, value: float = 1.0) -> None:
        self._value -= value

    def get(self) -> float:
        return self._value


class Histogram:
    """Tracks distribution of observed values with a bounded rolling window.

    Keeps at most ``max_observations`` recent values to prevent unbounded
    memory growth.  The ``_sum`` and ``_count`` accumulators are always
    accurate (monotonically increasing) regardless of the window size.
    """

    def __init__(
        self,
        name: str,
        description: str = "",
        labels: dict | None = None,
        max_observations: int = 10_000,
    ) -> None:
        self.name = name
        self.description = description
        self.labels = labels or {}
        self._max_observations = max_observations
        self._observations: list[float] = []
        self._sum: float = 0.0
        self._count: int = 0

    def observe(self, value: float) -> None:
        if len(self._observations) >= self._max_observations:
            self._observations.pop(0)
        self._observations.append(value)
        self._sum += value
        self._count += 1

    def get_count(self) -> int:
        return self._count

    def get_sum(self) -> float:
        return self._sum

    def get_avg(self) -> float:
        count = len(self._observations)
        return self._sum / count if count > 0 else 0.0

    def get_percentile(self, p: float) -> float:
        """Return the p-th percentile (0–100).  Returns 0 if no observations."""
        if not self._observations:
            return 0.0
        sorted_obs = sorted(self._observations)
        if p <= 0:
            return sorted_obs[0]
        if p >= 100:
            return sorted_obs[-1]
        # Linear interpolation
        index = (p / 100) * (len(sorted_obs) - 1)
        lower = math.floor(index)
        upper = math.ceil(index)
        if lower == upper:
            return sorted_obs[lower]
        fraction = index - lower
        return sorted_obs[lower] * (1 - fraction) + sorted_obs[upper] * fraction


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class MetricsRegistry:
    """Central registry for all metrics."""

    def __init__(self) -> None:
        self._metrics: dict[str, Counter | Gauge | Histogram] = {}

    def counter(
        self,
        name: str,
        description: str = "",
        labels: dict | None = None,
    ) -> Counter:
        key = _metric_key(name, labels)
        if key not in self._metrics:
            self._metrics[key] = Counter(name, description, labels)
        metric = self._metrics[key]
        if not isinstance(metric, Counter):
            raise TypeError(f"Metric '{name}' already registered as {type(metric).__name__}")
        return metric

    def gauge(
        self,
        name: str,
        description: str = "",
        labels: dict | None = None,
    ) -> Gauge:
        key = _metric_key(name, labels)
        if key not in self._metrics:
            self._metrics[key] = Gauge(name, description, labels)
        metric = self._metrics[key]
        if not isinstance(metric, Gauge):
            raise TypeError(f"Metric '{name}' already registered as {type(metric).__name__}")
        return metric

    def histogram(
        self,
        name: str,
        description: str = "",
        labels: dict | None = None,
    ) -> Histogram:
        key = _metric_key(name, labels)
        if key not in self._metrics:
            self._metrics[key] = Histogram(name, description, labels)
        metric = self._metrics[key]
        if not isinstance(metric, Histogram):
            raise TypeError(f"Metric '{name}' already registered as {type(metric).__name__}")
        return metric

    def get_all(self) -> dict[str, Any]:
        """Return all metrics as a plain dict."""
        result: dict[str, Any] = {}
        for key, metric in self._metrics.items():
            if isinstance(metric, Counter):
                result[key] = {"type": "counter", "value": metric.get(), "labels": metric.labels}
            elif isinstance(metric, Gauge):
                result[key] = {"type": "gauge", "value": metric.get(), "labels": metric.labels}
            elif isinstance(metric, Histogram):
                result[key] = {
                    "type": "histogram",
                    "count": metric.get_count(),
                    "sum": metric.get_sum(),
                    "avg": metric.get_avg(),
                    "p50": metric.get_percentile(50),
                    "p95": metric.get_percentile(95),
                    "p99": metric.get_percentile(99),
                    "labels": metric.labels,
                }
        return result

    def export_prometheus(self) -> str:
        """Export all metrics in Prometheus text exposition format."""
        lines: list[str] = []
        # Group by metric name for HELP/TYPE blocks
        by_name: dict[str, list[Counter | Gauge | Histogram]] = {}
        for metric in self._metrics.values():
            by_name.setdefault(metric.name, []).append(metric)

        timestamp_ms = int(time.time() * 1000)

        for name, metrics in sorted(by_name.items()):
            first = metrics[0]
            if first.description:
                lines.append(f"# HELP {name} {first.description}")

            if isinstance(first, Counter):
                lines.append(f"# TYPE {name} counter")
                for m in metrics:
                    label_str = _format_labels(m.labels)
                    lines.append(f"{name}{label_str} {m.get()} {timestamp_ms}")
            elif isinstance(first, Gauge):
                lines.append(f"# TYPE {name} gauge")
                for m in metrics:
                    label_str = _format_labels(m.labels)
                    lines.append(f"{name}{label_str} {m.get()} {timestamp_ms}")
            elif isinstance(first, Histogram):
                lines.append(f"# TYPE {name} histogram")
                for m in metrics:
                    label_str = _format_labels(m.labels)
                    lines.append(f"{name}_count{label_str} {m.get_count()} {timestamp_ms}")
                    lines.append(f"{name}_sum{label_str} {m.get_sum()} {timestamp_ms}")

        return "\n".join(lines) + "\n" if lines else ""


# ---------------------------------------------------------------------------
# Default metrics factory
# ---------------------------------------------------------------------------


def default_metrics(registry: MetricsRegistry | None = None) -> MetricsRegistry:
    """Create and register the standard set of agent/provider metrics."""
    reg = registry or MetricsRegistry()

    for agent in ["backend", "frontend", "devops", "platform-engineer", "ai-engineer", "scout"]:
        for status in ["completed", "failed", "stalled"]:
            reg.counter(
                "agent_tasks_total",
                "Total number of tasks processed by agents",
                labels={"agent": agent, "status": status},
            )
        for provider in ["anthropic", "openai", "google", "local", "openrouter"]:
            reg.counter(
                "agent_tokens_total",
                "Total tokens consumed by agents",
                labels={"agent": agent, "provider": provider},
            )
            reg.counter(
                "agent_cost_usd_total",
                "Total cost in USD consumed by agents",
                labels={"agent": agent, "provider": provider},
            )
        reg.histogram(
            "agent_latency_seconds",
            "Agent task execution latency in seconds",
            labels={"agent": agent},
        )

    for provider in ["anthropic", "openai", "google", "local", "openrouter"]:
        for status in ["success", "error"]:
            reg.counter(
                "provider_requests_total",
                "Total requests made to LLM providers",
                labels={"provider": provider, "status": status},
            )
        reg.histogram(
            "provider_latency_seconds",
            "LLM provider request latency in seconds",
            labels={"provider": provider},
        )
        for error_type in ["rate_limit", "timeout", "auth", "server_error", "unknown"]:
            reg.counter(
                "provider_errors_total",
                "Total errors from LLM providers",
                labels={"provider": provider, "error_type": error_type},
            )

    # Progressive skill loading metrics
    reg.counter(
        "skill_loads_total",
        "Total number of on-demand skill instruction loads",
    )

    # Conversation summarization metrics
    reg.counter(
        "conversation_summarization_total",
        "Total number of context summarizations performed",
    )
    reg.gauge(
        "conversation_tokens_saved",
        "Estimated tokens saved by context summarization",
    )

    # Loop detection metrics
    reg.counter(
        "loop_warnings_total",
        "Total number of loop warning events",
    )
    reg.counter(
        "loop_hard_stops_total",
        "Total number of loop hard stop events",
    )

    return reg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _metric_key(name: str, labels: dict | None) -> str:
    if not labels:
        return name
    label_part = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
    return f"{name}{{{label_part}}}"


def _format_labels(labels: dict) -> str:
    if not labels:
        return ""
    parts = ", ".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return "{" + parts + "}"
