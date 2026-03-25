"""Session-scoped sandbox lifecycle management.

Manages sandbox instances per session with lazy initialization
and automatic cleanup. Each session gets its own isolated workspace
directory. Evicts the oldest idle session once the concurrent limit is
reached (LRU eviction).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from ..core.sandbox import Sandbox, SandboxConfig

logger = logging.getLogger(__name__)

# Maximum number of simultaneously active sandboxes.
_MAX_CONCURRENT = 10


class SandboxManager:
    """Lifecycle manager for per-session Sandbox instances.

    Usage::

        config = SandboxConfig(type=SandboxType.LOCAL, timeout_seconds=30)
        manager = SandboxManager(default_config=config)

        sandbox = await manager.get_or_create("session-abc")
        result  = await sandbox.execute("echo hello")

        await manager.cleanup_session("session-abc")
        await manager.cleanup_all()  # on shutdown
    """

    def __init__(self, default_config: Optional[SandboxConfig] = None) -> None:
        self._default_config = default_config or SandboxConfig()
        # Maps session_id -> Sandbox
        self._sandboxes: dict[str, Sandbox] = {}
        # Maps session_id -> last-used timestamp (monotonic)
        self._last_used: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_or_create(self, session_id: str) -> Sandbox:
        """Return the sandbox for *session_id*, creating it if necessary.

        If the sandbox already exists and is running it is returned immediately.
        When the concurrent limit is reached the oldest idle session is evicted
        before a new one is started.

        Args:
            session_id: Unique identifier for the session (e.g. job logger ID).

        Returns:
            A started Sandbox instance.
        """
        if session_id in self._sandboxes:
            sandbox = self._sandboxes[session_id]
            if sandbox.is_running:
                self._last_used[session_id] = time.monotonic()
                return sandbox
            # Sandbox exists but stopped — remove so it can be re-created.
            del self._sandboxes[session_id]
            self._last_used.pop(session_id, None)

        # Enforce concurrent limit via LRU eviction.
        if len(self._sandboxes) >= _MAX_CONCURRENT:
            await self._evict_oldest()

        # Build a per-session config with its own workspace directory.
        config = self._session_config(session_id)
        sandbox = Sandbox(config)
        await sandbox.start()

        self._sandboxes[session_id] = sandbox
        self._last_used[session_id] = time.monotonic()
        logger.debug("Sandbox created for session %s", session_id)
        return sandbox

    async def cleanup_session(self, session_id: str) -> None:
        """Stop and remove the sandbox for *session_id*.

        Safe to call even if no sandbox exists for the session.

        Args:
            session_id: Session whose sandbox should be torn down.
        """
        sandbox = self._sandboxes.pop(session_id, None)
        self._last_used.pop(session_id, None)
        if sandbox is not None:
            try:
                await sandbox.stop()
                logger.debug("Sandbox cleaned up for session %s", session_id)
            except Exception:
                logger.warning("Error stopping sandbox for session %s", session_id, exc_info=True)

    async def cleanup_all(self) -> None:
        """Stop all active sandboxes.

        Intended to be called during application shutdown so that
        Docker containers are not left running.
        """
        session_ids = list(self._sandboxes.keys())
        for session_id in session_ids:
            await self.cleanup_session(session_id)

    @property
    def active_count(self) -> int:
        """Number of currently tracked sandbox sessions."""
        return len(self._sandboxes)

    @property
    def session_ids(self) -> list[str]:
        """Snapshot of active session IDs."""
        return list(self._sandboxes.keys())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session_config(self, session_id: str) -> SandboxConfig:
        """Build a SandboxConfig with a per-session workspace path."""
        cfg = self._default_config
        workspace = f"/workspace/{session_id}"
        # Build writable_paths: replace the generic /workspace with the
        # session-specific path while preserving any extra paths from the
        # default config.
        writable = [workspace]
        for path in cfg.writable_paths:
            if path != "/workspace" and path not in writable:
                writable.append(path)

        return SandboxConfig(
            type=cfg.type,
            image=cfg.image,
            timeout_seconds=cfg.timeout_seconds,
            memory_limit=cfg.memory_limit,
            cpu_limit=cfg.cpu_limit,
            network_enabled=cfg.network_enabled,
            writable_paths=writable,
            virtual_path_map=dict(cfg.virtual_path_map),
        )

    async def _evict_oldest(self) -> None:
        """Evict the sandbox with the earliest last-used timestamp."""
        if not self._last_used:
            return
        oldest = min(self._last_used, key=self._last_used.get)  # type: ignore[arg-type]
        logger.debug("Evicting sandbox for session %s (LRU, limit=%d)", oldest, _MAX_CONCURRENT)
        await self.cleanup_session(oldest)
