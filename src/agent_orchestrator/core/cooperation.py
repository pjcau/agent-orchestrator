"""Cooperation — inter-agent communication and coordination protocols."""

from __future__ import annotations

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


class SharedContextStore:
    """Shared store for inter-agent artifacts and state."""

    def __init__(self) -> None:
        self._artifacts: dict[str, Artifact] = {}
        self._messages: list[TaskReport] = []

    def publish(self, artifact: Artifact) -> None:
        existing = self._artifacts.get(artifact.name)
        if existing:
            artifact.version = existing.version + 1
        self._artifacts[artifact.name] = artifact

    def get_artifact(self, name: str) -> Artifact | None:
        return self._artifacts.get(name)

    def list_artifacts(self) -> list[str]:
        return list(self._artifacts.keys())

    def report(self, report: TaskReport) -> None:
        self._messages.append(report)

    def get_reports(self, task_id: str | None = None) -> list[TaskReport]:
        if task_id:
            return [r for r in self._messages if r.task_id == task_id]
        return list(self._messages)


class CooperationProtocol:
    """Manages the delegation and result collection workflow."""

    def __init__(self) -> None:
        self.store = SharedContextStore()
        self._pending: dict[str, TaskAssignment] = {}
        self._completed: dict[str, TaskReport] = {}

    def assign(self, assignment: TaskAssignment) -> None:
        self._pending[assignment.task_id] = assignment

    def complete(self, report: TaskReport) -> None:
        self._completed[report.task_id] = report
        self._pending.pop(report.task_id, None)
        self.store.report(report)

    def get_pending(self, agent_name: str | None = None) -> list[TaskAssignment]:
        tasks = list(self._pending.values())
        if agent_name:
            tasks = [t for t in tasks if t.to_agent == agent_name]
        return tasks

    def get_ready_tasks(self) -> list[TaskAssignment]:
        """Return tasks whose dependencies are all completed."""
        return [
            task
            for task in self._pending.values()
            if all(dep in self._completed for dep in task.depends_on)
        ]

    def all_complete(self) -> bool:
        return len(self._pending) == 0
