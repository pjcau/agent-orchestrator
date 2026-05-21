"""Tests for the team/agent cancel endpoint and disconnect-cancel paths.

The user flow this protects against is:

    1. user picks "multi-agent" mode by mistake (instead of "prompt")
    2. user presses Stop
    3. user expects the WHOLE chain to halt — not just the current step

Before this work, /api/team/run started a fire-and-forget background task
that was unreachable once the POST returned, so Stop only closed the
streaming WS and the team graph kept running on the server until natural
completion. These tests assert that the cancel endpoint actually reaches
into the live asyncio.Task and that the run coroutine observes the
cancellation and emits a terminal team.complete(cancelled=True) event.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def runtime_app(monkeypatch):
    # Disable auth so the cancel POSTs aren't bounced before reaching our
    # endpoint logic. This is the same pattern used by every other dashboard
    # test (test_modular_split, test_knowledge_routes, …).
    monkeypatch.setenv("ALLOW_DEV_MODE", "true")
    from agent_orchestrator.dashboard.app import create_dashboard_app
    from agent_orchestrator.dashboard.events import EventBus

    bus = EventBus()
    return create_dashboard_app(bus)


@pytest.fixture
def client(runtime_app):
    return TestClient(runtime_app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Cancel endpoint contract — independent of an actual team run
# ---------------------------------------------------------------------------


def test_cancel_unknown_job_returns_404(client):
    resp = client.post("/api/team/does-not-exist/cancel")
    assert resp.status_code == 404
    body = resp.json()
    assert body.get("error") == "Job not found"


def test_cancel_already_completed_job_returns_409(client, runtime_app):
    # Pre-populate active_jobs with a terminal job and no live future.
    runtime_app.state.active_jobs["done123"] = {
        "status": "completed",
        "task": "x",
        "result": {"success": True},
        "future": None,
    }
    resp = client.post("/api/team/done123/cancel")
    assert resp.status_code == 409
    body = resp.json()
    assert body["job_id"] == "done123"
    assert body["status"] == "completed"
    assert body["cancelled"] is False


def test_cancel_running_job_cancels_the_future(client, runtime_app):
    # Build a future that will never resolve on its own; cancel must reach it.
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        runtime_app.state.active_jobs["run123"] = {
            "status": "running",
            "task": "sleeping forever",
            "result": None,
            "future": future,
        }

        resp = client.post("/api/team/run123/cancel")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled"] is True
        assert body["status"] == "cancelling"
        # The endpoint must have called .cancel() on the live future
        assert future.cancelled()
    finally:
        if not future.done():
            future.cancel()
        loop.close()


# ---------------------------------------------------------------------------
# Real-world graph chain: start a team run, cancel it, assert the chain
# unwinds (the run coroutine observes CancelledError) and the terminal event
# carries cancelled=True.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancelled_team_run_unwinds_entire_chain(monkeypatch):
    """End-to-end: /api/team/run starts an asyncio.Task; the cancel endpoint
    cancels it; the background coroutine catches CancelledError and emits a
    terminal team.complete event with cancelled=True.

    `run_team` is patched to await forever so the cancellation has to travel
    through the chain (the await inside the coroutine) to take effect — if
    cancellation only stopped the wrong layer we'd hit the loop timeout.
    """
    monkeypatch.setenv("ALLOW_DEV_MODE", "true")
    from agent_orchestrator.dashboard.app import create_dashboard_app
    from agent_orchestrator.dashboard.events import EventBus, EventType

    bus = EventBus()
    app = create_dashboard_app(bus)

    # Subscribe to the event bus through its queue-based API and drain in a
    # background task so TEAM_COMPLETE events are captured as they arrive.
    queue = bus.subscribe()
    received: list[Any] = []

    async def _drain() -> None:
        while True:
            ev = await queue.get()
            received.append(ev)

    drainer = asyncio.create_task(_drain())

    # Replace run_team with a coroutine that simply awaits forever — the
    # cancellation MUST propagate into this await for the test to pass.
    async def _never_resolves(**kwargs: Any) -> dict:
        await asyncio.sleep(30)
        return {"success": True}

    monkeypatch.setattr(
        "agent_orchestrator.dashboard.agent_runtime_router.run_team",
        _never_resolves,
    )
    # Skip the repair loop wrapper so the patched run_team is reached directly.
    monkeypatch.setattr(
        "agent_orchestrator.dashboard.agent_runtime_router._repair_loop_enabled",
        lambda: False,
    )

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            start = await ac.post(
                "/api/team/run",
                json={"task": "halt this", "model": "fake/model"},
            )
            assert start.status_code == 200, start.text
            job_id = start.json()["job_id"]
            assert job_id

            # Give the background task a tick to register on the loop.
            await asyncio.sleep(0.05)

            cancel = await ac.post(f"/api/team/{job_id}/cancel")
            assert cancel.status_code == 200
            assert cancel.json()["cancelled"] is True

            # Wait (briefly) for the background task to observe the
            # cancellation and emit the terminal event.
            for _ in range(50):
                if any(
                    ev.event_type == EventType.TEAM_COMPLETE
                    and ev.data.get("job_id") == job_id
                    and ev.data.get("cancelled")
                    for ev in received
                ):
                    break
                await asyncio.sleep(0.05)

            cancelled_events = [
                ev
                for ev in received
                if ev.event_type == EventType.TEAM_COMPLETE
                and ev.data.get("job_id") == job_id
                and ev.data.get("cancelled")
            ]
            assert cancelled_events, (
                "Expected a TEAM_COMPLETE(cancelled=True) event after cancel; "
                f"received instead: {[ (ev.event_type, ev.data) for ev in received ]}"
            )
            assert app.state.active_jobs[job_id]["status"] == "cancelled"
    finally:
        drainer.cancel()
