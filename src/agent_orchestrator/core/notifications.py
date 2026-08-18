"""Unified notification dispatcher (scout findings #56/#70, Shoutrrr-inspired).

One ``NotificationDispatcher.notify()`` call fans a message out to every
registered backend (log, webhook, callable, or app-layer integrations such as
Slack/Telegram, which register their own backends from outside the harness).
Backend failures are isolated: one broken channel never blocks the others.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class NotificationLevel(IntEnum):
    """Severity — backends can subscribe to a minimum level."""

    INFO = 0
    WARNING = 1
    CRITICAL = 2


@dataclass
class Notification:
    """One message to fan out."""

    title: str
    body: str
    level: NotificationLevel = NotificationLevel.INFO
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class NotificationBackend(ABC):
    """A delivery channel. Implementations must not raise on delivery failure
    when they can report it — return False instead; raised exceptions are
    caught and counted as failures by the dispatcher anyway."""

    @abstractmethod
    async def send(self, notification: Notification) -> bool: ...


class LogBackend(NotificationBackend):
    """Deliver to the Python logging system — the always-available default."""

    _LEVELS = {
        NotificationLevel.INFO: logging.INFO,
        NotificationLevel.WARNING: logging.WARNING,
        NotificationLevel.CRITICAL: logging.CRITICAL,
    }

    async def send(self, notification: Notification) -> bool:
        logger.log(
            self._LEVELS[notification.level],
            "[notify] %s — %s",
            notification.title,
            notification.body,
        )
        return True


class CallableBackend(NotificationBackend):
    """Adapt any async callable into a backend — the extension point the app
    layer uses to plug Slack/Telegram bots in without the harness importing
    them (import-boundary safe)."""

    def __init__(self, fn: Callable[[Notification], Awaitable[bool]]) -> None:
        self._fn = fn

    async def send(self, notification: Notification) -> bool:
        return bool(await self._fn(notification))


class WebhookBackend(NotificationBackend):
    """POST the notification as JSON to a fixed webhook URL.

    The URL is operator configuration, never model/user input — do not build
    it from untrusted data (SSRF). Requires ``httpx`` (dashboard extra).
    """

    def __init__(self, url: str, timeout_seconds: float = 10.0) -> None:
        if not url.startswith(("https://", "http://")):
            raise ValueError("Webhook URL must be http(s)")
        self._url = url
        self._timeout = timeout_seconds

    async def send(self, notification: Notification) -> bool:
        try:
            import httpx
        except ImportError:
            logger.warning("WebhookBackend needs httpx — install the dashboard extra")
            return False
        payload = {
            "title": notification.title,
            "body": notification.body,
            "level": notification.level.name.lower(),
            "metadata": notification.metadata,
            "timestamp": notification.timestamp,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, json=payload)
            return response.status_code < 300
        except Exception as exc:
            logger.warning("Webhook notification failed: %s", exc)
            return False


class NotificationDispatcher:
    """Registry of named backends with per-backend minimum levels.

    Usage::

        dispatcher = NotificationDispatcher()
        dispatcher.register("log", LogBackend())
        dispatcher.register("ops", WebhookBackend(url), min_level=NotificationLevel.WARNING)
        results = await dispatcher.notify(
            Notification("deploy failed", "…", NotificationLevel.CRITICAL)
        )
        # results == {"log": True, "ops": True}
    """

    def __init__(self) -> None:
        self._backends: dict[str, tuple[NotificationBackend, NotificationLevel]] = {}

    def register(
        self,
        name: str,
        backend: NotificationBackend,
        min_level: NotificationLevel = NotificationLevel.INFO,
    ) -> None:
        self._backends[name] = (backend, min_level)

    def unregister(self, name: str) -> bool:
        return self._backends.pop(name, None) is not None

    @property
    def backend_names(self) -> list[str]:
        return list(self._backends)

    async def notify(
        self,
        notification: Notification,
        backends: list[str] | None = None,
    ) -> dict[str, bool]:
        """Fan the notification out; returns delivery success per backend.

        ``backends`` restricts delivery to the named subset. Backends whose
        ``min_level`` exceeds the notification's level are skipped (absent
        from the result). Failures (False or exception) never propagate.
        """
        selected = [
            (name, backend)
            for name, (backend, min_level) in self._backends.items()
            if (backends is None or name in backends) and notification.level >= min_level
        ]
        if not selected:
            return {}

        async def _send(backend: NotificationBackend) -> bool:
            try:
                return await backend.send(notification)
            except Exception as exc:
                logger.warning("Notification backend failed: %s", exc)
                return False

        outcomes = await asyncio.gather(*(_send(b) for _, b in selected))
        return {name: ok for (name, _), ok in zip(selected, outcomes)}
