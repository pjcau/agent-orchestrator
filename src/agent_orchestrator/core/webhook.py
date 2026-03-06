"""Webhook triggers — start graphs from external events."""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WebhookConfig:
    webhook_id: str
    name: str
    path: str  # URL path, e.g. "/hooks/deploy"
    secret: str | None = None  # for HMAC signature verification
    graph_type: str = "auto"  # which graph to trigger
    agent_name: str | None = None
    enabled: bool = True
    created_at: float = field(default_factory=time.time)


@dataclass
class WebhookEvent:
    webhook_id: str
    payload: dict[str, Any]
    headers: dict[str, str]
    received_at: float = field(default_factory=time.time)
    processed: bool = False
    result: str | None = None


class WebhookRegistry:
    """Register and dispatch incoming webhook events."""

    def __init__(self) -> None:
        self._webhooks: dict[str, WebhookConfig] = {}
        self._events: list[WebhookEvent] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, config: WebhookConfig) -> None:
        """Register a webhook endpoint."""
        self._webhooks[config.webhook_id] = config

    def unregister(self, webhook_id: str) -> bool:
        """Remove a webhook registration. Returns True if it existed."""
        if webhook_id in self._webhooks:
            del self._webhooks[webhook_id]
            return True
        return False

    def get(self, webhook_id: str) -> WebhookConfig | None:
        """Retrieve a webhook config by ID."""
        return self._webhooks.get(webhook_id)

    def get_by_path(self, path: str) -> WebhookConfig | None:
        """Find a webhook config by its URL path."""
        for config in self._webhooks.values():
            if config.path == path:
                return config
        return None

    def list_webhooks(self) -> list[WebhookConfig]:
        """Return all registered webhook configs."""
        return list(self._webhooks.values())

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def receive(self, webhook_id: str, payload: dict, headers: dict) -> WebhookEvent:
        """Record an incoming webhook event and return it."""
        event = WebhookEvent(
            webhook_id=webhook_id,
            payload=payload,
            headers=headers,
        )
        self._events.append(event)
        return event

    def validate_signature(
        self, webhook_id: str, payload_bytes: bytes, signature: str
    ) -> bool:
        """Validate HMAC-SHA256 signature if a secret is configured.

        Returns True when:
        - the webhook has no secret (no verification required), or
        - the computed digest matches ``signature`` (constant-time compare).
        Returns False when:
        - the webhook does not exist, or
        - the secret is set and the signature does not match.
        """
        config = self._webhooks.get(webhook_id)
        if config is None:
            return False
        if config.secret is None:
            return True
        expected = hmac.new(
            config.secret.encode(), payload_bytes, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def get_events(
        self,
        webhook_id: str | None = None,
        processed: bool | None = None,
    ) -> list[WebhookEvent]:
        """Return events, optionally filtered by webhook ID and/or processed flag."""
        events = list(self._events)
        if webhook_id is not None:
            events = [e for e in events if e.webhook_id == webhook_id]
        if processed is not None:
            events = [e for e in events if e.processed == processed]
        return events

    def mark_processed(self, event_index: int, result: str) -> None:
        """Mark an event as processed and record the result string."""
        self._events[event_index].processed = True
        self._events[event_index].result = result
