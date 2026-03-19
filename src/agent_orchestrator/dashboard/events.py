"""Event bus for orchestrator monitoring.

Captures events from the orchestrator, agents, graph engine, and cooperation
protocol, and broadcasts them to connected dashboard clients via WebSocket.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class EventType(str, Enum):
    # Orchestrator lifecycle
    ORCHESTRATOR_START = "orchestrator.start"
    ORCHESTRATOR_END = "orchestrator.end"

    # Agent events
    AGENT_SPAWN = "agent.spawn"
    AGENT_STEP = "agent.step"
    AGENT_TOOL_CALL = "agent.tool_call"
    AGENT_TOOL_RESULT = "agent.tool_result"
    AGENT_COMPLETE = "agent.complete"
    AGENT_ERROR = "agent.error"
    AGENT_STALLED = "agent.stalled"

    # Graph engine events
    GRAPH_START = "graph.start"
    GRAPH_NODE_ENTER = "graph.node.enter"
    GRAPH_NODE_EXIT = "graph.node.exit"
    GRAPH_EDGE = "graph.edge"
    GRAPH_PARALLEL = "graph.parallel"
    GRAPH_INTERRUPT = "graph.interrupt"
    GRAPH_END = "graph.end"

    # Cooperation events
    TASK_ASSIGNED = "cooperation.task_assigned"
    TASK_COMPLETED = "cooperation.task_completed"
    ARTIFACT_PUBLISHED = "cooperation.artifact_published"

    # Cache events
    CACHE_HIT = "cache.hit"
    CACHE_MISS = "cache.miss"
    CACHE_STATS = "cache.stats"

    # Team lifecycle (async)
    TEAM_STARTED = "team.started"
    TEAM_STEP = "team.step"
    TEAM_COMPLETE = "team.complete"

    # Loop detection events
    LOOP_WARNING = "loop.warning"
    LOOP_HARD_STOP = "loop.hard_stop"

    # Cost / metrics
    COST_UPDATE = "metrics.cost_update"
    TOKEN_UPDATE = "metrics.token_update"


@dataclass
class Event:
    event_type: EventType
    timestamp: float = field(default_factory=time.time)
    agent_name: str | None = None
    node_name: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return d


class EventBus:
    """Async event bus with WebSocket broadcast support."""

    _instance: EventBus | None = None

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[Event]] = []
        self._history: list[Event] = []
        self._max_history = 1000

    @classmethod
    def get(cls) -> EventBus:
        if cls._instance is None:
            cls._instance = EventBus()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    async def emit(self, event: Event) -> None:
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history :]
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # Drop events for slow consumers

    def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=200)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._subscribers = [q for q in self._subscribers if q is not queue]

    def get_history(self) -> list[Event]:
        return list(self._history)

    def get_snapshot(self) -> dict[str, Any]:
        """Return a summary snapshot of current state from event history."""
        agents: dict[str, dict[str, Any]] = {}
        tasks: list[dict[str, Any]] = []
        total_cost = 0.0
        total_tokens = 0
        orchestrator_status = "idle"
        graph_nodes: list[str] = []
        graph_edges: list[dict] = []
        cache_stats: dict[str, Any] = {"hits": 0, "misses": 0, "hit_rate": 0.0}

        for event in self._history:
            et = event.event_type

            if et == EventType.ORCHESTRATOR_START:
                orchestrator_status = "running"
            elif et == EventType.ORCHESTRATOR_END:
                orchestrator_status = "completed" if event.data.get("success") else "failed"

            elif et == EventType.AGENT_SPAWN:
                agents[event.agent_name or "unknown"] = {
                    "name": event.agent_name,
                    "status": "running",
                    "steps": 0,
                    "tokens": 0,
                    "cost_usd": 0.0,
                    "provider": event.data.get("provider", ""),
                    "role": event.data.get("role", ""),
                    "tools": event.data.get("tools", []),
                }
            elif et == EventType.AGENT_STEP and event.agent_name in agents:
                agents[event.agent_name]["steps"] += 1
            elif et == EventType.AGENT_COMPLETE and event.agent_name in agents:
                agents[event.agent_name]["status"] = "completed"
            elif et == EventType.AGENT_ERROR and event.agent_name in agents:
                agents[event.agent_name]["status"] = "error"
            elif et == EventType.AGENT_STALLED and event.agent_name in agents:
                agents[event.agent_name]["status"] = "stalled"

            elif et == EventType.TASK_ASSIGNED:
                tasks.append(
                    {
                        "task_id": event.data.get("task_id"),
                        "from_agent": event.data.get("from_agent"),
                        "to_agent": event.data.get("to_agent"),
                        "description": event.data.get("description", ""),
                        "status": "pending",
                        "priority": event.data.get("priority", "normal"),
                    }
                )
            elif et == EventType.TASK_COMPLETED:
                tid = event.data.get("task_id")
                for t in tasks:
                    if t["task_id"] == tid:
                        t["status"] = "completed" if event.data.get("success") else "failed"

            elif et == EventType.COST_UPDATE:
                total_cost = event.data.get("total_cost_usd", total_cost)
            elif et == EventType.TOKEN_UPDATE:
                total_tokens = event.data.get("total_tokens", total_tokens)
                if event.agent_name and event.agent_name in agents:
                    agents[event.agent_name]["tokens"] = event.data.get("agent_tokens", 0)
                    agents[event.agent_name]["cost_usd"] = event.data.get("agent_cost_usd", 0.0)

            elif et == EventType.CACHE_STATS:
                cache_stats = event.data.get("cache_stats", cache_stats)

            elif et == EventType.GRAPH_START:
                graph_nodes = event.data.get("nodes", [])
                graph_edges = event.data.get("edges", [])

        return {
            "orchestrator_status": orchestrator_status,
            "agents": agents,
            "tasks": tasks,
            "total_cost_usd": total_cost,
            "total_tokens": total_tokens,
            "graph": {"nodes": graph_nodes, "edges": graph_edges},
            "cache": cache_stats,
            "event_count": len(self._history),
        }
