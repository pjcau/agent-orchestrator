"""Structured clarification system for agent-user communication.

Provides typed clarification requests that agents can emit when they need
human input before proceeding. Supports blocking (pause agent) and
non-blocking (continue with best guess) modes, with configurable timeout.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# Default timeout for blocking clarification requests (seconds).
DEFAULT_CLARIFICATION_TIMEOUT = 300.0  # 5 minutes


class ClarificationType(Enum):
    """Category of clarification being requested."""

    MISSING_INFO = "missing_info"
    AMBIGUOUS = "ambiguous"
    APPROACH = "approach"
    RISK = "risk"
    SUGGESTION = "suggestion"


@dataclass
class ClarificationRequest:
    """A structured request for human clarification.

    Attributes:
        type: The category of clarification needed.
        question: The question to ask the human.
        options: Optional list of suggested answers.
        context: Optional background context for the question.
        blocking: If True, the agent pauses until a response is received.
        request_id: Unique identifier (auto-generated).
        timestamp: When the request was created.
        timeout_seconds: How long to wait before falling back (blocking only).
    """

    type: ClarificationType
    question: str
    options: list[str] | None = None
    context: str | None = None
    blocking: bool = True
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)
    timeout_seconds: float = DEFAULT_CLARIFICATION_TIMEOUT

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary for event emission."""
        return {
            "request_id": self.request_id,
            "type": self.type.value,
            "question": self.question,
            "options": self.options,
            "context": self.context,
            "blocking": self.blocking,
            "timestamp": self.timestamp,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass
class ClarificationResponse:
    """A human response to a clarification request.

    Attributes:
        answer: The human's answer text.
        request_id: The ID of the request this responds to.
        timestamp: When the response was created.
    """

    answer: str
    request_id: str
    timestamp: float = field(default_factory=time.time)


class ClarificationManager:
    """Manages pending clarification requests and their responses.

    Holds asyncio.Event objects so that agents can await a response.
    When a response arrives, the corresponding event is set and the
    agent resumes execution.
    """

    def __init__(self) -> None:
        self._pending: dict[str, ClarificationRequest] = {}
        self._responses: dict[str, ClarificationResponse] = {}
        self._events: dict[str, asyncio.Event] = {}

    def register(self, request: ClarificationRequest) -> asyncio.Event:
        """Register a clarification request and return an event to await."""
        self._pending[request.request_id] = request
        event = asyncio.Event()
        self._events[request.request_id] = event
        return event

    def respond(self, response: ClarificationResponse) -> bool:
        """Submit a response to a pending clarification request.

        Returns True if the request was found and answered, False otherwise.
        """
        if response.request_id not in self._pending:
            logger.warning(
                "Clarification response for unknown request: %s",
                response.request_id,
            )
            return False

        self._responses[response.request_id] = response
        event = self._events.get(response.request_id)
        if event:
            event.set()
        return True

    def get_response(self, request_id: str) -> ClarificationResponse | None:
        """Retrieve the response for a given request ID."""
        return self._responses.get(request_id)

    def get_pending(self) -> list[ClarificationRequest]:
        """Return all pending (unanswered) clarification requests."""
        return [req for rid, req in self._pending.items() if rid not in self._responses]

    def cleanup(self, request_id: str) -> None:
        """Remove a request and its response from tracking."""
        self._pending.pop(request_id, None)
        self._responses.pop(request_id, None)
        self._events.pop(request_id, None)

    async def wait_for_response(
        self,
        request: ClarificationRequest,
    ) -> ClarificationResponse | None:
        """Wait for a response to the given request, with timeout.

        Returns the response if received within the timeout, or None if
        the timeout expired.
        """
        event = self._events.get(request.request_id)
        if event is None:
            event = self.register(request)

        try:
            await asyncio.wait_for(event.wait(), timeout=request.timeout_seconds)
            return self._responses.get(request.request_id)
        except asyncio.TimeoutError:
            logger.warning(
                "Clarification request %s timed out after %.0fs: %s",
                request.request_id,
                request.timeout_seconds,
                request.question,
            )
            return None
