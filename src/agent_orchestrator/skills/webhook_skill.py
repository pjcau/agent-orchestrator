"""Webhook trigger skill — agents use this to send outgoing webhook notifications."""

from __future__ import annotations

import time

from ..core.skill import Skill, SkillResult


class WebhookSkill(Skill):
    """Send outgoing webhook notifications.

    HTTP calls are not made directly (no aiohttp/requests dependency).
    Instead the intent is recorded in memory so it can be audited or
    dispatched later by a real HTTP transport layer.
    """

    def __init__(self) -> None:
        # Store sent webhooks in memory (for audit/testing).
        self._sent: list[dict] = []

    @property
    def name(self) -> str:
        return "webhook_send"

    @property
    def description(self) -> str:
        return "Send outgoing webhook notifications"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "method": {
                    "type": "string",
                    "enum": ["POST", "PUT", "PATCH"],
                    "default": "POST",
                },
                "payload": {"type": "object"},
                "headers": {"type": "object"},
            },
            "required": ["url", "payload"],
        }

    async def execute(self, params: dict) -> SkillResult:
        record = {
            "url": params["url"],
            "method": params.get("method", "POST"),
            "payload": params["payload"],
            "headers": params.get("headers", {}),
            "timestamp": time.time(),
            "status": "queued",  # would be "sent" after real HTTP dispatch
        }
        self._sent.append(record)
        return SkillResult(success=True, output=record)

    def get_sent(self) -> list[dict]:
        """Return all queued/sent webhook records."""
        return list(self._sent)
