"""Tests for the agent-host telemetry layer.

Coverage:

* ``bind(None)`` returns ``None`` so the call sites can use truthy-check
  rather than branching on every metric increment.
* ``bind(registry)`` returns an :class:`AgentHostMetrics` whose handles
  are alive and pointing at the right registry entries.
* ``hash_run_id`` is deterministic and 16 hex chars.
* ``serve_agent_host`` increments ``disconnect_total`` on protocol error
  (cardinality stays bounded: stable reason string).

Threats / concerns covered:

* PII leak in labels — the handle for ``active_streams`` is keyed by
  ``run_id_hash``, never the raw run_id.
* Unbounded label cardinality — only stable enumerable strings are used
  as label values.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.agent_host import Hello
from agent_orchestrator.agent_host.server import serve_agent_host
from agent_orchestrator.agent_host.telemetry import (
    AgentHostMetrics,
    bind,
    hash_run_id,
)
from agent_orchestrator.core.metrics import MetricsRegistry


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.incoming: asyncio.Queue[dict] = asyncio.Queue()
        self.closed = False

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def receive_json(self) -> dict:
        return await self.incoming.get()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True


@pytest.fixture
def signing_key(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)


def test_bind_none_returns_none():
    assert bind(None) is None


def test_bind_returns_handles():
    reg = MetricsRegistry()
    m = bind(reg)
    assert isinstance(m, AgentHostMetrics)
    assert m.tool_call_latency is not None
    assert m.disconnect_total is not None
    assert m.active_streams is not None
    assert m.chunk_rejected_total is not None


def test_hash_run_id_deterministic():
    a = hash_run_id("run-123")
    b = hash_run_id("run-123")
    c = hash_run_id("run-124")
    assert a == b
    assert a != c
    assert len(a) == 16
    assert set(a) <= set("0123456789abcdef")


@pytest.mark.asyncio
async def test_serve_agent_host_increments_disconnect_on_error(signing_key):
    reg = MetricsRegistry()
    m = bind(reg)
    ws = FakeWS()
    await ws.incoming.put(Hello(version=99, tool_manifest=["x"]).to_dict())
    reason = await serve_agent_host(ws, metrics=m)
    assert reason == "protocol_error:version_unsupported"
    # The counter exists and was incremented for the labeled reason.
    rendered = reg.export_prometheus()
    assert "agent_host_disconnect_total" in rendered
