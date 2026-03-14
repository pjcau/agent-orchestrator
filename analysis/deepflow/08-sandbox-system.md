# 08 - Sandbox System

## Overview

The sandbox is DeerFlow's most distinctive feature. It provides an actual execution environment, not just tool access.

## Three Modes

### 1. Local Sandbox (Default)
- Direct execution on host machine
- Singleton provider
- Path translation: virtual → actual
- Development use

### 2. Docker/Container Sandbox
- `AioSandboxProvider` (all-in-one sandbox)
- Isolated Docker containers per session
- Supports Apple Container on macOS
- LRU eviction (max N concurrent containers)
- Automatic image selection

### 3. Kubernetes Sandbox (Provisioner)
- Dedicated pods per sandbox_id
- Managed by provisioner service (port 8002)
- k3s for local K8s
- Production-grade isolation

## Virtual Path System

```
Agent sees:                              Physical:
/mnt/user-data/workspace    →    backend/.deer-flow/threads/{thread_id}/user-data/workspace
/mnt/user-data/uploads      →    backend/.deer-flow/threads/{thread_id}/user-data/uploads
/mnt/user-data/outputs      →    backend/.deer-flow/threads/{thread_id}/user-data/outputs
/mnt/skills                 →    deer-flow/skills/
```

### Path Translation

- `replace_virtual_path()` — single path
- `replace_virtual_paths_in_command()` — paths in bash commands
- `mask_local_paths_in_output()` — reverse: actual → virtual in output
- `validate_local_bash_command_paths()` — security check for unsafe paths

### Security: Path Traversal Prevention

```python
def resolve_local_tool_path(path, thread_data):
    if not path.startswith(f"{VIRTUAL_PATH_PREFIX}/"):
        raise PermissionError("Only paths under /mnt/user-data/ are allowed")

    resolved = Path(resolved_path).resolve()
    for root in allowed_roots:
        resolved.relative_to(root)  # ValueError if traversal
```

Allowlisted system paths for bash commands:
```python
_LOCAL_BASH_SYSTEM_PATH_PREFIXES = (
    "/bin/", "/usr/bin/", "/usr/sbin/", "/sbin/",
    "/opt/homebrew/bin/", "/dev/",
)
```

## Lazy Initialization

Sandbox is initialized lazily on first tool use:
```python
def ensure_sandbox_initialized(runtime):
    # Check if sandbox already in state
    # If not: acquire from provider, store in state
    # Thread-safe via provider's internal locking
```

This avoids allocating containers for conversations that don't need them.

## Thread Isolation

Each thread gets its own:
- workspace/ — agent's working directory
- uploads/ — user-uploaded files
- outputs/ — final deliverables

Directories created lazily on first file operation.

## Key Insight for Our Project

DeerFlow's sandbox gives agents **actual computing capabilities**:
- Install packages in a venv
- Run Python/Node scripts
- Read/write files
- Execute bash commands

Our orchestrator doesn't have sandboxed execution — agents operate purely through skill abstractions. The sandbox model is more powerful but requires more infrastructure.
