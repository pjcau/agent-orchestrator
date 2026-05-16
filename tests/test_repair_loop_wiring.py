"""Integration test for the Phase-5 repair-loop wiring in `agent_runtime_router`.

Exercises the public helpers (`_run_team_with_repair`, `_make_emit_bridge`,
`_repair_loop_enabled`) without standing up the full FastAPI app — `run_team`
is monkeypatched and the EventBus is used directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.dashboard import agent_runtime_router as router_mod
from agent_orchestrator.dashboard.events import EventBus, EventType


# ---------------------------------------------------------------------------
# _repair_loop_enabled
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("TRUE", True),
        (" true ", True),
        ("false", False),
        ("", False),
        ("1", False),  # only literal "true" enables it — keeps the rule unambiguous
    ],
)
def test_repair_loop_enabled_env(monkeypatch: pytest.MonkeyPatch, value: str, expected: bool):
    monkeypatch.setenv("REPAIR_LOOP_ENABLED", value)
    assert router_mod._repair_loop_enabled() is expected


def test_repair_loop_disabled_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("REPAIR_LOOP_ENABLED", raising=False)
    assert router_mod._repair_loop_enabled() is False


# ---------------------------------------------------------------------------
# _make_emit_bridge
# ---------------------------------------------------------------------------


def test_emit_bridge_returns_none_for_no_bus():
    assert router_mod._make_emit_bridge(None) is None


@pytest.mark.asyncio
async def test_emit_bridge_forwards_known_events_to_bus():
    EventBus.reset()
    bus = EventBus.get()
    queue = bus.subscribe()
    emit = router_mod._make_emit_bridge(bus)
    assert emit is not None

    emit("verifier.started", {"name": "syntax"})
    # let the scheduled task run
    event = await queue.get()
    assert event.event_type == EventType.VERIFIER_STARTED
    assert event.data == {"name": "syntax"}


@pytest.mark.asyncio
async def test_emit_bridge_drops_unknown_event_names():
    EventBus.reset()
    bus = EventBus.get()
    queue = bus.subscribe()
    emit = router_mod._make_emit_bridge(bus)
    assert emit is not None

    emit("not.a.real.event", {"foo": 1})
    # nothing should land on the queue — give the loop a tick and assert empty.
    import asyncio

    await asyncio.sleep(0)
    assert queue.empty()


# ---------------------------------------------------------------------------
# _run_team_with_repair — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_team_with_repair_passes_through_underlying_dict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """When verifiers pass on the first attempt, the wrapper must return the
    underlying run_team dict with a `repair` summary added — preserving fields
    the dashboard relies on (output, plan, agent_costs, total_tokens)."""
    EventBus.reset()
    bus = EventBus.get()

    calls: list[dict[str, Any]] = []

    async def fake_run_team(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        # Touch the workdir so verifiers find a real (empty) directory.
        return {
            "success": True,
            "output": "all good",
            "plan": "1. do thing",
            "agent_costs": {"backend": 0.01},
            "total_tokens": 1234,
            "total_cost_usd": 0.01,
            "elapsed_s": 2.5,
        }

    monkeypatch.setattr(router_mod, "run_team", fake_run_team)
    # Keep the repair loop bounded for the test even if env vars leak in.
    monkeypatch.setenv("REPAIR_LOOP_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("REPAIR_LOOP_MAX_COST_USD", "1.0")

    result = await router_mod._run_team_with_repair(
        "build me a todo app",
        provider=object(),
        event_bus=bus,
        working_directory=str(tmp_path),
        usage_db=None,
        session_id="sess-1",
        conversation_id=None,
        conversation_manager=None,
        sandbox_manager=None,
    )

    # The underlying call ran exactly once (verifiers passed → no retry).
    assert len(calls) == 1
    # Original run_team fields survive.
    assert result["output"] == "all good"
    assert result["plan"] == "1. do thing"
    assert result["agent_costs"] == {"backend": 0.01}
    assert result["total_tokens"] == 1234
    # Repair-loop metadata is layered on top.
    assert result["repair"]["status"] == "passed"
    assert result["repair"]["attempts"] == 1
    assert result["repair"]["final_passed"] is True
    assert result["repair"]["final_failures"] == []
    # `success` is the AND of run_team success and verifier pass.
    assert result["success"] is True


@pytest.mark.asyncio
async def test_run_team_with_repair_success_false_when_run_team_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """If `run_team` reports success=False, the wrapper must not flip it to
    True even when verifiers pass on the (broken) workspace."""
    EventBus.reset()

    async def fake_run_team(**kwargs: Any) -> dict[str, Any]:
        return {
            "success": False,
            "output": "team-lead bailed out",
            "total_cost_usd": 0.0,
            "elapsed_s": 0.1,
        }

    monkeypatch.setattr(router_mod, "run_team", fake_run_team)
    monkeypatch.setenv("REPAIR_LOOP_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("REPAIR_LOOP_MAX_COST_USD", "1.0")

    result = await router_mod._run_team_with_repair(
        "task",
        provider=object(),
        event_bus=EventBus.get(),
        working_directory=str(tmp_path),
        usage_db=None,
        session_id="s",
        conversation_id=None,
        conversation_manager=None,
        sandbox_manager=None,
    )

    assert result["success"] is False
    assert result["repair"]["status"] == "passed"  # verifiers passed on empty dir
    assert result["repair"]["attempts"] == 1


@pytest.mark.asyncio
async def test_run_team_with_repair_emits_lifecycle_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """The loop must emit repair.started → repair.attempt_* → repair.finished
    onto the bus so the dashboard can render attempts."""
    EventBus.reset()
    bus = EventBus.get()
    queue = bus.subscribe()

    async def fake_run_team(**kwargs: Any) -> dict[str, Any]:
        return {
            "success": True,
            "output": "ok",
            "total_cost_usd": 0.0,
        }

    monkeypatch.setattr(router_mod, "run_team", fake_run_team)
    monkeypatch.setenv("REPAIR_LOOP_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("REPAIR_LOOP_MAX_COST_USD", "1.0")

    await router_mod._run_team_with_repair(
        "task",
        provider=object(),
        event_bus=bus,
        working_directory=str(tmp_path),
        usage_db=None,
        session_id="s",
        conversation_id=None,
        conversation_manager=None,
        sandbox_manager=None,
    )

    # The bridge schedules emits via `asyncio.create_task` — yield a few
    # times so the queued bus.emit coroutines get to run before we drain.
    import asyncio

    for _ in range(5):
        await asyncio.sleep(0)

    seen: list[str] = []
    while not queue.empty():
        seen.append((await queue.get()).event_type.value)

    assert "repair.started" in seen
    assert "repair.attempt_started" in seen
    assert "repair.attempt_finished" in seen
    assert "repair.finished" in seen
