"""Persistent task queue — in-memory now, Postgres-ready interface for later."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueuedTask:
    task_id: str
    description: str
    priority: int  # higher value = more urgent
    status: str = "pending"  # pending | running | completed | failed
    agent_name: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    result: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    retries: int = 0
    max_retries: int = 3


@dataclass
class QueueStats:
    pending: int
    running: int
    completed: int
    failed: int
    total: int


class TaskQueue:
    """In-memory priority task queue.

    Priority is descending (higher integer = processed first).
    Within the same priority, tasks are ordered FIFO by created_at.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, QueuedTask] = {}

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def enqueue(self, task: QueuedTask) -> str:
        """Add a task to the queue. Returns the task_id."""
        task.status = "pending"
        self._tasks[task.task_id] = task
        return task.task_id

    def dequeue(self, agent_name: str | None = None) -> QueuedTask | None:
        """Pop and return the highest-priority pending task.

        If agent_name is given, only tasks whose agent_name matches (or is
        None) are considered.
        """
        candidates = [
            t
            for t in self._tasks.values()
            if t.status == "pending"
            and (agent_name is None or t.agent_name is None or t.agent_name == agent_name)
        ]
        if not candidates:
            return None

        # Sort: priority descending, then created_at ascending (FIFO)
        candidates.sort(key=lambda t: (-t.priority, t.created_at))
        task = candidates[0]
        task.status = "running"
        task.started_at = time.time()
        return task

    def complete(self, task_id: str, result: str) -> None:
        """Mark a running task as completed."""
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.status = "completed"
        task.result = result
        task.completed_at = time.time()

    def fail(self, task_id: str, error: str) -> None:
        """Record a failure.

        If retries < max_retries the task is re-queued (status reset to
        "pending" and retry counter incremented).  Otherwise the task is
        permanently marked "failed".
        """
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.retries += 1
        if task.retries < task.max_retries:
            task.status = "pending"
            task.started_at = None
            task.result = error  # store last error for inspection
        else:
            task.status = "failed"
            task.result = error
            task.completed_at = time.time()

    def retry(self, task_id: str) -> bool:
        """Manually re-queue a failed task.  Returns True on success."""
        task = self._tasks.get(task_id)
        if task is None or task.status != "failed":
            return False
        task.status = "pending"
        task.started_at = None
        task.completed_at = None
        # Do NOT reset retries — caller explicitly requested retry
        return True

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_task(self, task_id: str) -> QueuedTask | None:
        return self._tasks.get(task_id)

    def get_pending(self) -> list[QueuedTask]:
        return [t for t in self._tasks.values() if t.status == "pending"]

    def get_running(self) -> list[QueuedTask]:
        return [t for t in self._tasks.values() if t.status == "running"]

    def get_stats(self) -> QueueStats:
        statuses = [t.status for t in self._tasks.values()]
        pending = statuses.count("pending")
        running = statuses.count("running")
        completed = statuses.count("completed")
        failed = statuses.count("failed")
        return QueueStats(
            pending=pending,
            running=running,
            completed=completed,
            failed=failed,
            total=len(statuses),
        )
