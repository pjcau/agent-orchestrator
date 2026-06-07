"""Prometheus metrics for the agent-host channel.

Metrics are emitted on the injected :class:`core.metrics.MetricsRegistry`
to keep the import boundary clean — ``agent_host`` does not depend on
``dashboard``; the dashboard supplies the shared registry when wiring
the WS route.

Catalogue (label set kept tiny on purpose — labels are a cardinality
multiplier and Prometheus penalises high-cardinality series):

* ``agent_host_tool_call_latency_seconds`` (histogram, labels
  ``tool, status``) — wall time between ``issue`` and ``resolve`` for
  each delegated call.
* ``agent_host_active_streams`` (gauge, labels ``run_id_hash``) —
  in-flight streaming tool_calls per run. ``run_id`` is hashed
  (16-hex SHA-256) so the metric label cannot be correlated to a
  specific user session at a glance.
* ``agent_host_disconnect_total`` (counter, labels ``reason``) — how
  agent-host sessions ended. ``reason`` is the same stable string
  returned by :func:`server.serve_agent_host`.
* ``agent_host_chunk_rejected_total`` (counter, labels ``reason``) —
  TOOL_CHUNK frames rejected at the registry (signature, out-of-order,
  too-large, …). Spikes here indicate a misbehaving or malicious
  client.

PII / privacy: **never** label by `cwd`, file path, agent name, model
ID, or anything user-controlled. Only enumerable strings + hashed ids.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol


class _MetricsLike(Protocol):
    """Structural type matching :class:`core.metrics.MetricsRegistry`.

    Avoids a hard import — wiring is dependency-injected from the
    dashboard so :mod:`agent_host` stays a pure-library package.
    """

    def counter(self, name: str, description: str = "", labels: dict | None = None): ...
    def gauge(self, name: str, description: str = "", labels: dict | None = None): ...
    def histogram(self, name: str, description: str = "", labels: dict | None = None): ...


def hash_run_id(run_id: str) -> str:
    """Stable 16-hex digest of ``run_id`` for metric labels.

    Even though ``run_id`` is server-minted (not user input), label
    cardinality must stay bounded — and an opaque hash makes ad-hoc
    cross-referencing with audit logs harder by default.
    """
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class AgentHostMetrics:
    """Bundle of cached metric handles.

    Acquire once per process via :func:`bind`; reusing the same labels
    keeps the hot path allocation-free.
    """

    tool_call_latency: object
    active_streams: object
    disconnect_total: object
    chunk_rejected_total: object


def bind(registry: _MetricsLike | None) -> AgentHostMetrics | None:
    """Resolve metric handles against ``registry``.

    Returns ``None`` when ``registry`` is ``None`` so the rest of the
    code can write ``metrics and metrics.tool_call_latency.observe(...)``
    without branching on the wiring scenario.
    """
    if registry is None:
        return None
    return AgentHostMetrics(
        tool_call_latency=registry.histogram(
            "agent_host_tool_call_latency_seconds",
            description="Wall time between issue and resolve of a delegated tool call",
            labels={"tool": "", "status": ""},
        ),
        active_streams=registry.gauge(
            "agent_host_active_streams",
            description="In-flight streaming tool_calls per run (run_id hashed)",
            labels={"run_id_hash": ""},
        ),
        disconnect_total=registry.counter(
            "agent_host_disconnect_total",
            description="Agent-host sessions ended, by stable reason string",
            labels={"reason": ""},
        ),
        chunk_rejected_total=registry.counter(
            "agent_host_chunk_rejected_total",
            description="TOOL_CHUNK frames rejected by the registry, by reason",
            labels={"reason": ""},
        ),
    )
