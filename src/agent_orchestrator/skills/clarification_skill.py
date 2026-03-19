"""Clarification skill — agents use this to request human clarification.

Emits clarification events via the EventBus and optionally blocks until
a response is received (or timeout expires).
"""

from __future__ import annotations

import logging

from ..core.clarification import (
    ClarificationManager,
    ClarificationRequest,
    ClarificationType,
)
from ..core.skill import Skill, SkillResult

logger = logging.getLogger(__name__)


class ClarificationSkill(Skill):
    """Skill that allows agents to ask for human clarification.

    When invoked, it creates a ClarificationRequest, emits it via the
    EventBus, and (if blocking) waits for a response before returning.
    On timeout, returns a fallback message so the agent can proceed with
    its best guess.
    """

    def __init__(
        self,
        manager: ClarificationManager | None = None,
        event_bus: object | None = None,
        emit_callback: object | None = None,
    ) -> None:
        self._manager = manager or ClarificationManager()
        self._event_bus = event_bus  # EventBus instance (optional)
        self._emit_callback = emit_callback  # async callable(event_type: str, data: dict)

    @property
    def name(self) -> str:
        return "ask_clarification"

    @property
    def description(self) -> str:
        return (
            "Ask the human for clarification before proceeding. "
            "Supports blocking (pauses until response) and non-blocking modes."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": [t.value for t in ClarificationType],
                    "description": "Category of clarification needed.",
                },
                "question": {
                    "type": "string",
                    "description": "The question to ask the human.",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional suggested answers.",
                },
                "context": {
                    "type": "string",
                    "description": "Background context for the question.",
                },
                "blocking": {
                    "type": "boolean",
                    "description": "Whether to pause until a response is received.",
                    "default": True,
                },
                "timeout_seconds": {
                    "type": "number",
                    "description": "Timeout in seconds for blocking requests (default 300).",
                },
            },
            "required": ["type", "question"],
        }

    @property
    def manager(self) -> ClarificationManager:
        """Expose the manager for external response submission."""
        return self._manager

    async def execute(self, params: dict) -> SkillResult:
        """Create a clarification request, emit event, and optionally wait."""
        try:
            ctype = ClarificationType(params["type"])
        except (KeyError, ValueError) as exc:
            return SkillResult(
                success=False,
                output=None,
                error=f"Invalid clarification type: {exc}",
            )

        question = params.get("question", "")
        if not question:
            return SkillResult(
                success=False,
                output=None,
                error="Question is required.",
            )

        kwargs: dict = {
            "type": ctype,
            "question": question,
            "options": params.get("options"),
            "context": params.get("context"),
            "blocking": params.get("blocking", True),
        }
        if "timeout_seconds" in params:
            kwargs["timeout_seconds"] = float(params["timeout_seconds"])

        request = ClarificationRequest(**kwargs)

        # Register with manager
        self._manager.register(request)

        # Emit event via EventBus if available
        await self._emit_request_event(request)

        if not request.blocking:
            # Non-blocking: return immediately
            return SkillResult(
                success=True,
                output={
                    "request_id": request.request_id,
                    "status": "emitted",
                    "blocking": False,
                    "message": f"Non-blocking clarification emitted: {question}",
                },
            )

        # Blocking: wait for response (with timeout)
        response = await self._manager.wait_for_response(request)

        if response is None:
            # Timeout — emit timeout event and return fallback
            await self._emit_timeout_event(request)
            return SkillResult(
                success=True,
                output={
                    "request_id": request.request_id,
                    "status": "timeout",
                    "blocking": True,
                    "message": (
                        f"Clarification timed out after {request.timeout_seconds}s. "
                        "Proceeding with best-guess approach."
                    ),
                },
            )

        # Got a response
        return SkillResult(
            success=True,
            output={
                "request_id": request.request_id,
                "status": "answered",
                "answer": response.answer,
            },
        )

    async def _emit_request_event(self, request: ClarificationRequest) -> None:
        """Emit a clarification.request event via callback."""
        callback = self._emit_callback
        if callback is None:
            return

        try:
            await callback("clarification.request", request.to_dict())
        except Exception:
            logger.debug("Could not emit clarification request event", exc_info=True)

    async def _emit_timeout_event(self, request: ClarificationRequest) -> None:
        """Emit a clarification.timeout event via callback."""
        callback = self._emit_callback
        if callback is None:
            return

        try:
            await callback(
                "clarification.timeout",
                {
                    "request_id": request.request_id,
                    "question": request.question,
                    "timeout_seconds": request.timeout_seconds,
                },
            )
        except Exception:
            logger.debug("Could not emit clarification timeout event", exc_info=True)
