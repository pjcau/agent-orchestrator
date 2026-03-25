"""Tests for SSE streaming and HITL resume endpoints.

Covers:
- RunManager lifecycle (create, get, subscribe, cleanup)
- SSE event formatting helpers
- HITL resume flow (auto_approve, manual, timeout, disabled)
- TTL eviction and run limit
- stream_mode parameter (events vs values)
- Reconnection with Last-Event-ID header
- Integration tests via httpx.AsyncClient against the FastAPI app
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest
import pytest_asyncio

from agent_orchestrator.core.graph import (
    CompiledGraph,
    GraphInterrupt,
    Interrupt,
    InterruptType,
    START,
    END,
    StateGraph,
    StreamEvent,
    StreamEventType,
)
from agent_orchestrator.dashboard.events import EventBus
from agent_orchestrator.dashboard.sse import (
    HITLConfig,
    RUN_LIMIT,
    RUN_TTL_SECONDS,
    RunInfo,
    RunManager,
    _sse_data,
    _sse_error,
    _stream_event_to_events_payload,
    _stream_event_to_values_payload,
)


# ------------------------------------------------------------------ helpers


def _make_graph(nodes: dict[str, Any] | None = None) -> CompiledGraph:
    """Build and compile a simple graph for testing.

    Default: START -> echo -> END, where echo returns state unchanged.
    Custom node functions can be passed in the *nodes* dict.
    """
    sg = StateGraph()
    if nodes is None:

        async def echo(state):
            return state

        nodes = {"echo": echo}

    for name, fn in nodes.items():
        sg.add_node(name, fn)

    # Wire START → first node → END
    first = next(iter(nodes))
    sg.add_edge(START, first)
    # If there are more nodes, wire them sequentially
    names = list(nodes)
    for i in range(len(names) - 1):
        sg.add_edge(names[i], names[i + 1])
    sg.add_edge(names[-1], END)
    return sg.compile()


def _make_interrupt_graph() -> CompiledGraph:
    """Build a graph whose first node raises GraphInterrupt."""
    sg = StateGraph()

    async def needs_approval(state):
        raise GraphInterrupt(
            Interrupt(
                interrupt_type=InterruptType.APPROVAL,
                message="Approve this action?",
                node="needs_approval",
                options=["approve", "reject"],
            )
        )

    async def finalize(state):
        return {"finalized": True}

    sg.add_node("needs_approval", needs_approval)
    sg.add_node("finalize", finalize)
    sg.add_edge(START, "needs_approval")
    sg.add_edge("needs_approval", "finalize")
    sg.add_edge("finalize", END)
    return sg.compile()


async def _collect_sse(manager: RunManager, run_id: str) -> list[dict]:
    """Subscribe to a run and return all parsed event payloads."""
    payloads: list[dict] = []
    async for chunk in manager.subscribe(run_id):
        # Filter out SSE comment lines and empty lines
        for line in chunk.split("\n"):
            if line.startswith("data: "):
                payloads.append(json.loads(line[len("data: ") :]))
    return payloads


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def isolated_bus() -> EventBus:
    """Return a fresh EventBus not shared with the global singleton."""
    return EventBus()


@pytest.fixture
def manager(isolated_bus) -> RunManager:
    return RunManager(event_bus=isolated_bus)


# ================================================================== unit tests


class TestSSEHelpers:
    """Unit tests for the SSE formatting utilities."""

    def test_sse_data_format(self):
        payload = {"event": "node_start", "node": "echo"}
        result = _sse_data(payload)
        # Must end with double newline
        assert result.endswith("\n\n")
        # Must contain data: line
        assert "data: " in result
        parsed = json.loads(result.split("data: ")[1].strip())
        assert parsed["event"] == "node_start"

    def test_sse_data_with_event_id(self):
        result = _sse_data({"x": 1}, event_id="42")
        assert "id: 42\n" in result

    def test_sse_data_without_event_id(self):
        result = _sse_data({"x": 1})
        assert "id:" not in result

    def test_sse_error_format(self):
        result = _sse_error("run_not_found", "Run 'abc' does not exist")
        assert "data: " in result
        parsed = json.loads(result.split("data: ")[1].strip())
        assert parsed["event"] == "error"
        assert parsed["error_type"] == "run_not_found"
        assert "Run 'abc'" in parsed["message"]

    def test_stream_event_to_events_payload_node_start(self):
        event = StreamEvent(
            event_type=StreamEventType.NODE_START,
            node="analyze",
            step_index=1,
            state={"x": 1},
        )
        payload = _stream_event_to_events_payload(event)
        assert payload["event"] == "node_start"
        assert payload["node"] == "analyze"
        assert payload["state"] == {"x": 1}
        assert payload["step"] == 1
        # Optional fields absent when None
        assert "delta" not in payload
        assert "error" not in payload

    def test_stream_event_to_events_payload_with_interrupt(self):
        interrupt = Interrupt(
            interrupt_type=InterruptType.HUMAN_INPUT,
            message="Provide input",
            node="review",
        )
        event = StreamEvent(
            event_type=StreamEventType.NODE_ERROR,
            node="review",
            step_index=2,
            state={},
            interrupted=interrupt,
        )
        payload = _stream_event_to_events_payload(event)
        assert "interrupt" in payload
        assert payload["interrupt"]["type"] == "human_input"
        assert payload["interrupt"]["message"] == "Provide input"

    def test_stream_event_to_values_payload(self):
        event = StreamEvent(
            event_type=StreamEventType.NODE_END,
            node="echo",
            step_index=0,
            state={"result": "done"},
        )
        payload = _stream_event_to_values_payload(event)
        assert payload["event"] == "node_end"
        assert "values" in payload
        assert payload["values"] == {"result": "done"}
        # values mode does not include delta
        assert "delta" not in payload


class TestHITLConfig:
    """Unit tests for the HITLConfig dataclass."""

    def test_defaults(self):
        cfg = HITLConfig()
        assert cfg.enabled is True
        assert cfg.timeout_seconds == 300
        assert cfg.auto_approve is False

    def test_custom_values(self):
        cfg = HITLConfig(enabled=False, timeout_seconds=60, auto_approve=True)
        assert cfg.enabled is False
        assert cfg.timeout_seconds == 60
        assert cfg.auto_approve is True


class TestRunInfo:
    """Unit tests for the RunInfo dataclass."""

    def test_defaults(self):
        info = RunInfo(run_id="abc")
        assert info.status == "pending"
        assert info.result is None
        assert info.error is None
        assert info.interrupt is None
        assert isinstance(info.created_at, float)


# ================================================================== RunManager


class TestRunManagerLifecycle:
    """Tests for RunManager.create_run / get_run."""

    @pytest.mark.asyncio
    async def test_create_run_returns_run_id(self, manager):
        graph = _make_graph()
        run_id = manager.create_run(graph=graph, input_data={"msg": "hi"})
        assert isinstance(run_id, str)
        assert len(run_id) > 0

    @pytest.mark.asyncio
    async def test_get_run_returns_info(self, manager):
        graph = _make_graph()
        run_id = manager.create_run(graph=graph)
        info = manager.get_run(run_id)
        assert info is not None
        assert info.run_id == run_id

    @pytest.mark.asyncio
    async def test_get_run_unknown_returns_none(self, manager):
        assert manager.get_run("nonexistent-id") is None

    @pytest.mark.asyncio
    async def test_run_completes(self, manager):
        graph = _make_graph()
        run_id = manager.create_run(graph=graph, input_data={"val": 42})

        # Give the background task time to complete
        await asyncio.sleep(0.2)

        info = manager.get_run(run_id)
        assert info.status == "completed"
        assert info.result is not None

    @pytest.mark.asyncio
    async def test_run_status_transitions(self, manager):
        """Status must move from pending -> running -> completed."""
        graph = _make_graph()
        run_id = manager.create_run(graph=graph)

        # Immediately after creation it is pending or already running
        info = manager.get_run(run_id)
        assert info.status in ("pending", "running", "completed")

        await asyncio.sleep(0.2)
        assert manager.get_run(run_id).status == "completed"

    @pytest.mark.asyncio
    async def test_failed_graph_marks_run_failed(self, manager):
        async def broken(state):
            raise RuntimeError("Node exploded")

        graph = _make_graph({"broken": broken})
        run_id = manager.create_run(graph=graph)
        await asyncio.sleep(0.2)

        info = manager.get_run(run_id)
        assert info.status == "failed"
        assert info.error is not None


class TestRunManagerSubscribe:
    """Tests for RunManager.subscribe() SSE iteration."""

    @pytest.mark.asyncio
    async def test_subscribe_unknown_run_yields_error(self, manager):
        chunks = []
        async for chunk in manager.subscribe("bad-id"):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert "run_not_found" in chunks[0]

    @pytest.mark.asyncio
    async def test_subscribe_receives_events(self, manager):
        graph = _make_graph()
        run_id = manager.create_run(graph=graph, input_data={"x": 1})

        payloads = await _collect_sse(manager, run_id)
        event_types = [p["event"] for p in payloads]

        assert "graph_start" in event_types
        assert "graph_end" in event_types

    @pytest.mark.asyncio
    async def test_subscribe_events_have_id_field(self, manager):
        graph = _make_graph()
        run_id = manager.create_run(graph=graph)

        raw_chunks = []
        async for chunk in manager.subscribe(run_id):
            raw_chunks.append(chunk)

        # At least one chunk should carry an id: field
        id_lines = [c for c in raw_chunks if "id: " in c]
        assert len(id_lines) > 0

    @pytest.mark.asyncio
    async def test_reconnect_comment_sent_with_last_event_id(self, manager):
        graph = _make_graph()
        run_id = manager.create_run(graph=graph)
        # Let the run finish so subscribe terminates quickly
        await asyncio.sleep(0.2)

        raw_chunks = []
        async for chunk in manager.subscribe(run_id, last_event_id="5"):
            raw_chunks.append(chunk)

        # First chunk must be the reconnect comment
        reconnect_chunks = [c for c in raw_chunks if c.startswith(": reconnected")]
        assert len(reconnect_chunks) >= 1
        assert "5" in reconnect_chunks[0]

    @pytest.mark.asyncio
    async def test_multiple_subscribers_receive_same_events(self, manager):
        graph = _make_graph()
        run_id = manager.create_run(graph=graph)

        results = await asyncio.gather(
            _collect_sse(manager, run_id),
            _collect_sse(manager, run_id),
        )
        assert len(results[0]) > 0
        assert len(results[1]) > 0


class TestStreamMode:
    """Tests for stream_mode parameter (events vs values)."""

    @pytest.mark.asyncio
    async def test_stream_mode_events_has_event_field(self, manager):
        graph = _make_graph()
        run_id = manager.create_run(graph=graph, stream_mode="events")
        payloads = await _collect_sse(manager, run_id)

        for p in payloads:
            assert "event" in p, f"Missing 'event' key in: {p}"

    @pytest.mark.asyncio
    async def test_stream_mode_values_has_values_field(self, manager):
        graph = _make_graph()
        run_id = manager.create_run(graph=graph, stream_mode="values")
        payloads = await _collect_sse(manager, run_id)

        for p in payloads:
            assert "values" in p, f"Missing 'values' key in: {p}"

    @pytest.mark.asyncio
    async def test_stream_mode_events_does_not_have_values_field(self, manager):
        graph = _make_graph()
        run_id = manager.create_run(graph=graph, stream_mode="events")
        payloads = await _collect_sse(manager, run_id)

        non_error_payloads = [p for p in payloads if p.get("event") != "error"]
        for p in non_error_payloads:
            assert "values" not in p, f"Unexpected 'values' key in events mode: {p}"


class TestHITLFlow:
    """Tests for HITL (Human-in-the-Loop) interrupt and resume."""

    @pytest.mark.asyncio
    async def test_auto_approve_completes_run(self, manager):
        """With auto_approve=True the graph resumes immediately."""
        graph = _interrupt_graph_with_finalize()
        hitl = HITLConfig(auto_approve=True)
        run_id = manager.create_run(graph=graph, input_data={"step": 0}, hitl_config=hitl)
        await asyncio.sleep(0.3)

        info = manager.get_run(run_id)
        assert info.status == "completed"

    @pytest.mark.asyncio
    async def test_hitl_disabled_marks_run_failed(self, manager):
        """When HITL is disabled, an interrupt immediately fails the run."""
        graph = _make_interrupt_graph()
        hitl = HITLConfig(enabled=False)
        run_id = manager.create_run(graph=graph, hitl_config=hitl)
        await asyncio.sleep(0.3)

        info = manager.get_run(run_id)
        assert info.status == "failed"
        assert info.error is not None

    @pytest.mark.asyncio
    async def test_hitl_interrupt_status(self, manager):
        """An interrupted run should reach status='interrupted'."""
        graph = _make_interrupt_graph()
        # Use a short timeout so the background task cleans itself up quickly
        hitl = HITLConfig(enabled=True, timeout_seconds=2)
        run_id = manager.create_run(graph=graph, hitl_config=hitl)

        # Poll until status leaves running/pending (give generous time)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            info = manager.get_run(run_id)
            if info.status not in ("pending", "running"):
                break

        info = manager.get_run(run_id)
        # Status should be 'interrupted' (before timeout) or 'failed' (after)
        assert info.status in ("interrupted", "failed")
        # Either the interrupt object is set or there's a timeout error
        assert info.interrupt is not None or info.error is not None

    @pytest.mark.asyncio
    async def test_resume_invalid_run_raises(self, manager):
        with pytest.raises(ValueError, match="not found"):
            await manager.resume_run("nonexistent", {})

    @pytest.mark.asyncio
    async def test_resume_non_interrupted_run_raises(self, manager):
        graph = _make_graph()
        run_id = manager.create_run(graph=graph)
        await asyncio.sleep(0.2)

        with pytest.raises(ValueError, match="cannot be resumed"):
            await manager.resume_run(run_id, {})

    @pytest.mark.asyncio
    async def test_hitl_timeout_marks_run_failed(self, manager):
        """A very short timeout should cause the run to fail."""
        graph = _make_interrupt_graph()
        hitl = HITLConfig(enabled=True, timeout_seconds=0)
        run_id = manager.create_run(graph=graph, hitl_config=hitl)

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            info = manager.get_run(run_id)
            if info.status not in ("pending", "running", "interrupted"):
                break

        info = manager.get_run(run_id)
        assert info.status == "failed"
        assert "timeout" in (info.error or "").lower()


class TestTTLEviction:
    """Tests for TTL and run limit eviction."""

    def test_eviction_on_ttl_expired(self, manager):
        """Runs whose created_at is older than TTL are evicted."""
        old_id = "old-run-id"
        manager._runs[old_id] = RunInfo(
            run_id=old_id,
            status="completed",
            created_at=time.time() - RUN_TTL_SECONDS - 1,
        )
        manager._queues[old_id] = []

        # Trigger eviction (called internally by create_run)
        manager._evict_old_runs()

        assert old_id not in manager._runs

    def test_fresh_run_not_evicted(self, manager):
        """Runs created moments ago must not be evicted."""
        fresh_id = "fresh-run-id"
        manager._runs[fresh_id] = RunInfo(
            run_id=fresh_id,
            status="running",
            created_at=time.time(),
        )
        manager._queues[fresh_id] = []
        manager._evict_old_runs()

        assert fresh_id in manager._runs

    def test_run_limit_drops_oldest(self, manager):
        """When at RUN_LIMIT, the oldest run is evicted on new create."""
        # Fill up to the limit with synthetic entries
        for i in range(RUN_LIMIT):
            rid = f"synthetic-{i}"
            manager._runs[rid] = RunInfo(
                run_id=rid,
                status="completed",
                # Spread creation times so ordering is deterministic
                created_at=time.time() - (RUN_LIMIT - i),
            )
            manager._queues[rid] = []

        # The oldest synthetic run
        oldest = "synthetic-0"
        assert oldest in manager._runs

        # Trigger eviction via create_run
        manager._evict_old_runs()
        # After eviction the count should be below RUN_LIMIT
        assert len(manager._runs) < RUN_LIMIT

    def test_drop_run_sends_sentinel_to_subscribers(self, manager):
        """Dropping a run must close any attached subscriber queues."""
        run_id = "drop-me"
        q: asyncio.Queue[str | None] = asyncio.Queue()
        manager._runs[run_id] = RunInfo(run_id=run_id, status="running")
        manager._queues[run_id] = [q]

        manager._drop_run(run_id)

        assert run_id not in manager._runs
        # Queue should have received the sentinel
        assert not q.empty()
        assert q.get_nowait() is None


# ================================================================== integration


class TestSSEIntegration:
    """Integration tests using httpx.AsyncClient against the real FastAPI app."""

    @pytest_asyncio.fixture
    async def client(self, isolated_bus, monkeypatch):
        """Create a test client with dev mode enabled so auth is skipped."""
        from httpx import ASGITransport, AsyncClient
        from agent_orchestrator.dashboard.app import create_dashboard_app

        monkeypatch.setenv("ALLOW_DEV_MODE", "true")

        # Reset EventBus singleton so the app uses our isolated_bus
        EventBus._instance = isolated_bus

        app = create_dashboard_app(event_bus=isolated_bus)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client

        EventBus.reset()

    @pytest.mark.asyncio
    async def test_post_runs_returns_run_id(self, client):
        resp = await client.post("/api/runs", json={"input": {"hello": "world"}})
        assert resp.status_code == 201
        data = resp.json()
        assert "run_id" in data
        assert len(data["run_id"]) > 0

    @pytest.mark.asyncio
    async def test_get_run_not_found(self, client):
        resp = await client.get("/api/runs/does-not-exist")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_run_status(self, client):
        resp = await client.post("/api/runs", json={"input": {}})
        run_id = resp.json()["run_id"]

        # Poll until completed
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            status_resp = await client.get(f"/api/runs/{run_id}")
            if status_resp.json().get("status") == "completed":
                break

        status_resp = await client.get(f"/api/runs/{run_id}")
        assert status_resp.status_code == 200
        body = status_resp.json()
        assert body["run_id"] == run_id
        assert body["status"] == "completed"

    @pytest.mark.asyncio
    async def test_stream_run_not_found(self, client):
        resp = await client.get("/api/runs/bad-id/stream")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_stream_run_content_type(self, client):
        resp = await client.post("/api/runs", json={"input": {}})
        run_id = resp.json()["run_id"]

        stream_resp = await client.get(f"/api/runs/{run_id}/stream")
        assert stream_resp.status_code == 200
        assert "text/event-stream" in stream_resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_stream_run_contains_events(self, client):
        resp = await client.post("/api/runs", json={"input": {"x": 99}})
        run_id = resp.json()["run_id"]

        stream_resp = await client.get(f"/api/runs/{run_id}/stream")
        content = stream_resp.text

        # Must have at least one data: line
        data_lines = [line for line in content.split("\n") if line.startswith("data: ")]
        assert len(data_lines) >= 1

    @pytest.mark.asyncio
    async def test_stream_mode_values_query_param(self, client):
        resp = await client.post("/api/runs", json={"input": {}, "stream_mode": "values"})
        run_id = resp.json()["run_id"]

        stream_resp = await client.get(f"/api/runs/{run_id}/stream?stream_mode=values")
        data_lines = [line for line in stream_resp.text.split("\n") if line.startswith("data: ")]
        assert len(data_lines) >= 1

    @pytest.mark.asyncio
    async def test_post_runs_invalid_stream_mode(self, client):
        resp = await client.post("/api/runs", json={"stream_mode": "invalid"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_resume_run_not_found(self, client):
        resp = await client.post("/api/runs/bad-run/resume", json={"human_input": {}})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_resume_non_interrupted_run_returns_400(self, client):
        resp = await client.post("/api/runs", json={"input": {}})
        run_id = resp.json()["run_id"]

        # Wait for it to complete
        await asyncio.sleep(0.3)

        resume_resp = await client.post(
            f"/api/runs/{run_id}/resume", json={"human_input": {"approved": True}}
        )
        assert resume_resp.status_code == 400


# ------------------------------------------------------------------ helper that builds an interrupt+finalize graph


def _interrupt_graph_with_finalize() -> CompiledGraph:
    """Build a two-node graph: interrupt_node -> finalize.

    When auto_approve is set the interrupt is immediately resolved
    by re-running the graph with the merged state, so finalize runs.
    """
    sg = StateGraph()

    _interrupt_raised: list[bool] = [False]

    async def interrupt_once(state):
        if not _interrupt_raised[0]:
            _interrupt_raised[0] = True
            raise GraphInterrupt(
                Interrupt(
                    interrupt_type=InterruptType.APPROVAL,
                    message="Please approve",
                    node="interrupt_once",
                )
            )
        return state

    async def finalize(state):
        return {"finalized": True}

    sg.add_node("interrupt_once", interrupt_once)
    sg.add_node("finalize", finalize)
    sg.add_edge(START, "interrupt_once")
    sg.add_edge("interrupt_once", "finalize")
    sg.add_edge("finalize", END)
    return sg.compile()
