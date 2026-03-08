"""Cooperation — inter-agent communication and coordination protocols."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class TaskAssignment:
    """A task delegated from one agent to another."""

    task_id: str
    from_agent: str
    to_agent: str
    description: str
    context: dict[str, Any] = field(default_factory=dict)
    priority: Priority = Priority.NORMAL
    depends_on: list[str] = field(default_factory=list)


@dataclass
class TaskReport:
    """Result reported back from a specialist to the coordinator."""

    task_id: str
    agent_name: str
    success: bool
    output: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0


@dataclass
class Artifact:
    """A shared artifact produced by an agent (code, spec, test result)."""

    name: str
    type: str  # "code", "spec", "test_result", "config"
    content: Any
    produced_by: str
    version: int = 1
    timestamp: float = field(default_factory=time.time)


@dataclass
class AgentMessage:
    """A message sent between agents."""

    from_agent: str
    to_agent: str | None  # None = broadcast to all
    content: str
    message_type: str = "info"  # "info", "request", "response", "conflict"
    related_task_id: str | None = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class ConflictRecord:
    """Records when two agents modify the same resource."""

    resource: str
    agents: list[str]
    task_ids: list[str]
    resolved: bool = False
    resolution: str | None = None
    timestamp: float = field(default_factory=time.time)


class SharedContextStore:
    """Shared store for inter-agent artifacts, messages, and conflict tracking."""

    def __init__(self) -> None:
        self._artifacts: dict[str, Artifact] = {}
        self._reports: list[TaskReport] = []
        self._messages: list[AgentMessage] = []
        self._conflicts: list[ConflictRecord] = []
        self._subscribers: list[asyncio.Queue[Artifact]] = []
        self._message_subscribers: list[asyncio.Queue[AgentMessage]] = []

    def publish(self, artifact: Artifact) -> None:
        existing = self._artifacts.get(artifact.name)
        if existing:
            artifact.version = existing.version + 1
            # Conflict detection: different agent modifying same artifact
            if existing.produced_by != artifact.produced_by:
                self._conflicts.append(
                    ConflictRecord(
                        resource=artifact.name,
                        agents=[existing.produced_by, artifact.produced_by],
                        task_ids=[],
                    )
                )
        self._artifacts[artifact.name] = artifact
        # Notify subscribers
        for queue in self._subscribers:
            try:
                queue.put_nowait(artifact)
            except asyncio.QueueFull:
                pass

    def get_artifact(self, name: str) -> Artifact | None:
        return self._artifacts.get(name)

    def list_artifacts(self) -> list[str]:
        return list(self._artifacts.keys())

    def get_all_artifacts(self) -> dict[str, Artifact]:
        return dict(self._artifacts)

    def subscribe_artifacts(self) -> asyncio.Queue[Artifact]:
        queue: asyncio.Queue[Artifact] = asyncio.Queue(maxsize=100)
        self._subscribers.append(queue)
        return queue

    def unsubscribe_artifacts(self, queue: asyncio.Queue[Artifact]) -> None:
        self._subscribers = [q for q in self._subscribers if q is not queue]

    def report(self, report: TaskReport) -> None:
        self._reports.append(report)

    def get_reports(self, task_id: str | None = None) -> list[TaskReport]:
        if task_id:
            return [r for r in self._reports if r.task_id == task_id]
        return list(self._reports)

    # --- Agent Messages ---

    def send_message(self, message: AgentMessage) -> None:
        self._messages.append(message)
        for queue in self._message_subscribers:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                pass

    def get_messages(
        self,
        agent_name: str | None = None,
        task_id: str | None = None,
    ) -> list[AgentMessage]:
        msgs = self._messages
        if agent_name:
            msgs = [m for m in msgs if m.to_agent == agent_name or m.to_agent is None]
        if task_id:
            msgs = [m for m in msgs if m.related_task_id == task_id]
        return msgs

    def subscribe_messages(self) -> asyncio.Queue[AgentMessage]:
        queue: asyncio.Queue[AgentMessage] = asyncio.Queue(maxsize=100)
        self._message_subscribers.append(queue)
        return queue

    def unsubscribe_messages(self, queue: asyncio.Queue[AgentMessage]) -> None:
        self._message_subscribers = [q for q in self._message_subscribers if q is not queue]

    # --- Conflict Management ---

    def get_conflicts(self, unresolved_only: bool = False) -> list[ConflictRecord]:
        if unresolved_only:
            return [c for c in self._conflicts if not c.resolved]
        return list(self._conflicts)

    def resolve_conflict(self, resource: str, resolution: str) -> bool:
        for conflict in self._conflicts:
            if conflict.resource == resource and not conflict.resolved:
                conflict.resolved = True
                conflict.resolution = resolution
                return True
        return False


class CooperationProtocol:
    """Manages the delegation, parallel execution, and result collection workflow."""

    def __init__(self) -> None:
        self.store = SharedContextStore()
        self._pending: dict[str, TaskAssignment] = {}
        self._completed: dict[str, TaskReport] = {}
        self._running: set[str] = set()

    def assign(self, assignment: TaskAssignment) -> None:
        self._pending[assignment.task_id] = assignment

    def mark_running(self, task_id: str) -> None:
        self._running.add(task_id)

    def complete(self, report: TaskReport) -> None:
        self._completed[report.task_id] = report
        self._pending.pop(report.task_id, None)
        self._running.discard(report.task_id)
        self.store.report(report)

    def get_pending(self, agent_name: str | None = None) -> list[TaskAssignment]:
        tasks = list(self._pending.values())
        if agent_name:
            tasks = [t for t in tasks if t.to_agent == agent_name]
        return tasks

    def get_ready_tasks(self) -> list[TaskAssignment]:
        """Return tasks whose dependencies are all completed and not already running."""
        return [
            task
            for task in self._pending.values()
            if task.task_id not in self._running
            and all(dep in self._completed for dep in task.depends_on)
        ]

    def get_parallel_batches(self) -> list[list[TaskAssignment]]:
        """Group ready tasks into batches that can run in parallel.

        Tasks with no dependencies between each other go in the same batch.
        """
        ready = self.get_ready_tasks()
        if not ready:
            return []
        # All ready tasks can run in parallel (their deps are met)
        return [ready]

    def all_complete(self) -> bool:
        return len(self._pending) == 0

    def get_completed(self) -> dict[str, TaskReport]:
        return dict(self._completed)
