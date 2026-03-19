"""Docker sandbox for isolated code execution.

Provides a secure, containerised execution environment for agent-generated code.
Supports Docker containers (production) and local subprocess (testing/fallback).
Includes virtual path mapping with traversal protection.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import Any


class SandboxType(Enum):
    """Execution environment type."""

    DOCKER = "docker"
    LOCAL = "local"


@dataclass
class SandboxConfig:
    """Configuration for a sandbox environment.

    Attributes:
        type: Docker or local subprocess.
        image: Docker image to use (ignored for LOCAL type).
        timeout_seconds: Default command timeout.
        memory_limit: Docker memory limit (e.g. '512m').
        cpu_limit: Docker CPU limit (e.g. 1.0 = one core).
        network_enabled: Whether to allow network access.
        writable_paths: Paths inside the container that are writable.
        virtual_path_map: Host-to-container path mappings.
    """

    type: SandboxType = SandboxType.DOCKER
    image: str = "python:3.12-slim"
    timeout_seconds: int = 60
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    network_enabled: bool = False
    writable_paths: list[str] = field(default_factory=lambda: ["/workspace"])
    virtual_path_map: dict[str, str] = field(default_factory=dict)


@dataclass
class SandboxResult:
    """Result of a command execution inside a sandbox.

    Attributes:
        stdout: Standard output.
        stderr: Standard error.
        exit_code: Process exit code.
        timed_out: Whether the command was killed due to timeout.
        duration_seconds: Wall-clock execution time.
    """

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    duration_seconds: float


class SandboxError(Exception):
    """Raised when sandbox operations fail."""


def _validate_path(path: str, allowed_roots: list[str]) -> str:
    """Validate a path against traversal attacks.

    Resolves the path and checks it stays within one of the allowed roots.
    Raises SandboxError if the path escapes.
    """
    # Normalise and resolve (collapse .., //, etc.)
    resolved = str(PurePosixPath(path))

    # Check for traversal patterns
    if ".." in resolved.split("/"):
        raise SandboxError(f"Path traversal detected: {path}")

    # Must start with one of the allowed roots
    if allowed_roots:
        if not any(resolved.startswith(root) for root in allowed_roots):
            raise SandboxError(f"Path '{path}' is outside allowed roots: {allowed_roots}")

    return resolved


class Sandbox:
    """Isolated execution environment for agent-generated code.

    Usage::

        config = SandboxConfig(type=SandboxType.DOCKER)
        async with Sandbox(config) as sandbox:
            result = await sandbox.execute("python -c 'print(42)'")
            print(result.stdout)  # "42\\n"

    For tests or environments without Docker, use SandboxType.LOCAL which
    runs commands in a subprocess (no isolation — use only for testing).
    """

    def __init__(self, config: SandboxConfig | None = None) -> None:
        self._config = config or SandboxConfig()
        self._container_id: str | None = None
        self._started = False

    @property
    def config(self) -> SandboxConfig:
        return self._config

    @property
    def is_running(self) -> bool:
        return self._started

    @property
    def container_id(self) -> str | None:
        return self._container_id

    async def start(self) -> None:
        """Start the sandbox environment."""
        if self._started:
            return

        if self._config.type == SandboxType.DOCKER:
            await self._start_docker()
        else:
            # LOCAL mode — no container to start
            pass

        self._started = True

    async def stop(self) -> None:
        """Stop and clean up the sandbox environment."""
        if not self._started:
            return

        if self._config.type == SandboxType.DOCKER and self._container_id:
            await self._stop_docker()

        self._started = False
        self._container_id = None

    async def execute(self, command: str, timeout: int | None = None) -> SandboxResult:
        """Execute a command inside the sandbox.

        Args:
            command: Shell command to run.
            timeout: Override the default timeout (seconds).

        Returns:
            SandboxResult with stdout, stderr, exit_code, etc.

        Raises:
            SandboxError: If the sandbox is not running.
        """
        if not self._started:
            raise SandboxError("Sandbox is not started. Call start() first.")

        effective_timeout = timeout or self._config.timeout_seconds

        if self._config.type == SandboxType.DOCKER:
            return await self._execute_docker(command, effective_timeout)
        else:
            return await self._execute_local(command, effective_timeout)

    async def write_file(self, path: str, content: str) -> None:
        """Write a file inside the sandbox.

        Args:
            path: Absolute path inside the sandbox/container.
            content: File content to write.

        Raises:
            SandboxError: If path validation fails or sandbox is not running.
        """
        if not self._started:
            raise SandboxError("Sandbox is not started. Call start() first.")

        validated = _validate_path(path, self._config.writable_paths)

        if self._config.type == SandboxType.DOCKER:
            # Use docker exec to write the file
            escaped = content.replace("'", "'\\''")
            cmd = f"mkdir -p $(dirname '{validated}') && printf '%s' '{escaped}' > '{validated}'"
            result = await self._execute_docker(cmd, self._config.timeout_seconds)
            if result.exit_code != 0:
                raise SandboxError(f"Failed to write file {validated}: {result.stderr}")
        else:
            # LOCAL mode — write directly
            from pathlib import Path

            target = Path(validated)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

    async def read_file(self, path: str) -> str:
        """Read a file from inside the sandbox.

        Args:
            path: Absolute path inside the sandbox/container.

        Returns:
            File content as string.

        Raises:
            SandboxError: If path validation fails or file does not exist.
        """
        if not self._started:
            raise SandboxError("Sandbox is not started. Call start() first.")

        validated = _validate_path(path, self._config.writable_paths)

        if self._config.type == SandboxType.DOCKER:
            result = await self._execute_docker(f"cat '{validated}'", self._config.timeout_seconds)
            if result.exit_code != 0:
                raise SandboxError(f"Failed to read file {validated}: {result.stderr}")
            return result.stdout
        else:
            from pathlib import Path

            target = Path(validated)
            if not target.exists():
                raise SandboxError(f"File not found: {validated}")
            return target.read_text()

    def map_virtual_path(self, virtual_path: str) -> str:
        """Translate a virtual path to a real sandbox path.

        Uses the virtual_path_map from config. If no mapping matches,
        returns the path unchanged.

        Raises:
            SandboxError: If the resolved path escapes allowed roots.
        """
        for prefix, replacement in self._config.virtual_path_map.items():
            if virtual_path.startswith(prefix):
                mapped = replacement + virtual_path[len(prefix) :]
                return _validate_path(mapped, self._config.writable_paths)

        return _validate_path(virtual_path, self._config.writable_paths)

    # ─── Context Manager ─────────────────────────────────────────────

    async def __aenter__(self) -> Sandbox:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.stop()

    # ─── Docker Internals ────────────────────────────────────────────

    async def _start_docker(self) -> None:
        """Start a Docker container."""
        cmd_parts = [
            "docker",
            "run",
            "-d",
            "--rm",
            f"--memory={self._config.memory_limit}",
            f"--cpus={self._config.cpu_limit}",
        ]

        if not self._config.network_enabled:
            cmd_parts.append("--network=none")

        # Mount writable paths as tmpfs for isolation
        for wp in self._config.writable_paths:
            cmd_parts.extend(["--tmpfs", f"{wp}:rw,size=100m"])

        cmd_parts.extend([self._config.image, "sleep", "infinity"])

        proc = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise SandboxError(
                f"Failed to start Docker container: {stderr.decode(errors='replace')}"
            )

        self._container_id = stdout.decode().strip()

    async def _stop_docker(self) -> None:
        """Stop and remove the Docker container."""
        if not self._container_id:
            return

        proc = await asyncio.create_subprocess_exec(
            "docker",
            "kill",
            self._container_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    async def _execute_docker(self, command: str, timeout: int) -> SandboxResult:
        """Execute a command inside the Docker container."""
        start = time.monotonic()
        timed_out = False

        proc = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            self._container_id,  # type: ignore[arg-type]
            "sh",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            timed_out = True
            stdout = b""
            stderr = b"Command timed out"

        duration = time.monotonic() - start
        return SandboxResult(
            stdout=stdout.decode(errors="replace") if not timed_out else "",
            stderr=stderr.decode(errors="replace") if not timed_out else "Command timed out",
            exit_code=proc.returncode if proc.returncode is not None else -1,
            timed_out=timed_out,
            duration_seconds=round(duration, 3),
        )

    async def _execute_local(self, command: str, timeout: int) -> SandboxResult:
        """Execute a command locally (no isolation — for testing only)."""
        start = time.monotonic()
        timed_out = False

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            timed_out = True
            stdout = b""
            stderr = b"Command timed out"

        duration = time.monotonic() - start
        return SandboxResult(
            stdout=stdout.decode(errors="replace") if not timed_out else "",
            stderr=stderr.decode(errors="replace") if not timed_out else "Command timed out",
            exit_code=proc.returncode if proc.returncode is not None else -1,
            timed_out=timed_out,
            duration_seconds=round(duration, 3),
        )
