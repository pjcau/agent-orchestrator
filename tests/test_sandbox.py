"""Tests for Docker sandbox and sandboxed shell skill."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.core.sandbox import (
    Sandbox,
    SandboxConfig,
    SandboxError,
    SandboxResult,
    SandboxType,
    _validate_path,
)
from agent_orchestrator.skills.sandboxed_shell import SandboxedShellSkill


# ─── SandboxConfig defaults ─────────────────────────────────────────


class TestSandboxConfig:
    def test_defaults(self):
        cfg = SandboxConfig()
        assert cfg.type == SandboxType.DOCKER
        assert cfg.image == "python:3.12-slim"
        assert cfg.timeout_seconds == 60
        assert cfg.memory_limit == "512m"
        assert cfg.cpu_limit == 1.0
        assert cfg.network_enabled is False
        assert cfg.writable_paths == ["/workspace"]
        assert cfg.virtual_path_map == {}

    def test_custom_config(self):
        cfg = SandboxConfig(
            type=SandboxType.LOCAL,
            image="node:20",
            timeout_seconds=30,
            memory_limit="1g",
            cpu_limit=2.0,
            network_enabled=True,
            writable_paths=["/tmp", "/data"],
            virtual_path_map={"/project": "/workspace/project"},
        )
        assert cfg.type == SandboxType.LOCAL
        assert cfg.memory_limit == "1g"
        assert len(cfg.writable_paths) == 2


# ─── SandboxResult ──────────────────────────────────────────────────


class TestSandboxResult:
    def test_fields(self):
        r = SandboxResult(
            stdout="hello\n",
            stderr="",
            exit_code=0,
            timed_out=False,
            duration_seconds=0.123,
        )
        assert r.stdout == "hello\n"
        assert r.exit_code == 0
        assert r.timed_out is False
        assert r.duration_seconds == 0.123

    def test_timeout_result(self):
        r = SandboxResult(
            stdout="",
            stderr="Command timed out",
            exit_code=-1,
            timed_out=True,
            duration_seconds=60.0,
        )
        assert r.timed_out is True
        assert r.exit_code == -1


# ─── Path Validation ────────────────────────────────────────────────


class TestPathValidation:
    def test_valid_path(self):
        result = _validate_path("/workspace/test.py", ["/workspace"])
        assert result == "/workspace/test.py"

    def test_traversal_blocked(self):
        with pytest.raises(SandboxError, match="Path traversal detected"):
            _validate_path("/workspace/../etc/passwd", ["/workspace"])

    def test_outside_allowed_roots(self):
        with pytest.raises(SandboxError, match="outside allowed roots"):
            _validate_path("/etc/passwd", ["/workspace"])

    def test_multiple_allowed_roots(self):
        result = _validate_path("/tmp/data.csv", ["/workspace", "/tmp"])
        assert result == "/tmp/data.csv"

    def test_nested_traversal_blocked(self):
        with pytest.raises(SandboxError, match="Path traversal detected"):
            _validate_path("/workspace/foo/../../etc/shadow", ["/workspace"])

    def test_empty_allowed_roots(self):
        # No roots = no restriction (but traversal still blocked)
        result = _validate_path("/any/path/file.py", [])
        assert result == "/any/path/file.py"

    def test_traversal_with_empty_roots(self):
        with pytest.raises(SandboxError, match="Path traversal detected"):
            _validate_path("/any/../escape", [])


# ─── Virtual Path Mapping ───────────────────────────────────────────


class TestVirtualPathMapping:
    def test_mapping_applied(self):
        cfg = SandboxConfig(
            type=SandboxType.LOCAL,
            writable_paths=["/workspace"],
            virtual_path_map={"/project": "/workspace/project"},
        )
        sandbox = Sandbox(cfg)
        result = sandbox.map_virtual_path("/project/src/main.py")
        assert result == "/workspace/project/src/main.py"

    def test_no_mapping_match(self):
        cfg = SandboxConfig(
            type=SandboxType.LOCAL,
            writable_paths=["/workspace"],
            virtual_path_map={"/project": "/workspace/project"},
        )
        sandbox = Sandbox(cfg)
        result = sandbox.map_virtual_path("/workspace/other.py")
        assert result == "/workspace/other.py"

    def test_mapping_traversal_blocked(self):
        cfg = SandboxConfig(
            type=SandboxType.LOCAL,
            writable_paths=["/workspace"],
            virtual_path_map={"/project": "/workspace/../etc"},
        )
        sandbox = Sandbox(cfg)
        with pytest.raises(SandboxError, match="Path traversal detected"):
            sandbox.map_virtual_path("/project/passwd")


# ─── Sandbox Lifecycle (LOCAL mode) ─────────────────────────────────


class TestSandboxLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        sandbox = Sandbox(SandboxConfig(type=SandboxType.LOCAL))
        assert sandbox.is_running is False
        await sandbox.start()
        assert sandbox.is_running is True
        await sandbox.stop()
        assert sandbox.is_running is False

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self):
        sandbox = Sandbox(SandboxConfig(type=SandboxType.LOCAL))
        await sandbox.start()
        await sandbox.start()  # Should not raise
        assert sandbox.is_running is True
        await sandbox.stop()

    @pytest.mark.asyncio
    async def test_double_stop_is_noop(self):
        sandbox = Sandbox(SandboxConfig(type=SandboxType.LOCAL))
        await sandbox.start()
        await sandbox.stop()
        await sandbox.stop()  # Should not raise
        assert sandbox.is_running is False

    @pytest.mark.asyncio
    async def test_context_manager(self):
        async with Sandbox(SandboxConfig(type=SandboxType.LOCAL)) as sandbox:
            assert sandbox.is_running is True
        assert sandbox.is_running is False

    @pytest.mark.asyncio
    async def test_context_manager_cleanup_on_error(self):
        """Sandbox should stop even if an error occurs inside the context."""
        sandbox = Sandbox(SandboxConfig(type=SandboxType.LOCAL))
        with pytest.raises(ValueError, match="test error"):
            async with sandbox:
                assert sandbox.is_running is True
                raise ValueError("test error")
        assert sandbox.is_running is False

    @pytest.mark.asyncio
    async def test_execute_without_start_raises(self):
        sandbox = Sandbox(SandboxConfig(type=SandboxType.LOCAL))
        with pytest.raises(SandboxError, match="not started"):
            await sandbox.execute("echo hello")

    @pytest.mark.asyncio
    async def test_container_id_none_for_local(self):
        sandbox = Sandbox(SandboxConfig(type=SandboxType.LOCAL))
        assert sandbox.container_id is None
        await sandbox.start()
        assert sandbox.container_id is None  # LOCAL mode has no container
        await sandbox.stop()


# ─── Command Execution (LOCAL mode) ─────────────────────────────────


class TestSandboxExecute:
    @pytest.mark.asyncio
    async def test_simple_command(self):
        async with Sandbox(SandboxConfig(type=SandboxType.LOCAL)) as sandbox:
            result = await sandbox.execute("echo hello")
            assert result.exit_code == 0
            assert result.stdout.strip() == "hello"
            assert result.timed_out is False
            assert result.duration_seconds > 0

    @pytest.mark.asyncio
    async def test_command_stderr(self):
        async with Sandbox(SandboxConfig(type=SandboxType.LOCAL)) as sandbox:
            result = await sandbox.execute("echo error >&2")
            assert result.exit_code == 0
            assert "error" in result.stderr

    @pytest.mark.asyncio
    async def test_command_failure(self):
        async with Sandbox(SandboxConfig(type=SandboxType.LOCAL)) as sandbox:
            result = await sandbox.execute("false")
            assert result.exit_code != 0

    @pytest.mark.asyncio
    async def test_command_stdout_and_stderr(self):
        async with Sandbox(SandboxConfig(type=SandboxType.LOCAL)) as sandbox:
            result = await sandbox.execute("echo out && echo err >&2")
            assert "out" in result.stdout
            assert "err" in result.stderr

    @pytest.mark.asyncio
    async def test_timeout_kills_command(self):
        cfg = SandboxConfig(type=SandboxType.LOCAL, timeout_seconds=1)
        async with Sandbox(cfg) as sandbox:
            result = await sandbox.execute("sleep 30", timeout=1)
            assert result.timed_out is True
            assert result.exit_code != 0

    @pytest.mark.asyncio
    async def test_custom_timeout_override(self):
        cfg = SandboxConfig(type=SandboxType.LOCAL, timeout_seconds=30)
        async with Sandbox(cfg) as sandbox:
            result = await sandbox.execute("sleep 30", timeout=1)
            assert result.timed_out is True


# ─── File Read/Write (LOCAL mode) ───────────────────────────────────


class TestSandboxFiles:
    @pytest.mark.asyncio
    async def test_write_and_read_file(self, tmp_path):
        workspace = str(tmp_path / "workspace")
        Path(workspace).mkdir()
        cfg = SandboxConfig(
            type=SandboxType.LOCAL,
            writable_paths=[workspace],
        )
        async with Sandbox(cfg) as sandbox:
            file_path = f"{workspace}/test.txt"
            await sandbox.write_file(file_path, "hello world")
            content = await sandbox.read_file(file_path)
            assert content == "hello world"

    @pytest.mark.asyncio
    async def test_write_creates_directories(self, tmp_path):
        workspace = str(tmp_path / "workspace")
        Path(workspace).mkdir()
        cfg = SandboxConfig(
            type=SandboxType.LOCAL,
            writable_paths=[workspace],
        )
        async with Sandbox(cfg) as sandbox:
            file_path = f"{workspace}/sub/dir/test.txt"
            await sandbox.write_file(file_path, "nested")
            content = await sandbox.read_file(file_path)
            assert content == "nested"

    @pytest.mark.asyncio
    async def test_read_nonexistent_raises(self, tmp_path):
        workspace = str(tmp_path / "workspace")
        Path(workspace).mkdir()
        cfg = SandboxConfig(
            type=SandboxType.LOCAL,
            writable_paths=[workspace],
        )
        async with Sandbox(cfg) as sandbox:
            with pytest.raises(SandboxError, match="File not found"):
                await sandbox.read_file(f"{workspace}/nope.txt")

    @pytest.mark.asyncio
    async def test_write_outside_allowed_path_blocked(self, tmp_path):
        workspace = str(tmp_path / "workspace")
        Path(workspace).mkdir()
        cfg = SandboxConfig(
            type=SandboxType.LOCAL,
            writable_paths=[workspace],
        )
        async with Sandbox(cfg) as sandbox:
            with pytest.raises(SandboxError, match="outside allowed roots"):
                await sandbox.write_file("/etc/evil.txt", "bad")

    @pytest.mark.asyncio
    async def test_write_traversal_blocked(self, tmp_path):
        workspace = str(tmp_path / "workspace")
        Path(workspace).mkdir()
        cfg = SandboxConfig(
            type=SandboxType.LOCAL,
            writable_paths=[workspace],
        )
        async with Sandbox(cfg) as sandbox:
            with pytest.raises(SandboxError, match="Path traversal"):
                await sandbox.write_file(f"{workspace}/../escape.txt", "bad")

    @pytest.mark.asyncio
    async def test_file_ops_without_start_raises(self):
        sandbox = Sandbox(SandboxConfig(type=SandboxType.LOCAL))
        with pytest.raises(SandboxError, match="not started"):
            await sandbox.write_file("/workspace/test.txt", "data")
        with pytest.raises(SandboxError, match="not started"):
            await sandbox.read_file("/workspace/test.txt")


# ─── SandboxedShellSkill ────────────────────────────────────────────


class TestSandboxedShellSkill:
    @pytest.mark.asyncio
    async def test_basic_execution(self):
        async with Sandbox(SandboxConfig(type=SandboxType.LOCAL)) as sandbox:
            skill = SandboxedShellSkill(sandbox=sandbox)
            result = await skill.execute({"command": "echo hello"})
            assert result.success is True
            assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_skill_name_and_description(self):
        sandbox = Sandbox(SandboxConfig(type=SandboxType.LOCAL))
        skill = SandboxedShellSkill(sandbox=sandbox)
        assert skill.name == "sandboxed_shell"
        assert "sandbox" in skill.description.lower()
        assert "command" in skill.parameters["properties"]

    @pytest.mark.asyncio
    async def test_empty_command(self):
        async with Sandbox(SandboxConfig(type=SandboxType.LOCAL)) as sandbox:
            skill = SandboxedShellSkill(sandbox=sandbox)
            result = await skill.execute({"command": ""})
            assert result.success is False
            assert "No command" in result.error

    @pytest.mark.asyncio
    async def test_missing_command(self):
        async with Sandbox(SandboxConfig(type=SandboxType.LOCAL)) as sandbox:
            skill = SandboxedShellSkill(sandbox=sandbox)
            result = await skill.execute({})
            assert result.success is False

    @pytest.mark.asyncio
    async def test_allowed_commands_filter(self):
        async with Sandbox(SandboxConfig(type=SandboxType.LOCAL)) as sandbox:
            skill = SandboxedShellSkill(sandbox=sandbox, allowed_commands=["echo"])
            result = await skill.execute({"command": "echo ok"})
            assert result.success is True

            result = await skill.execute({"command": "rm -rf /"})
            assert result.success is False
            assert "not allowed" in result.error

    @pytest.mark.asyncio
    async def test_sandbox_not_running(self):
        sandbox = Sandbox(SandboxConfig(type=SandboxType.LOCAL))
        skill = SandboxedShellSkill(sandbox=sandbox)
        result = await skill.execute({"command": "echo hello"})
        assert result.success is False
        assert "not running" in result.error

    @pytest.mark.asyncio
    async def test_command_failure_returns_exit_code(self):
        async with Sandbox(SandboxConfig(type=SandboxType.LOCAL)) as sandbox:
            skill = SandboxedShellSkill(sandbox=sandbox)
            result = await skill.execute({"command": "false"})
            assert result.success is False
            assert "Exit code" in result.error

    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        cfg = SandboxConfig(type=SandboxType.LOCAL, timeout_seconds=1)
        async with Sandbox(cfg) as sandbox:
            skill = SandboxedShellSkill(sandbox=sandbox, timeout=1)
            result = await skill.execute({"command": "sleep 30"})
            assert result.success is False
            assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_stderr_included_in_output(self):
        async with Sandbox(SandboxConfig(type=SandboxType.LOCAL)) as sandbox:
            skill = SandboxedShellSkill(sandbox=sandbox)
            result = await skill.execute({"command": "echo out && echo err >&2"})
            assert result.success is True
            assert "out" in result.output
            assert "STDERR" in result.output
            assert "err" in result.output


# ─── Agent Runner Integration ───────────────────────────────────────


class TestAgentRunnerSandboxIntegration:
    def test_create_skill_registry_without_sandbox(self):
        """Registry works without sandbox (backwards compatible)."""
        from agent_orchestrator.dashboard.agent_runner import create_skill_registry

        registry = create_skill_registry()
        assert "shell_exec" in registry.list_skills()
        assert "sandboxed_shell" not in registry.list_skills()

    @pytest.mark.asyncio
    async def test_create_skill_registry_with_sandbox(self):
        """Registry includes sandboxed_shell when sandbox is provided."""
        from agent_orchestrator.dashboard.agent_runner import create_skill_registry

        async with Sandbox(SandboxConfig(type=SandboxType.LOCAL)) as sandbox:
            registry = create_skill_registry(sandbox=sandbox)
            assert "shell_exec" in registry.list_skills()
            assert "sandboxed_shell" in registry.list_skills()
