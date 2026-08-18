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

    async def get_stats(self) -> dict[str, float]:
        """Return live resource usage snapshot for this sandbox (PR #81 follow-up).

        Returns a dict with ``cpu_percent``, ``memory_bytes``,
        ``memory_limit_bytes``, ``memory_percent``, ``net_rx_bytes``,
        ``net_tx_bytes``. Returns zeros if the container is not running
        or when Docker is unavailable — never raises.
        """
        zero = {
            "cpu_percent": 0.0,
            "memory_bytes": 0.0,
            "memory_limit_bytes": 0.0,
            "memory_percent": 0.0,
            "net_rx_bytes": 0.0,
            "net_tx_bytes": 0.0,
        }
        if self._config.type != SandboxType.DOCKER or not self._container_id:
            return zero

        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{json .}}",
                self._container_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0 or not stdout:
                return zero

            import json as _json

            data = _json.loads(stdout.decode(errors="replace").strip().splitlines()[0])

            def _parse_bytes(s: str) -> float:
                s = s.strip()
                units = {
                    "B": 1,
                    "KB": 1_000,
                    "KiB": 1_024,
                    "MB": 1_000_000,
                    "MiB": 1_048_576,
                    "GB": 1_000_000_000,
                    "GiB": 1_073_741_824,
                }
                for unit, mul in sorted(units.items(), key=lambda kv: -len(kv[0])):
                    if s.endswith(unit):
                        try:
                            return float(s[: -len(unit)].strip()) * mul
                        except ValueError:
                            return 0.0
                try:
                    return float(s)
                except ValueError:
                    return 0.0

            cpu_pct = float(data.get("CPUPerc", "0").rstrip("%") or 0)
            # MemUsage is "used / limit"
            mem_usage_str = str(data.get("MemUsage", "0B / 0B"))
            used_s, _, limit_s = mem_usage_str.partition("/")
            mem_used = _parse_bytes(used_s)
            mem_limit = _parse_bytes(limit_s)
            mem_pct = float(data.get("MemPerc", "0").rstrip("%") or 0)
            net_str = str(data.get("NetIO", "0B / 0B"))
            rx_s, _, tx_s = net_str.partition("/")
            return {
                "cpu_percent": cpu_pct,
                "memory_bytes": mem_used,
                "memory_limit_bytes": mem_limit,
                "memory_percent": mem_pct,
                "net_rx_bytes": _parse_bytes(rx_s),
                "net_tx_bytes": _parse_bytes(tx_s),
            }
        except Exception:
            # Docker unavailable, parse errors, etc — return zeros silently.
            return zero

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

    async def reset(self) -> bool:
        """Restore the sandbox to a clean state so it can be reused safely.

        Threat model: a pooled sandbox is handed to a *different* task after
        release, so anything the previous task left behind is a cross-task
        contamination channel. Reset therefore:

        1. Kills every process except PID 1 (the container's ``sleep infinity``
           keep-alive) — background daemons a task started must not observe
           the next task.
        2. Wipes the contents of every writable path — files are the other
           persistence channel (tmpfs mounts survive ``docker exec`` exits).

        Environment variables are fixed at container start from the pool's
        single shared ``SandboxConfig`` and cannot be mutated across ``docker
        exec`` calls, so they need no scrubbing.

        Returns True when the sandbox is clean and healthy for reuse, False
        when it should be discarded (caller must ``stop()`` it). LOCAL
        sandboxes always return False: they have no isolation boundary, so
        "reuse after reset" is meaningless — and wiping ``writable_paths``
        would delete real host directories.
        """
        if not self._started:
            return False
        if self._config.type != SandboxType.DOCKER or not self._container_id:
            return False

        # 1. Kill everything but PID 1 and the killer shell itself.
        kill_cmd = (
            "for p in /proc/[0-9]*; do pid=${p#/proc/}; "
            'if [ "$pid" != 1 ] && [ "$pid" != "$$" ]; then '
            "kill -9 $pid 2>/dev/null; fi; done; true"
        )
        # 2. Wipe writable paths (quote each; config paths, not user input).
        wipe_parts = [
            f"find '{path}' -mindepth 1 -delete 2>/dev/null" for path in self._config.writable_paths
        ]
        cmd = kill_cmd + ("; " + "; ".join(wipe_parts) if wipe_parts else "") + "; true"

        try:
            result = await self._execute_docker(cmd, self._config.timeout_seconds)
        except Exception:
            return False
        if result.timed_out or result.exit_code != 0:
            return False

        # Health check: the container must still be running.
        return await self._get_docker_status() == "running"

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

    # NOTE: SandboxPool below assumes execute()/reset() are the only ways a
    # task touches a pooled container — direct docker CLI access bypasses the
    # reset guarantees.

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


