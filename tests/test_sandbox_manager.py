"""Tests for SandboxManager: session-scoped sandbox lifecycle management."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_orchestrator.core.sandbox import PortMapping, SandboxConfig, SandboxType
from agent_orchestrator.dashboard.sandbox_manager import (
    SandboxManager,
    _DEFAULT_PORT_RANGE_END,
    _DEFAULT_PORT_RANGE_START,
    _MAX_CONCURRENT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _local_config() -> SandboxConfig:
    """Return a LOCAL-mode SandboxConfig for tests (no Docker required)."""
    return SandboxConfig(
        type=SandboxType.LOCAL,
        timeout_seconds=5,
        writable_paths=["/workspace"],
    )


def _make_mock_sandbox(running: bool = False) -> MagicMock:
    """Return a MagicMock that quacks like a Sandbox.

    ``is_running`` is set as a plain bool attribute so truthiness checks
    inside SandboxManager work as expected.
    """
    sb = MagicMock(spec=["is_running", "start", "stop", "execute", "config"])
    type(sb).is_running = property(lambda self: running)
    sb.start = AsyncMock()
    sb.stop = AsyncMock()
    return sb


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestSandboxManagerInit:
    def test_default_config_used_when_none_given(self):
        mgr = SandboxManager()
        assert mgr._default_config is not None
        assert mgr.active_count == 0

    def test_custom_config_stored(self):
        cfg = _local_config()
        mgr = SandboxManager(default_config=cfg)
        assert mgr._default_config is cfg

    def test_starts_with_no_active_sandboxes(self):
        mgr = SandboxManager(default_config=_local_config())
        assert mgr.active_count == 0
        assert mgr.session_ids == []


# ---------------------------------------------------------------------------
# Lazy initialization via get_or_create
# ---------------------------------------------------------------------------


class TestGetOrCreate:
    @pytest.mark.asyncio
    async def test_creates_sandbox_on_first_call(self):
        """First call to get_or_create should start a new sandbox."""
        mgr = SandboxManager(default_config=_local_config())
        sandbox = await mgr.get_or_create("session-1")
        assert sandbox.is_running is True
        assert mgr.active_count == 1
        await mgr.cleanup_all()

    @pytest.mark.asyncio
    async def test_returns_existing_running_sandbox(self):
        """Subsequent calls for the same session return the same sandbox."""
        mgr = SandboxManager(default_config=_local_config())
        sb1 = await mgr.get_or_create("session-1")
        sb2 = await mgr.get_or_create("session-1")
        assert sb1 is sb2
        assert mgr.active_count == 1
        await mgr.cleanup_all()

    @pytest.mark.asyncio
    async def test_recreates_stopped_sandbox(self):
        """If a cached sandbox is no longer running it should be replaced."""
        # Build a sandbox that reports is_running == False.
        stopped = MagicMock()
        stopped.is_running = False
        stopped.stop = AsyncMock()

        # The fresh sandbox that SandboxManager will create after detecting stopped.
        fresh = MagicMock()
        fresh.is_running = True
        fresh.start = AsyncMock()
        fresh.stop = AsyncMock()

        mgr = SandboxManager(default_config=_local_config())

        # Inject the stopped sandbox directly into the manager cache.
        mgr._sandboxes["session-x"] = stopped
        mgr._last_used["session-x"] = 0.0

        # Patch Sandbox() so the re-creation returns `fresh`.
        with patch(
            "agent_orchestrator.dashboard.sandbox_manager.Sandbox",
            return_value=fresh,
        ):
            sb = await mgr.get_or_create("session-x")

        assert sb is fresh
        fresh.start.assert_called_once()
        assert mgr.active_count == 1

    @pytest.mark.asyncio
    async def test_session_isolation(self):
        """Different sessions get different sandbox instances."""
        mgr = SandboxManager(default_config=_local_config())
        sb_a = await mgr.get_or_create("session-a")
        sb_b = await mgr.get_or_create("session-b")
        assert sb_a is not sb_b
        assert mgr.active_count == 2
        await mgr.cleanup_all()

    @pytest.mark.asyncio
    async def test_session_workspace_is_unique(self):
        """Each session receives a distinct /workspace/<session_id> path."""
        cfg = _local_config()
        mgr = SandboxManager(default_config=cfg)
        # Verify the config builder injects the session path.
        config_a = mgr._session_config("abc")
        config_b = mgr._session_config("xyz")
        assert "/workspace/abc" in config_a.writable_paths
        assert "/workspace/xyz" in config_b.writable_paths
        assert config_a.writable_paths != config_b.writable_paths


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_session_stops_sandbox(self):
        """cleanup_session should stop the sandbox and remove it from tracking."""
        mgr = SandboxManager(default_config=_local_config())
        sandbox = await mgr.get_or_create("session-clean")
        assert mgr.active_count == 1

        await mgr.cleanup_session("session-clean")
        assert sandbox.is_running is False
        assert mgr.active_count == 0

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent_session_is_safe(self):
        """cleanup_session on an unknown session_id must not raise."""
        mgr = SandboxManager(default_config=_local_config())
        await mgr.cleanup_session("does-not-exist")  # Should not raise
        assert mgr.active_count == 0

    @pytest.mark.asyncio
    async def test_cleanup_all_stops_all_sandboxes(self):
        """cleanup_all should stop every active sandbox."""
        mgr = SandboxManager(default_config=_local_config())
        await mgr.get_or_create("s1")
        await mgr.get_or_create("s2")
        await mgr.get_or_create("s3")
        assert mgr.active_count == 3

        await mgr.cleanup_all()
        assert mgr.active_count == 0

    @pytest.mark.asyncio
    async def test_cleanup_all_idempotent(self):
        """Calling cleanup_all twice should not raise."""
        mgr = SandboxManager(default_config=_local_config())
        await mgr.get_or_create("s1")
        await mgr.cleanup_all()
        await mgr.cleanup_all()  # Should not raise
        assert mgr.active_count == 0


# ---------------------------------------------------------------------------
# LRU eviction
# ---------------------------------------------------------------------------


class TestLRUEviction:
    @pytest.mark.asyncio
    async def test_evicts_oldest_when_limit_reached(self):
        """When _MAX_CONCURRENT is reached, the oldest idle session is evicted."""
        mgr = SandboxManager(default_config=_local_config())

        # Create _MAX_CONCURRENT sandboxes
        for i in range(_MAX_CONCURRENT):
            await mgr.get_or_create(f"session-{i}")
        assert mgr.active_count == _MAX_CONCURRENT

        # The very first one has the oldest last-used timestamp.
        oldest_id = "session-0"
        assert oldest_id in mgr.session_ids

        # Creating one more sandbox should evict the oldest.
        await mgr.get_or_create("session-new")
        assert mgr.active_count == _MAX_CONCURRENT
        assert oldest_id not in mgr.session_ids
        assert "session-new" in mgr.session_ids

        await mgr.cleanup_all()


# ---------------------------------------------------------------------------
# SANDBOX_ENABLED environment variable
# ---------------------------------------------------------------------------


class TestSandboxEnabled:
    def test_sandbox_manager_not_created_when_disabled(self, monkeypatch):
        """When SANDBOX_ENABLED=false (default), SandboxManager stays None in app."""
        monkeypatch.setenv("SANDBOX_ENABLED", "false")
        enabled = os.environ.get("SANDBOX_ENABLED", "false").lower() == "true"
        manager = SandboxManager() if enabled else None
        assert manager is None

    def test_sandbox_manager_created_when_enabled(self, monkeypatch):
        """When SANDBOX_ENABLED=true, SandboxManager is instantiated."""
        monkeypatch.setenv("SANDBOX_ENABLED", "true")
        enabled = os.environ.get("SANDBOX_ENABLED", "false").lower() == "true"
        manager = SandboxManager() if enabled else None
        assert manager is not None


# ---------------------------------------------------------------------------
# run_agent with sandbox parameter
# ---------------------------------------------------------------------------


class TestRunAgentWithSandbox:
    @pytest.mark.asyncio
    async def test_run_agent_accepts_sandbox_parameter(self):
        """run_agent accepts a sandbox kwarg without error (signature test)."""
        import inspect
        from agent_orchestrator.dashboard.agent_runner import run_agent

        sig = inspect.signature(run_agent)
        assert "sandbox" in sig.parameters

    @pytest.mark.asyncio
    async def test_create_skill_registry_registers_sandboxed_shell(self):
        """Sandboxed shell skill is present in registry when sandbox is passed."""
        from agent_orchestrator.core.sandbox import Sandbox
        from agent_orchestrator.dashboard.agent_runner import create_skill_registry

        async with Sandbox(SandboxConfig(type=SandboxType.LOCAL)) as sandbox:
            registry = create_skill_registry(sandbox=sandbox)
            assert "sandboxed_shell" in registry.list_skills()

    @pytest.mark.asyncio
    async def test_create_skill_registry_no_sandbox_excludes_sandboxed_shell(self):
        """Without sandbox, sandboxed_shell should not appear in the registry."""
        from agent_orchestrator.dashboard.agent_runner import create_skill_registry

        registry = create_skill_registry()
        assert "sandboxed_shell" not in registry.list_skills()


# ---------------------------------------------------------------------------
# run_team with sandbox_manager parameter
# ---------------------------------------------------------------------------


class TestRunTeamWithSandboxManager:
    @pytest.mark.asyncio
    async def test_run_team_accepts_sandbox_manager_parameter(self):
        """run_team signature includes sandbox_manager."""
        import inspect
        from agent_orchestrator.dashboard.agent_runner import run_team

        sig = inspect.signature(run_team)
        assert "sandbox_manager" in sig.parameters


# ---------------------------------------------------------------------------
# Configurable max_concurrent
# ---------------------------------------------------------------------------


class TestConfigurableMaxConcurrent:
    def test_default_max_concurrent(self):
        mgr = SandboxManager(default_config=_local_config())
        assert mgr.max_concurrent == _MAX_CONCURRENT

    def test_custom_max_concurrent(self):
        mgr = SandboxManager(default_config=_local_config(), max_concurrent=5)
        assert mgr.max_concurrent == 5

    @pytest.mark.asyncio
    async def test_custom_limit_enforced(self):
        """Eviction triggers at the custom limit, not the default."""
        mgr = SandboxManager(default_config=_local_config(), max_concurrent=3)
        for i in range(3):
            await mgr.get_or_create(f"s-{i}")
        assert mgr.active_count == 3

        # Creating a 4th should evict the oldest
        await mgr.get_or_create("s-new")
        assert mgr.active_count == 3
        assert "s-0" not in mgr.session_ids
        assert "s-new" in mgr.session_ids
        await mgr.cleanup_all()


# ---------------------------------------------------------------------------
# Port allocation
# ---------------------------------------------------------------------------


class TestPortAllocation:
    def test_allocate_explicit_port(self):
        mgr = SandboxManager(default_config=_local_config())
        ports = [PortMapping(container_port=8000, host_port=9050)]
        resolved = mgr._allocate_ports("session-1", ports)
        assert resolved[0].host_port == 9050
        assert 9050 in mgr.allocated_ports
        assert mgr.allocated_ports[9050] == "session-1"

    def test_allocate_auto_port(self):
        mgr = SandboxManager(
            default_config=_local_config(),
            port_range_start=9000,
            port_range_end=9099,
        )
        ports = [PortMapping(container_port=8000)]
        resolved = mgr._allocate_ports("session-1", ports)
        assert resolved[0].host_port == 9000
        assert 9000 in mgr.allocated_ports

    def test_auto_ports_no_collision(self):
        mgr = SandboxManager(
            default_config=_local_config(),
            port_range_start=9000,
            port_range_end=9099,
        )
        mgr._allocate_ports("s1", [PortMapping(container_port=8000)])
        mgr._allocate_ports("s2", [PortMapping(container_port=8000)])
        assert mgr.allocated_ports[9000] == "s1"
        assert mgr.allocated_ports[9001] == "s2"

    def test_release_ports_on_cleanup(self):
        mgr = SandboxManager(default_config=_local_config())
        mgr._allocate_ports("session-1", [PortMapping(container_port=8000, host_port=9050)])
        assert len(mgr.allocated_ports) == 1
        mgr._release_ports("session-1")
        assert len(mgr.allocated_ports) == 0

    def test_release_only_session_ports(self):
        mgr = SandboxManager(default_config=_local_config())
        mgr._allocate_ports("s1", [PortMapping(container_port=8000, host_port=9001)])
        mgr._allocate_ports("s2", [PortMapping(container_port=8000, host_port=9002)])
        mgr._release_ports("s1")
        assert 9001 not in mgr.allocated_ports
        assert 9002 in mgr.allocated_ports

    def test_exhausted_port_range(self):
        """When all ports are taken, PortMapping keeps host_port=0."""
        mgr = SandboxManager(
            default_config=_local_config(),
            port_range_start=9000,
            port_range_end=9001,
        )
        mgr._allocate_ports("s1", [PortMapping(container_port=8000)])
        mgr._allocate_ports("s2", [PortMapping(container_port=8000)])
        # Range exhausted (9000, 9001 taken)
        resolved = mgr._allocate_ports("s3", [PortMapping(container_port=8000)])
        assert resolved[0].host_port == 0  # Fallback to Docker auto-assign

    def test_port_range_defaults(self):
        assert _DEFAULT_PORT_RANGE_START == 9000
        assert _DEFAULT_PORT_RANGE_END == 9099


# ---------------------------------------------------------------------------
# get_sandbox_info
# ---------------------------------------------------------------------------


class TestGetSandboxInfo:
    @pytest.mark.asyncio
    async def test_info_for_existing_session(self):
        mgr = SandboxManager(default_config=_local_config())
        await mgr.get_or_create("session-info")
        info = await mgr.get_sandbox_info("session-info")
        assert info is not None
        assert info.status == "running"
        await mgr.cleanup_all()

    @pytest.mark.asyncio
    async def test_info_for_nonexistent_session(self):
        mgr = SandboxManager(default_config=_local_config())
        info = await mgr.get_sandbox_info("no-such-session")
        assert info is None


# ---------------------------------------------------------------------------
# Session config propagates new fields
# ---------------------------------------------------------------------------


class TestSessionConfigNewFields:
    def test_exposed_ports_propagated(self):
        cfg = SandboxConfig(
            type=SandboxType.LOCAL,
            exposed_ports=[PortMapping(container_port=3000)],
        )
        mgr = SandboxManager(default_config=cfg)
        session_cfg = mgr._session_config("test-session")
        assert len(session_cfg.exposed_ports) == 1
        # Port should be allocated from range
        assert session_cfg.exposed_ports[0].host_port >= 9000

    def test_startup_command_propagated(self):
        cfg = SandboxConfig(
            type=SandboxType.LOCAL,
            startup_command="pip install flask",
        )
        mgr = SandboxManager(default_config=cfg)
        session_cfg = mgr._session_config("test-session")
        assert session_cfg.startup_command == "pip install flask"

    def test_env_vars_propagated(self):
        cfg = SandboxConfig(
            type=SandboxType.LOCAL,
            env_vars={"NODE_ENV": "dev"},
        )
        mgr = SandboxManager(default_config=cfg)
        session_cfg = mgr._session_config("test-session")
        assert session_cfg.env_vars == {"NODE_ENV": "dev"}

    def test_env_vars_are_copied(self):
        """Env vars should be a copy, not a shared reference."""
        cfg = SandboxConfig(type=SandboxType.LOCAL, env_vars={"A": "1"})
        mgr = SandboxManager(default_config=cfg)
        cfg_a = mgr._session_config("a")
        cfg_b = mgr._session_config("b")
        cfg_a.env_vars["B"] = "2"
        assert "B" not in cfg_b.env_vars

    @pytest.mark.asyncio
    async def test_ports_released_on_session_cleanup(self):
        cfg = SandboxConfig(
            type=SandboxType.LOCAL,
            exposed_ports=[PortMapping(container_port=8000)],
        )
        mgr = SandboxManager(default_config=cfg)
        await mgr.get_or_create("port-session")
        assert len(mgr.allocated_ports) > 0
        await mgr.cleanup_session("port-session")
        assert len(mgr.allocated_ports) == 0
