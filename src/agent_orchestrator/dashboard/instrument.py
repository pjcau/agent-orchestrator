"""Instrumentation — monkey-patch the orchestrator, agent, and graph to emit events.

Usage:
    from agent_orchestrator.dashboard.instrument import instrument_all
    instrument_all()  # Call once at startup
"""

from __future__ import annotations

import functools
import time
from typing import Any

from ..core.agent import Agent, Task, TaskResult, TaskStatus
from ..core.graph import CompiledGraph
from ..core.orchestrator import Orchestrator, OrchestratorResult
from ..core.cooperation import CooperationProtocol, TaskAssignment, TaskReport
from ..core.cache import InMemoryCache
from .events import Event, EventBus, EventType
from .tracing_metrics import record_llm_duration, record_node_duration, record_stall


def instrument_all(bus: EventBus | None = None) -> None:
    """Instrument all core classes to emit dashboard events and tracing metrics."""
    bus = bus or EventBus.get()
    _instrument_agent(bus)
    _instrument_orchestrator(bus)
    _instrument_graph(bus)
    _instrument_cooperation(bus)
    _instrument_cache(bus)
    _instrument_provider_metrics()


def _instrument_agent(bus: EventBus) -> None:
    original_execute = Agent.execute

    @functools.wraps(original_execute)
    async def patched_execute(self: Agent, task: Task) -> TaskResult:
        await bus.emit(
            Event(
                event_type=EventType.AGENT_SPAWN,
                agent_name=self.config.name,
                data={
                    "provider": self.config.provider_key,
                    "role": self.config.role[:200],
                    "tools": self.config.tools,
                    "task": task.description[:300],
                },
            )
        )

        result = await original_execute(self, task)

        if result.status == TaskStatus.COMPLETED:
            event_type = EventType.AGENT_COMPLETE
        elif result.status == TaskStatus.STALLED:
            event_type = EventType.AGENT_STALLED
            record_stall(self.config.provider_key)
        else:
            event_type = EventType.AGENT_ERROR

        await bus.emit(
            Event(
                event_type=event_type,
                agent_name=self.config.name,
                data={
                    "output": result.output[:300],
                    "steps": result.steps_taken,
                    "tokens": result.total_tokens,
                    "cost_usd": result.total_cost_usd,
                    "error": result.error,
                },
            )
        )

        await bus.emit(
            Event(
                event_type=EventType.TOKEN_UPDATE,
                agent_name=self.config.name,
                data={
                    "agent_tokens": result.total_tokens,
                    "agent_cost_usd": result.total_cost_usd,
                },
            )
        )

        return result

    Agent.execute = patched_execute


def _instrument_orchestrator(bus: EventBus) -> None:
    original_run = Orchestrator.run

    @functools.wraps(original_run)
    async def patched_run(
        self: Orchestrator, task_description: str, context: dict[str, Any] | None = None
    ) -> OrchestratorResult:
        await bus.emit(
            Event(
                event_type=EventType.ORCHESTRATOR_START,
                data={"task": task_description[:500]},
            )
        )

        result = await original_run(self, task_description, context)

        await bus.emit(
            Event(
                event_type=EventType.ORCHESTRATOR_END,
                data={
                    "success": result.success,
                    "total_cost_usd": result.total_cost_usd,
                    "total_tokens": result.total_tokens,
                },
            )
        )

        await bus.emit(
            Event(
                event_type=EventType.COST_UPDATE,
                data={"total_cost_usd": result.total_cost_usd},
            )
        )
        await bus.emit(
            Event(
                event_type=EventType.TOKEN_UPDATE,
                data={"total_tokens": result.total_tokens},
            )
        )

        return result

    Orchestrator.run = patched_run


