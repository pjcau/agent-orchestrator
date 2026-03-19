# Feature: Docker Sandbox for Code Execution

## Context

From DeerFlow analysis (analysis/deepflow/08-sandbox-system.md, 29-learnings.md).
Agents currently execute code directly on the host. A sandbox isolates agent-generated code in a Docker container with restricted permissions.

## What to Build

### 1. Sandbox Manager

```python
# src/agent_orchestrator/core/sandbox.py

from dataclasses import dataclass
from enum import Enum

class SandboxType(Enum):
    DOCKER = "docker"       # OrbStack/Docker container
    LOCAL = "local"          # Direct host execution (current behavior, for dev)

@dataclass
class SandboxConfig:
    type: SandboxType = SandboxType.DOCKER
    image: str = "python:3.12-slim"
    timeout_seconds: int = 60
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    network_enabled: bool = False     # No internet by default
    writable_paths: list[str] = None  # Virtual paths agent can write to

class Sandbox:
    """Isolated execution environment for agent-generated code."""

    def __init__(self, config: SandboxConfig, session_id: str):
        self._config = config
        self._session_id = session_id
        self._container_id: str | None = None
        self._workdir = f"/workspace/{session_id}"

    async def start(self) -> None:
        """Create and start the sandbox container."""
        # docker create with limits, mount session workdir
        ...

    async def execute(self, command: str, timeout: int | None = None) -> SandboxResult:
        """Execute a command inside the sandbox. Returns stdout, stderr, exit_code."""
        ...

    async def write_file(self, path: str, content: str) -> None:
        """Write a file inside the sandbox (virtual path system)."""
        # Validate path is within allowed writable_paths
        ...

    async def read_file(self, path: str) -> str:
        """Read a file from the sandbox."""
        ...

    async def stop(self) -> None:
        """Stop and remove the sandbox container."""
        ...

    async def __aenter__(self): ...
    async def __aexit__(self, *args): ...
```

### 2. Sandbox Result

```python
@dataclass
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    duration_seconds: float
```

### 3. Sandboxed Skill Variants

Create sandboxed versions of shell_exec and file_write:

```python
# src/agent_orchestrator/skills/sandboxed_shell.py

class SandboxedShellSkill(Skill):
    """Execute shell commands inside the sandbox, not on the host."""

    async def execute(self, params: dict) -> SkillResult:
        result = await self.sandbox.execute(params["command"])
        return SkillResult(
            success=result.exit_code == 0,
            output=result.stdout if result.exit_code == 0 else result.stderr,
        )
```

### 4. Virtual Path System

Map agent-visible paths to sandbox paths:

```
Agent sees:     /project/src/app.py
Sandbox maps:   /workspace/<session_id>/src/app.py
Host maps:      jobs/job_<session_id>/src/app.py
```

- Agent cannot access paths outside `/project/`
- Paths are validated before any read/write
- Path traversal attacks blocked (no `../`)

### 5. Integration with Agent Runner

```python
# In agent_runner.py, when sandbox is enabled:

async with Sandbox(sandbox_config, session_id) as sandbox:
    # Replace shell_exec and file_write skills with sandboxed versions
    registry.register(SandboxedShellSkill(sandbox=sandbox))
    registry.register(SandboxedFileWriteSkill(sandbox=sandbox))

    result = await agent.execute(task)
```

### 6. Configuration

Add sandbox config to agent configuration:

```yaml
# In orchestrator.yaml
sandbox:
  enabled: true
  type: docker
  image: "python:3.12-slim"
  timeout: 60
  memory: "512m"
  network: false
```

## Files to Modify

- **Create**: `src/agent_orchestrator/core/sandbox.py`
- **Create**: `src/agent_orchestrator/skills/sandboxed_shell.py`
- **Modify**: `src/agent_orchestrator/dashboard/agent_runner.py` (wrap execution in sandbox)
- **Modify**: `src/agent_orchestrator/core/yaml_config.py` (add sandbox config section)

## Tests

- Test sandbox container creation and cleanup
- Test command execution returns stdout/stderr/exit_code
- Test timeout kills long-running commands
- Test file write inside sandbox
- Test file read from sandbox
- Test path traversal blocked
- Test virtual path mapping
- Test memory limit enforcement
- Test network disabled by default
- Test SandboxedShellSkill wraps correctly
- Test graceful cleanup on error

## Acceptance Criteria

- [ ] Sandbox class with Docker container management
- [ ] SandboxedShellSkill and SandboxedFileWriteSkill
- [ ] Virtual path system with traversal protection
- [ ] Timeout and resource limits enforced
- [ ] Network disabled by default
- [ ] Integration with agent runner
- [ ] All tests pass
- [ ] Existing tests still pass