class SandboxPool:
    """Pre-provisioned pool of warm sandboxes for millisecond acquire latency.

    Every sandboxed execution normally pays full ``docker run`` startup
    (~1-3s). The pool keeps ``min_ready`` started sandboxes idle so
    ``acquire()`` returns one in microseconds; ``release()`` resets it
    (process kill + workspace wipe — see :meth:`Sandbox.reset`) and requeues
    it only if the reset succeeded, otherwise the sandbox is destroyed and a
    fresh replacement is spawned. Inspired by CubeSandbox's warm-pool design.

    All sandboxes in a pool share one ``SandboxConfig`` — a pool is
    homogeneous by construction, so a reused container never leaks another
    config's image, env vars, or mounts.

    Usage::

        pool = SandboxPool(SandboxConfig(), min_ready=2, max_total=8)
        await pool.start()
        sb = await pool.acquire()
        try:
            result = await sb.execute("python -c 'print(42)'")
        finally:
            await pool.release(sb)
        ...
        await pool.stop()

    Concurrency: ``acquire()`` blocks (up to ``timeout``) when all
    ``max_total`` sandboxes are in use; releases wake waiters in FIFO order.
    """

    def __init__(
        self,
        config: SandboxConfig | None = None,
        min_ready: int = 2,
        max_total: int = 8,
    ) -> None:
        if min_ready < 0:
            raise ValueError("min_ready must be >= 0")
        if max_total < max(min_ready, 1):
            raise ValueError("max_total must be >= max(min_ready, 1)")
        self._config = config or SandboxConfig()
        self._min_ready = min_ready
        self._max_total = max_total
        self._idle: asyncio.Queue[Sandbox] = asyncio.Queue()
        self._in_use: set[Sandbox] = set()
        # Sandboxes being spawned right now — counted against max_total so a
        # burst of acquires can't over-provision past the cap.
        self._spawning = 0
        self._lock = asyncio.Lock()
        self._closed = False

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def idle_count(self) -> int:
        return self._idle.qsize()

    @property
    def in_use_count(self) -> int:
        return len(self._in_use)

    @property
    def total_count(self) -> int:
        return self.idle_count + self.in_use_count + self._spawning

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Pre-warm ``min_ready`` sandboxes concurrently."""
        if self._closed:
            raise SandboxError("Pool is stopped")
        spawns = []
        async with self._lock:
            budget = min(self._min_ready, self._max_total) - self.total_count
            if budget > 0:
                self._spawning += budget
                spawns = [self._spawn_ready() for _ in range(budget)]
        if spawns:
            await asyncio.gather(*spawns)

    async def stop(self) -> None:
        """Stop every idle and in-use sandbox and refuse further acquires."""
        self._closed = True
        victims: list[Sandbox] = list(self._in_use)
        self._in_use.clear()
        while True:
            try:
                victims.append(self._idle.get_nowait())
            except asyncio.QueueEmpty:
                break
        if victims:
            await asyncio.gather(*(sb.stop() for sb in victims), return_exceptions=True)

    # ------------------------------------------------------------------
    # Acquire / release
    # ------------------------------------------------------------------

    async def acquire(self, timeout: float = 30.0) -> Sandbox:
        """Return a warm sandbox, spawning a new one if the pool has room.

        Raises SandboxError when the pool is stopped, and
        ``asyncio.TimeoutError`` when ``max_total`` sandboxes are busy for
        longer than ``timeout`` seconds.
        """
        if self._closed:
            raise SandboxError("Pool is stopped")

        try:
            sb = self._idle.get_nowait()
        except asyncio.QueueEmpty:
            # Nothing idle: spawn a replacement if the cap allows, then wait.
            async with self._lock:
                if self.total_count < self._max_total:
                    self._spawning += 1
                    asyncio.ensure_future(self._spawn_ready())
            sb = await asyncio.wait_for(self._idle.get(), timeout=timeout)

        self._in_use.add(sb)
        # Background refill keeps the next caller warm too.
        async with self._lock:
            if self.idle_count < self._min_ready and self.total_count < self._max_total:
                self._spawning += 1
                asyncio.ensure_future(self._spawn_ready())
        return sb

    async def release(self, sb: Sandbox) -> None:
        """Return a sandbox to the pool after resetting it.

        A sandbox that fails reset (dirty, unhealthy, or LOCAL type) is
        destroyed; a replacement is spawned only if the idle set dropped
        below ``min_ready``.
        """
        self._in_use.discard(sb)
        if self._closed:
            await sb.stop()
            return

        if await sb.reset():
            await self._idle.put(sb)
            return

        await sb.stop()
        async with self._lock:
            if self.idle_count < self._min_ready and self.total_count < self._max_total:
                self._spawning += 1
                asyncio.ensure_future(self._spawn_ready())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _new_sandbox(self) -> Sandbox:
        """Factory hook — tests override this to inject fake sandboxes."""
        return Sandbox(self._config)

    async def _spawn_ready(self) -> None:
        sb = self._new_sandbox()
        try:
            await sb.start()
        except Exception:
            # Spawn failures must not leak the reserved slot.
            async with self._lock:
                self._spawning -= 1
            return
        async with self._lock:
            self._spawning -= 1
        if self._closed:
            await sb.stop()
            return
        await self._idle.put(sb)
