"""Audit logging — structured record of every significant agent action."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Event type constants
# ---------------------------------------------------------------------------

EVENT_AGENT_START = "agent.start"
EVENT_AGENT_COMPLETE = "agent.complete"
EVENT_AGENT_ERROR = "agent.error"
EVENT_TOOL_CALL = "tool.call"
EVENT_TOOL_RESULT = "tool.result"
EVENT_PROVIDER_CALL = "provider.call"
EVENT_PROVIDER_ERROR = "provider.error"
EVENT_BUDGET_WARNING = "budget.warning"
EVENT_BUDGET_EXCEEDED = "budget.exceeded"
EVENT_ESCALATION = "escalation"
EVENT_CONFLICT = "conflict"


@dataclass
class AuditEntry:
    timestamp: float
    event_type: str
    action: str
    agent_name: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    provider_key: str | None = None
    task_id: str | None = None
    cost_usd: float = 0.0
    tokens: int = 0
    tool_description: str | None = None


class AuditLog:
    """In-memory audit log with filtering and export capabilities."""

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def log(self, entry: AuditEntry) -> None:
        """Append an entry to the log."""
        self._entries.append(entry)

    def log_action(
        self,
        event_type: str,
        agent_name: str | None,
        action: str,
        *,
        details: dict[str, Any] | None = None,
        provider_key: str | None = None,
        task_id: str | None = None,
        cost_usd: float = 0.0,
        tokens: int = 0,
        tool_description: str | None = None,
    ) -> AuditEntry:
        """Create an AuditEntry from keyword args and record it.

        Returns the created entry so callers can inspect it if needed.
        """
        entry = AuditEntry(
            timestamp=time.time(),
            event_type=event_type,
            agent_name=agent_name,
            action=action,
            details=details or {},
            provider_key=provider_key,
            task_id=task_id,
            cost_usd=cost_usd,
            tokens=tokens,
            tool_description=tool_description,
        )
        self.log(entry)
        return entry

    # ------------------------------------------------------------------
    # Read / query
    # ------------------------------------------------------------------

    def get_entries(
        self,
        event_type: str | None = None,
        agent_name: str | None = None,
        task_id: str | None = None,
        since: float | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Return entries matching the given filters, newest last."""
        results: list[AuditEntry] = []
        for entry in self._entries:
            if event_type is not None and entry.event_type != event_type:
                continue
            if agent_name is not None and entry.agent_name != agent_name:
                continue
            if task_id is not None and entry.task_id != task_id:
                continue
            if since is not None and entry.timestamp < since:
                continue
            results.append(entry)
        # Apply limit from the tail (most recent)
        return results[-limit:]

    def get_agent_history(self, agent_name: str) -> list[AuditEntry]:
        """All entries for a specific agent, in chronological order."""
        return [e for e in self._entries if e.agent_name == agent_name]

    def get_task_trace(self, task_id: str) -> list[AuditEntry]:
        """Full chronological trace of all events for a task."""
        return [e for e in self._entries if e.task_id == task_id]

    # ------------------------------------------------------------------
    # Export / maintenance
    # ------------------------------------------------------------------

    def export_json(self) -> list[dict[str, Any]]:
        """Export all entries as a list of plain dicts (JSON-serialisable)."""
        return [
            {
                "timestamp": e.timestamp,
                "event_type": e.event_type,
                "agent_name": e.agent_name,
                "action": e.action,
                "details": e.details,
                "provider_key": e.provider_key,
                "task_id": e.task_id,
                "cost_usd": e.cost_usd,
                "tokens": e.tokens,
                "tool_description": e.tool_description,
            }
            for e in self._entries
        ]

    def clear(self) -> None:
        """Remove all stored entries."""
        self._entries.clear()
