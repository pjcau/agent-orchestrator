"""Docker sandbox for isolated code execution.

Provides a secure, containerised execution environment for agent-generated code.
Supports Docker containers (production) and local subprocess (testing/fallback).
Includes virtual path mapping with traversal protection, port forwarding, and
container introspection for live preview workflows.
"""

from __future__ import annotations

import asyncio
import json
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
class PortMapping:
    """A port mapping between host and container.

    Attributes:
        container_port: Port inside the container.
        host_port: Port on the host (0 = auto-assign).
        protocol: Protocol (tcp or udp).
    """

    container_port: int
    host_port: int = 0
    protocol: str = "tcp"


@dataclass
class SandboxInfo:
    """Runtime information about a sandbox container.

    Attributes:
        container_id: Docker container ID (None for LOCAL).
        status: Current status (running, stopped, not_started).
        image: Docker image name.
        mapped_ports: Actual host:container port mappings after start.
        uptime_seconds: Seconds since container started.
        memory_limit: Configured memory limit.
        cpu_limit: Configured CPU limit.
    """

    container_id: str | None
    status: str
    image: str
    mapped_ports: dict[int, int] = field(default_factory=dict)
    uptime_seconds: float = 0.0
    memory_limit: str = ""
    cpu_limit: float = 0.0


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
        exposed_ports: Ports to forward from container to host.
        startup_command: Optional command to run after container starts.
        env_vars: Environment variables to set inside the container.
    """

    type: SandboxType = SandboxType.DOCKER
    image: str = "python:3.12-slim"
    timeout_seconds: int = 60
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    network_enabled: bool = False
    writable_paths: list[str] = field(default_factory=lambda: ["/workspace"])
    virtual_path_map: dict[str, str] = field(default_factory=dict)
    exposed_ports: list[PortMapping] = field(default_factory=list)
    startup_command: str | None = None
    env_vars: dict[str, str] = field(default_factory=dict)


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
        self._start_time: float | None = None
        self._mapped_ports: dict[int, int] = {}

    @property
    def config(self) -> SandboxConfig:
        return self._config

    @property
    def is_running(self) -> bool:
        return self._started

    @property
    def container_id(self) -> str | None:
        return self._container_id

    @property
    def port_mappings(self) -> dict[int, int]:
        """Actual container_port -> host_port mappings after start."""
        return dict(self._mapped_ports)

    async def get_info(self) -> SandboxInfo:
        """Return runtime information about this sandbox."""
        if not self._started:
            return SandboxInfo(
                container_id=None,
                status="not_started",
                image=self._config.image,
                memory_limit=self._config.memory_limit,
                cpu_limit=self._config.cpu_limit,
            )

        uptime = time.monotonic() - self._start_time if self._start_time else 0.0

        if self._config.type == SandboxType.DOCKER and self._container_id:
            # Query actual container status
            status = await self._get_docker_status()
            # Refresh port mappings from Docker
            await self._refresh_port_mappings()
        else:
            status = "running"

        return SandboxInfo(
            container_id=self._container_id,
            status=status,
            image=self._config.image,
            mapped_ports=dict(self._mapped_ports),
            uptime_seconds=round(uptime, 1),
            memory_limit=self._config.memory_limit,
            cpu_limit=self._config.cpu_limit,
        )

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
        self._start_time = time.monotonic()

    async def stop(self) -> None:
        """Stop and clean up the sandbox environment."""
        if not self._started:
            return

        if self._config.type == SandboxType.DOCKER and self._container_id:
            await self._stop_docker()

        self._started = False
        self._container_id = None
        self._start_time = None
        self._mapped_ports = {}

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
        """Start a Docker container with optional port forwarding and env vars."""
        cmd_parts = [
            "docker",
            "run",
            "-d",
            "--rm",
            f"--memory={self._config.memory_limit}",
            f"--cpus={self._config.cpu_limit}",
        ]

        if not self._config.network_enabled:
            # When ports are exposed, network must be enabled
            if not self._config.exposed_ports:
                cmd_parts.append("--network=none")

        # Port mappings
        for pm in self._config.exposed_ports:
            if pm.host_port:
                cmd_parts.extend(["-p", f"{pm.host_port}:{pm.container_port}/{pm.protocol}"])
            else:
                # Auto-assign host port
                cmd_parts.extend(["-p", f"{pm.container_port}/{pm.protocol}"])

        # Environment variables
        for key, value in self._config.env_vars.items():
            cmd_parts.extend(["-e", f"{key}={value}"])

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

        # Resolve actual port mappings
        if self._config.exposed_ports:
            await self._refresh_port_mappings()

        # Run startup command if configured
        if self._config.startup_command:
            result = await self._execute_docker(
                self._config.startup_command, self._config.timeout_seconds
            )
            if result.exit_code != 0:
                raise SandboxError(f"Startup command failed: {result.stderr}")

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

    async def _refresh_port_mappings(self) -> None:
        """Query Docker for actual port mappings and update _mapped_ports."""
        if not self._container_id:
            return

        proc = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            "--format",
            "{{json .NetworkSettings.Ports}}",
            self._container_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return

        try:
            ports_json = json.loads(stdout.decode().strip())
            self._mapped_ports = {}
            if ports_json:
                for container_key, bindings in ports_json.items():
                    if not bindings:
                        continue
                    # container_key is like "8000/tcp"
                    container_port = int(container_key.split("/")[0])
                    host_port = int(bindings[0]["HostPort"])
                    self._mapped_ports[container_port] = host_port
        except (json.JSONDecodeError, KeyError, IndexError, ValueError):
            pass

    async def _get_docker_status(self) -> str:
        """Query Docker for the container's current status."""
        if not self._container_id:
            return "not_started"

        proc = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            "--format",
            "{{.State.Status}}",
            self._container_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return "unknown"
        return stdout.decode().strip() or "unknown"

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
