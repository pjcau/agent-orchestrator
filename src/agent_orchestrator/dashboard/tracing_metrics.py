"""Tracing metrics collector — lightweight counters for Prometheus export.

Collects LLM call durations, graph node durations, and agent stalls
from the instrumented code. Thread-safe via simple dict operations.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()

_llm_durations: dict[str, dict[str, float]] = {}  # provider -> {count, sum}
_node_durations: dict[str, dict[str, float]] = {}  # node_name -> {count, sum}
_stalls_by_category: dict[str, int] = {}


def record_llm_duration(provider: str, duration_s: float) -> None:
    with _lock:
        if provider not in _llm_durations:
            _llm_durations[provider] = {"count": 0, "sum": 0.0}
        _llm_durations[provider]["count"] += 1
        _llm_durations[provider]["sum"] += duration_s


def record_node_duration(node_name: str, duration_s: float) -> None:
    with _lock:
        if node_name not in _node_durations:
            _node_durations[node_name] = {"count": 0, "sum": 0.0}
        _node_durations[node_name]["count"] += 1
        _node_durations[node_name]["sum"] += duration_s


def record_stall(category: str) -> None:
    with _lock:
        _stalls_by_category[category] = _stalls_by_category.get(category, 0) + 1


def get_tracing_metrics() -> dict:
    with _lock:
        return {
            "llm_durations": dict(_llm_durations),
            "node_durations": dict(_node_durations),
            "stalls_by_category": dict(_stalls_by_category),
        }
