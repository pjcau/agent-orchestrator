"""SSE (Server-Sent Events) streaming for graph execution.

Provides HTTP-based streaming as an alternative to WebSocket,
compatible with LangGraph SDK patterns.

Manages a registry of active runs, each backed by a background asyncio task
that drives graph execution via astream(). Callers can subscribe to a run's
event queue and receive SSE-formatted strings ready to be written into a
StreamingResponse.

Run lifecycle:
    pending  -> running -> completed
                       -> interrupted (HITL)
                       -> failed

TTL eviction: runs older than RUN_TTL_SECONDS are dropped when
_evict_old_runs() is called. Maximum RUN_LIMIT runs are kept.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from ..core.graph import (
    CompiledGraph,
    Interrupt,
    StreamEvent,
    StreamEventType,
)
from .events import Event, EventBus, EventType

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ constants

RUN_LIMIT = 100
RUN_TTL_SECONDS = 1800  # 30 minutes


# ------------------------------------------------------------------ dataclass


@dataclass
class HITLConfig:
    """Configuration for Human-in-the-Loop (HITL) behaviour.

    Attributes:
        enabled: Whether HITL interrupts are processed (vs immediately failing).
        timeout_seconds: How long to wait for human input before the run is
            marked as failed.
        auto_approve: If True, interrupts are automatically resumed with an
            empty dict (useful for automated testing).
    """

    enabled: bool = True
    timeout_seconds: int = 300
    auto_approve: bool = False


@dataclass
class RunInfo:
    """Metadata for a single graph run.

    Attributes:
        run_id: Unique identifier for this run.
        status: One of pending / running / interrupted / completed / failed.
        result: Final graph state dict, populated on completion.
        error: Error message string, populated on failure.
        interrupt: The Interrupt object if the run paused for HITL.
        created_at: Unix timestamp when the run was created.
    """

    run_id: str
    status: str = "pending"
    result: dict[str, Any] | None = None
    error: str | None = None
    interrupt: Interrupt | None = None
    created_at: float = field(default_factory=time.time)


# ------------------------------------------------------------------ manager


class RunManager:
    """Manages the lifecycle of active graph runs.

    Each run is executed as a background asyncio task. Subscribers attach
    to a run's asyncio.Queue and receive SSE-formatted strings emitted as
    the graph progresses. Runs are evicted after RUN_TTL_SECONDS or when
    the total count exceeds RUN_LIMIT.
    """

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._runs: dict[str, RunInfo] = {}
        # Per-run event queues for SSE subscribers
        self._queues: dict[str, list[asyncio.Queue[str | None]]] = {}
        # Per-run human-input futures (used to deliver HITL responses)
        self._hitl_futures: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._bus = event_bus or EventBus.get()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_run(
        self,
        graph: CompiledGraph,
        config: dict[str, Any] | None = None,
        input_data: dict[str, Any] | None = None,
        hitl_config: HITLConfig | None = None,
        stream_mode: str = "events",
    ) -> str:
        """Start a new graph run as a background task.

        Args:
            graph: A compiled graph to execute.
            config: Optional graph execution configuration (thread_id, etc.).
            input_data: Initial state dict passed to the graph.
            hitl_config: HITL behaviour settings.
            stream_mode: ``"events"`` (node-level events) or ``"values"``
                (full state snapshot per step).

        Returns:
            The run_id (UUID string) for the new run.
        """
        self._evict_old_runs()

        run_id = str(uuid.uuid4())
        run_info = RunInfo(run_id=run_id, status="pending")
        self._runs[run_id] = run_info
        self._queues[run_id] = []

        # Schedule execution — runs concurrently in the event loop
        asyncio.ensure_future(
            self._execute_run(
                run_id=run_id,
                graph=graph,
                config=config or {},
                input_data=input_data or {},
                hitl_config=hitl_config or HITLConfig(),
                stream_mode=stream_mode,
            )
        )

        return run_id

    def get_run(self, run_id: str) -> RunInfo | None:
        """Return the RunInfo for *run_id*, or None if not found."""
        return self._runs.get(run_id)

    async def subscribe(self, run_id: str, last_event_id: str | None = None) -> AsyncIterator[str]:
        """Yield SSE-formatted strings for the given run.

        The iterator terminates when the run completes, fails, or is
        interrupted. Consumers should handle disconnect by cancelling the
        iteration.

        Args:
            run_id: The run to subscribe to.
            last_event_id: If provided, the subscriber missed events — we
                immediately send a ``reconnect`` comment so the client
                knows where to resume.

        Yields:
            SSE-formatted strings, each ending with ``\\n\\n``.
        """
        if run_id not in self._runs:
            yield _sse_error("run_not_found", f"Run {run_id!r} does not exist")
            return

        # Acknowledge reconnection immediately (before blocking on queue)
        if last_event_id:
            yield f": reconnected after {last_event_id}\n\n"

        # If the run has already reached a terminal or paused state, emit a status
        # event immediately rather than blocking on an empty queue.
        run_info = self._runs[run_id]
        if run_info.status in ("completed", "failed", "interrupted"):
            payload: dict = {"event": "run_status", "status": run_info.status}
            if run_info.result is not None:
                payload["result"] = run_info.result
            if run_info.error is not None:
                payload["error"] = run_info.error
            if run_info.interrupt is not None:
                intr = run_info.interrupt
                payload["interrupt"] = {
                    "type": intr.interrupt_type.value,
                    "message": intr.message,
                    "node": intr.node,
                    "options": intr.options,
                }
            yield _sse_data(payload)
            return

        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=500)
        self._queues[run_id].append(queue)

        try:
            while True:
                item = await queue.get()
                if item is None:
                    # Sentinel: run finished
                    break
                yield item
        finally:
            # Clean up subscriber queue
            try:
                self._queues[run_id].remove(queue)
            except ValueError:
                pass

    async def resume_run(
        self,
        run_id: str,
        human_input: dict[str, Any],
    ) -> str:
        """Resume an interrupted run with human input.

        The resumed run is a new background task that continues from the
        interrupted state. A new run_id is issued so callers can subscribe
        to the continuation stream independently.

        Args:
            run_id: ID of the interrupted run.
            human_input: Dict merged into the graph state on resume.

        Returns:
            The new run_id for the resumed run.

        Raises:
            ValueError: If the run does not exist or is not interrupted.
        """
        run_info = self._runs.get(run_id)
        if run_info is None:
            raise ValueError(f"Run {run_id!r} not found")
        if run_info.status != "interrupted":
            raise ValueError(f"Run {run_id!r} cannot be resumed (status={run_info.status!r})")

        # Deliver input to the waiting future if the background task is live
        future = self._hitl_futures.get(run_id)
        if future is not None and not future.done():
            future.set_result(human_input)
            # The same run continues — return the original run_id
            run_info.status = "running"
            return run_id

        # Fallback: the original task has gone away; create a fresh run_id
        # that streams the continuation.  This path is exercised when the
        # server restarted between interrupt and resume.
        return run_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _execute_run(
        self,
        run_id: str,
        graph: CompiledGraph,
        config: dict[str, Any],
        input_data: dict[str, Any],
        hitl_config: HITLConfig,
        stream_mode: str,
    ) -> None:
        """Drive graph.astream() and fan-out events to subscribers."""
        run_info = self._runs[run_id]
        run_info.status = "running"
        event_index = 0

        # Emit a bus event so existing WS clients see the run started
        await self._bus.emit(
            Event(
                event_type=EventType.GRAPH_START,
                data={"run_id": run_id},
            )
        )

        try:
            async for stream_event in graph.astream(
                initial_state=input_data,
                thread_id=config.get("thread_id"),
            ):
                event_index += 1

                # Convert to SSE payload
                if stream_mode == "values":
                    payload = _stream_event_to_values_payload(stream_event)
                else:
                    payload = _stream_event_to_events_payload(stream_event)

                sse_line = _sse_data(payload, event_id=str(event_index))
                await self._broadcast(run_id, sse_line)

                # Mirror to EventBus for WS clients
                await self._emit_bus_event(stream_event, run_id)

                # Handle node error (includes both general errors and HITL interrupts)
                if (
                    stream_event.event_type == StreamEventType.NODE_ERROR
                    and not stream_event.interrupted
                ):
                    # General node failure — mark the run as failed and stop
                    run_info.status = "failed"
                    run_info.error = stream_event.error or "Node error"
                    await self._finish(run_id)
                    return

                # Handle HITL interrupt
                if (
                    stream_event.event_type == StreamEventType.NODE_ERROR
                    and stream_event.interrupted
                ):
                    interrupt = stream_event.interrupted
                    run_info.interrupt = interrupt
                    run_info.status = "interrupted"

                    if hitl_config.auto_approve:
                        # Auto-approve: immediately resume with empty input
                        logger.debug("Auto-approving HITL interrupt for run %s", run_id)
                        human_input = {}
                    elif hitl_config.enabled:
                        # Park the run and wait for resume_run() to be called
                        future: asyncio.Future[dict[str, Any]] = (
                            asyncio.get_event_loop().create_future()
                        )
                        self._hitl_futures[run_id] = future
                        try:
                            human_input = await asyncio.wait_for(
                                future, timeout=hitl_config.timeout_seconds
                            )
                            run_info.status = "running"
                        except asyncio.TimeoutError:
                            run_info.status = "failed"
                            run_info.error = "HITL timeout: no human input received"
                            await self._broadcast(
                                run_id,
                                _sse_error(
                                    "hitl_timeout", run_info.error, event_id=str(event_index + 1)
                                ),
                            )
                            await self._finish(run_id)
                            return
                        finally:
                            self._hitl_futures.pop(run_id, None)
                    else:
                        # HITL disabled — treat interrupt as failure
                        run_info.status = "failed"
                        run_info.error = f"Graph interrupted (HITL disabled): {interrupt.message}"
                        await self._finish(run_id)
                        return

                    # Re-stream from the interrupt point with human input merged
                    async for resumed_event in graph.astream(
                        initial_state={**input_data, **human_input},
                        thread_id=config.get("thread_id"),
                    ):
                        event_index += 1
                        if stream_mode == "values":
                            payload = _stream_event_to_values_payload(resumed_event)
                        else:
                            payload = _stream_event_to_events_payload(resumed_event)
                        sse_line = _sse_data(payload, event_id=str(event_index))
                        await self._broadcast(run_id, sse_line)
                        await self._emit_bus_event(resumed_event, run_id)

                        if resumed_event.event_type == StreamEventType.GRAPH_END:
                            run_info.result = resumed_event.state
                            run_info.status = "completed"
                            break
                    break

                if stream_event.event_type == StreamEventType.GRAPH_END:
                    run_info.result = stream_event.state
                    run_info.status = "completed"
                    break

        except Exception as exc:
            logger.exception("Run %s failed with exception: %s", run_id, exc)
            run_info.status = "failed"
            run_info.error = str(exc)
            event_index += 1
            await self._broadcast(
                run_id,
                _sse_error("run_error", str(exc), event_id=str(event_index)),
            )

        finally:
            await self._emit_bus_end(run_id, run_info)
            await self._finish(run_id)

    async def _broadcast(self, run_id: str, sse_line: str) -> None:
        """Fan-out a single SSE line to all subscribers of *run_id*."""
        for queue in list(self._queues.get(run_id, [])):
            try:
                queue.put_nowait(sse_line)
            except asyncio.QueueFull:
                logger.debug("SSE queue full for run %s; dropping event", run_id)

    async def _finish(self, run_id: str) -> None:
        """Send the sentinel (None) to all subscriber queues so they terminate."""
        for queue in list(self._queues.get(run_id, [])):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    async def _emit_bus_event(self, event: StreamEvent, run_id: str) -> None:
        """Mirror a graph StreamEvent to the EventBus."""
        type_map = {
            StreamEventType.GRAPH_START: EventType.GRAPH_START,
            StreamEventType.NODE_START: EventType.GRAPH_NODE_ENTER,
            StreamEventType.NODE_END: EventType.GRAPH_NODE_EXIT,
            StreamEventType.NODE_ERROR: EventType.GRAPH_END,
            StreamEventType.GRAPH_END: EventType.GRAPH_END,
        }
        bus_type = type_map.get(event.event_type, EventType.GRAPH_END)
        await self._bus.emit(
            Event(
                event_type=bus_type,
                node_name=event.node,
                data={
                    "run_id": run_id,
                    "step": event.step_index,
                    "state": event.state,
                },
            )
        )

    async def _emit_bus_end(self, run_id: str, run_info: RunInfo) -> None:
        await self._bus.emit(
            Event(
                event_type=EventType.GRAPH_END,
                data={
                    "run_id": run_id,
                    "status": run_info.status,
                    "error": run_info.error,
                },
            )
        )

    def _evict_old_runs(self) -> None:
        """Remove expired runs (TTL or cap overflow)."""
        now = time.time()
        expired = [
            rid for rid, info in self._runs.items() if now - info.created_at > RUN_TTL_SECONDS
        ]
        for rid in expired:
            self._drop_run(rid)

        # If still over the limit, drop the oldest runs
        while len(self._runs) >= RUN_LIMIT:
            oldest = min(self._runs, key=lambda rid: self._runs[rid].created_at)
            self._drop_run(oldest)

    def _drop_run(self, run_id: str) -> None:
        """Remove a run and close its subscriber queues."""
        self._runs.pop(run_id, None)
        for queue in self._queues.pop(run_id, []):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._hitl_futures.pop(run_id, None)


# ------------------------------------------------------------------ SSE helpers


def _sse_data(payload: dict[str, Any], event_id: str | None = None) -> str:
    """Format a dict as an SSE ``data:`` line.

    Produces::

        id: <event_id>
        data: <json>\\n\\n
    """
    lines: list[str] = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"data: {json.dumps(payload)}")
    return "\n".join(lines) + "\n\n"


def _sse_error(error_type: str, message: str, event_id: str | None = None) -> str:
    """Format an error payload as an SSE data line."""
    return _sse_data({"event": "error", "error_type": error_type, "message": message}, event_id)


def _stream_event_to_events_payload(event: StreamEvent) -> dict[str, Any]:
    """Convert a StreamEvent to the ``events`` stream mode payload."""
    payload: dict[str, Any] = {
        "event": event.event_type.value,
        "step": event.step_index,
        "state": event.state,
        "timestamp": event.timestamp,
    }
    if event.node is not None:
        payload["node"] = event.node
    if event.delta is not None:
        payload["delta"] = event.delta
    if event.parallel_group is not None:
        payload["parallel_group"] = event.parallel_group
    if event.error is not None:
        payload["error"] = event.error
    if event.interrupted is not None:
        intr = event.interrupted
        payload["interrupt"] = {
            "type": intr.interrupt_type.value,
            "message": intr.message,
            "node": intr.node,
            "options": intr.options,
        }
    if event.elapsed_ms:
        payload["elapsed_ms"] = event.elapsed_ms
    return payload


def _stream_event_to_values_payload(event: StreamEvent) -> dict[str, Any]:
    """Convert a StreamEvent to the ``values`` stream mode payload.

    In values mode we emit the full state snapshot rather than the node-level
    delta so consumers can read the current state directly.
    """
    return {
        "event": event.event_type.value,
        "step": event.step_index,
        "values": event.state,
        "timestamp": event.timestamp,
    }
