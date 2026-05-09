"""Typed cooperation messages — frozen dataclasses for inter-agent communication.

This module provides a typed wire format for the protocol implemented by
``core.cooperation``. Each message type is a frozen ``dataclass`` with two
adapters:

- ``from_dict(d)`` — tolerant constructor; missing optional fields default
  to ``None`` / empty collections so older callers keep working.
- ``to_dict()`` — exact reverse, suitable for queue serialisation, logging,
  audit trails, and the dashboard SSE bus.

Use ``parse_message(d)`` to dispatch a raw dict on the ``kind`` field. This
lets ``cooperation.py`` adopt typed handling incrementally without breaking
the existing dict-based callers (orchestrator, dashboard, MCP bridge).

See ``docs/cooperation-protocol.md`` for the full message catalogue, state
transitions, and error semantics.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, fields
from typing import Any, ClassVar


# ---------------------------------------------------------------------------
# Message kinds
# ---------------------------------------------------------------------------

#: ``delegate`` — coordinator assigns a task to a specialist agent.
KIND_DELEGATE = "delegate"
#: ``result`` — specialist reports task completion (success or failure).
KIND_RESULT = "result"
#: ``conflict`` — overlapping write to a shared resource detected.
KIND_CONFLICT = "conflict"
#: ``capability_query`` — peer asks "what can you do?" before delegating.
KIND_CAPABILITY_QUERY = "capability_query"
#: ``capability_response`` — peer replies with a list of supported skills.
KIND_CAPABILITY_RESPONSE = "capability_response"

ALL_KINDS: tuple[str, ...] = (
    KIND_DELEGATE,
    KIND_RESULT,
    KIND_CONFLICT,
    KIND_CAPABILITY_QUERY,
    KIND_CAPABILITY_RESPONSE,
)


def _new_message_id() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CooperationMessage:
    """Common header for every typed cooperation message.

    Subclasses MUST override ``kind`` (class variable) and may add their own
    payload fields. The header is stable across versions; payloads are
    additive.
    """

    #: Class-level marker copied into ``to_dict``. Subclasses override.
    kind: ClassVar[str] = "cooperation"

    from_agent: str = ""
    to_agent: str | None = None
    message_id: str = field(default_factory=_new_message_id)
    timestamp: float = field(default_factory=time.time)

    # ---- adapters -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict.

        Header fields (``message_id``, ``from_agent``, ``to_agent``,
        ``timestamp``, ``kind``) are emitted in a stable order. Subclass
        payload fields follow.
        """
        out: dict[str, Any] = {
            "kind": self.kind,
            "message_id": self.message_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "timestamp": self.timestamp,
        }
        # Append subclass-specific fields (skip the base header above).
        base_names = {"message_id", "from_agent", "to_agent", "timestamp"}
        for f in fields(self):
            if f.name in base_names:
                continue
            out[f.name] = getattr(self, f.name)
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CooperationMessage:
        """Tolerant constructor.

        Unknown keys are ignored. Missing fields fall back to their default
        factory (or ``None`` / ``[]`` / ``{}`` for typed payloads).
        """
        kwargs: dict[str, Any] = {}
        own_names = {f.name for f in fields(cls)}
        for name in own_names:
            if name in d:
                kwargs[name] = d[name]
        return cls(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Concrete messages
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DelegateMessage(CooperationMessage):
    """Coordinator → specialist: please run this task.

    Mirrors ``cooperation.TaskAssignment`` on the wire so the existing
    protocol can adopt typed handling without changing its in-memory store.
    """

    kind: ClassVar[str] = KIND_DELEGATE

    task_id: str = ""
    description: str = ""
    priority: str = "normal"
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResultMessage(CooperationMessage):
    """Specialist → coordinator: I am done (succeeded or failed)."""

    kind: ClassVar[str] = KIND_RESULT

    task_id: str = ""
    success: bool = False
    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CapabilityQueryMessage(CooperationMessage):
    """Coordinator → peer: tell me what skills/tools you support."""

    kind: ClassVar[str] = KIND_CAPABILITY_QUERY

    query: str = ""


@dataclass(frozen=True)
class CapabilityResponseMessage(CooperationMessage):
    """Peer → coordinator: my advertised capabilities."""

    kind: ClassVar[str] = KIND_CAPABILITY_RESPONSE

    capabilities: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ConflictMessage(CooperationMessage):
    """Any agent → coordinator: shared-resource conflict detected."""

    kind: ClassVar[str] = KIND_CONFLICT

    task_id: str = ""
    reason: str = ""
    proposed_resolution: str | None = None


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


_KIND_TO_CLASS: dict[str, type[CooperationMessage]] = {
    KIND_DELEGATE: DelegateMessage,
    KIND_RESULT: ResultMessage,
    KIND_CAPABILITY_QUERY: CapabilityQueryMessage,
    KIND_CAPABILITY_RESPONSE: CapabilityResponseMessage,
    KIND_CONFLICT: ConflictMessage,
}


def parse_message(d: dict[str, Any]) -> CooperationMessage:
    """Dispatch a raw dict to the appropriate typed message class.

    Raises:
        ValueError: if ``kind`` is missing or not a registered cooperation
            message kind. Existing callers can keep using dicts; new code is
            expected to call ``parse_message`` at the boundary.
    """
    if not isinstance(d, dict):
        raise ValueError(f"parse_message expected dict, got {type(d).__name__}")
    kind = d.get("kind")
    if kind is None:
        raise ValueError("cooperation message missing required 'kind' field")
    cls = _KIND_TO_CLASS.get(kind)
    if cls is None:
        raise ValueError(
            f"unknown cooperation message kind: {kind!r} "
            f"(known: {sorted(_KIND_TO_CLASS)})"
        )
    return cls.from_dict(d)


__all__ = [
    "ALL_KINDS",
    "KIND_CAPABILITY_QUERY",
    "KIND_CAPABILITY_RESPONSE",
    "KIND_CONFLICT",
    "KIND_DELEGATE",
    "KIND_RESULT",
    "CapabilityQueryMessage",
    "CapabilityResponseMessage",
    "ConflictMessage",
    "CooperationMessage",
    "DelegateMessage",
    "ResultMessage",
    "parse_message",
]