def _instrument_graph(bus: EventBus) -> None:
    original_single = CompiledGraph._execute_single
    original_parallel = CompiledGraph._execute_parallel

    @functools.wraps(original_single)
    async def patched_single(self, node_name, state, steps, step_index, thread_id):
        await bus.emit(
            Event(
                event_type=EventType.GRAPH_NODE_ENTER,
                node_name=node_name,
                data={"step_index": step_index, "thread_id": thread_id},
            )
        )

        t0 = time.monotonic()
        result = await original_single(self, node_name, state, steps, step_index, thread_id)
        record_node_duration(node_name, time.monotonic() - t0)

        if result.interrupted:
            await bus.emit(
                Event(
                    event_type=EventType.GRAPH_INTERRUPT,
                    node_name=node_name,
                    data={"message": result.interrupted.message},
                )
            )
        else:
            await bus.emit(
                Event(
                    event_type=EventType.GRAPH_NODE_EXIT,
                    node_name=node_name,
                    data={"success": result.success, "step_index": step_index},
                )
            )

        return result

    @functools.wraps(original_parallel)
    async def patched_parallel(self, node_names, state, steps, step_index, thread_id):
        await bus.emit(
            Event(
                event_type=EventType.GRAPH_PARALLEL,
                data={"nodes": node_names, "step_index": step_index},
            )
        )

        result = await original_parallel(self, node_names, state, steps, step_index, thread_id)
        return result

    CompiledGraph._execute_single = patched_single
    CompiledGraph._execute_parallel = patched_parallel


def _instrument_cooperation(bus: EventBus) -> None:
    original_assign = CooperationProtocol.assign
    original_complete = CooperationProtocol.complete

    @functools.wraps(original_assign)
    def patched_assign(self: CooperationProtocol, assignment: TaskAssignment) -> None:
        import asyncio

        original_assign(self, assignment)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                bus.emit(
                    Event(
                        event_type=EventType.TASK_ASSIGNED,
                        data={
                            "task_id": assignment.task_id,
                            "from_agent": assignment.from_agent,
                            "to_agent": assignment.to_agent,
                            "description": assignment.description[:300],
                            "priority": assignment.priority.value,
                        },
                    )
                )
            )
        except RuntimeError:
            pass  # No event loop running

    @functools.wraps(original_complete)
    def patched_complete(self: CooperationProtocol, report: TaskReport) -> None:
        import asyncio

        original_complete(self, report)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                bus.emit(
                    Event(
                        event_type=EventType.TASK_COMPLETED,
                        data={
                            "task_id": report.task_id,
                            "agent_name": report.agent_name,
                            "success": report.success,
                            "cost_usd": report.cost_usd,
                        },
                    )
                )
            )
        except RuntimeError:
            pass

    CooperationProtocol.assign = patched_assign
    CooperationProtocol.complete = patched_complete


def _instrument_cache(bus: EventBus) -> None:
    original_get = InMemoryCache.get

    @functools.wraps(original_get)
    def patched_get(self, key):
        import asyncio

        result = original_get(self, key)
        event_type = EventType.CACHE_HIT if result is not None else EventType.CACHE_MISS
        try:
            loop = asyncio.get_running_loop()
            stats = self.get_stats()
            loop.create_task(
                bus.emit(
                    Event(
                        event_type=event_type,
                        data={
                            "key": key[:64],
                            "node_name": result.node_name if result else "",
                        },
                    )
                )
            )
            stats_dict = stats.to_dict()
            stats_dict["entries"] = self.size()
            loop.create_task(
                bus.emit(
                    Event(
                        event_type=EventType.CACHE_STATS,
                        data={"cache_stats": stats_dict},
                    )
                )
            )
        except RuntimeError:
            pass
        return result

    InMemoryCache.get = patched_get


def _instrument_provider_metrics() -> None:
    """Monkey-patch Provider.traced_complete to record LLM call durations."""
    from ..core.provider import Provider

    original_traced_complete = Provider.traced_complete

    @functools.wraps(original_traced_complete)
    async def patched_traced_complete(self, *args, **kwargs):
        t0 = time.monotonic()
        result = await original_traced_complete(self, *args, **kwargs)
        provider_name = type(self).__name__.lower().replace("provider", "")
        record_llm_duration(provider_name, time.monotonic() - t0)
        return result

    Provider.traced_complete = patched_traced_complete
