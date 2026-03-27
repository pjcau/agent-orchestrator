"""Session-scoped sandbox lifecycle management.

Manages sandbox instances per session with lazy initialization
and automatic cleanup. Each session gets its own isolated workspace
directory. Evicts the oldest idle session once the concurrent limit is
reached (LRU eviction). Tracks port allocations to avoid host-port
collisions between sessions.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from ..core.sandbox import PortMapping, Sandbox, SandboxConfig, SandboxInfo

logger = logging.getLogger(__name__)

# Default maximum number of simultaneously active sandboxes.
_MAX_CONCURRENT = 10

# Default host port range for sandbox port allocation.
_DEFAULT_PORT_RANGE_START = 9000
_DEFAULT_PORT_RANGE_END = 9099


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

    def __init__(
        self,
        default_config: Optional[SandboxConfig] = None,
        max_concurrent: int = _MAX_CONCURRENT,
        port_range_start: int = _DEFAULT_PORT_RANGE_START,
        port_range_end: int = _DEFAULT_PORT_RANGE_END,
    ) -> None:
        self._default_config = default_config or SandboxConfig()
        self._max_concurrent = max_concurrent
        self._port_range_start = port_range_start
        self._port_range_end = port_range_end
        # Maps session_id -> Sandbox
        self._sandboxes: dict[str, Sandbox] = {}
        # Maps session_id -> last-used timestamp (monotonic)
        self._last_used: dict[str, float] = {}
        # Tracks allocated host ports -> session_id
        self._allocated_ports: dict[int, str] = {}

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
            self._release_ports(session_id)
            del self._sandboxes[session_id]
            self._last_used.pop(session_id, None)

        # Enforce concurrent limit via LRU eviction.
        if len(self._sandboxes) >= self._max_concurrent:
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
        self._release_ports(session_id)
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

    async def get_sandbox_info(self, session_id: str) -> SandboxInfo | None:
        """Return runtime info for a session's sandbox, or None if not found."""
        sandbox = self._sandboxes.get(session_id)
        if sandbox is None:
            return None
        return await sandbox.get_info()

    @property
    def active_count(self) -> int:
        """Number of currently tracked sandbox sessions."""
        return len(self._sandboxes)

    @property
    def session_ids(self) -> list[str]:
        """Snapshot of active session IDs."""
        return list(self._sandboxes.keys())

    @property
    def max_concurrent(self) -> int:
        """Configured maximum concurrent sandboxes."""
        return self._max_concurrent

    @property
    def allocated_ports(self) -> dict[int, str]:
        """Snapshot of allocated host ports mapped to session IDs."""
        return dict(self._allocated_ports)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _allocate_ports(self, session_id: str, ports: list[PortMapping]) -> list[PortMapping]:
        """Allocate host ports for the given port mappings.

        For mappings with host_port=0, assigns the next available port from
        the configured range. Mappings with an explicit host_port are kept
        as-is.

        Returns:
            New list of PortMapping with resolved host ports.
        """
        resolved = []
        for pm in ports:
            if pm.host_port != 0:
                # Explicit port — use as-is, track it
                self._allocated_ports[pm.host_port] = session_id
                resolved.append(pm)
            else:
                # Auto-assign from range
                host_port = self._next_available_port()
                if host_port is not None:
                    self._allocated_ports[host_port] = session_id
                    resolved.append(
                        PortMapping(
                            container_port=pm.container_port,
                            host_port=host_port,
                            protocol=pm.protocol,
                        )
                    )
                else:
                    logger.warning(
                        "No available ports in range %d-%d for session %s",
                        self._port_range_start,
                        self._port_range_end,
                        session_id,
                    )
                    # Keep the mapping with host_port=0 — Docker will auto-assign
                    resolved.append(pm)
        return resolved

    def _next_available_port(self) -> int | None:
        """Return the next unallocated port in the configured range."""
        for port in range(self._port_range_start, self._port_range_end + 1):
            if port not in self._allocated_ports:
                return port
        return None

    def _release_ports(self, session_id: str) -> None:
        """Release all host ports allocated to a session."""
        to_remove = [p for p, sid in self._allocated_ports.items() if sid == session_id]
        for port in to_remove:
            del self._allocated_ports[port]

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

        # Allocate host ports for any exposed ports
        exposed_ports = self._allocate_ports(session_id, list(cfg.exposed_ports))

        return SandboxConfig(
            type=cfg.type,
            image=cfg.image,
            timeout_seconds=cfg.timeout_seconds,
            memory_limit=cfg.memory_limit,
            cpu_limit=cfg.cpu_limit,
            network_enabled=cfg.network_enabled,
            writable_paths=writable,
            virtual_path_map=dict(cfg.virtual_path_map),
            exposed_ports=exposed_ports,
            startup_command=cfg.startup_command,
            env_vars=dict(cfg.env_vars),
        )

    async def _evict_oldest(self) -> None:
        """Evict the sandbox with the earliest last-used timestamp."""
        if not self._last_used:
            return
        oldest = min(self._last_used, key=self._last_used.get)  # type: ignore[arg-type]
        logger.debug(
            "Evicting sandbox for session %s (LRU, limit=%d)",
            oldest,
            self._max_concurrent,
        )
        await self.cleanup_session(oldest)
