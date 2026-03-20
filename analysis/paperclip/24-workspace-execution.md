# 24 - Workspace and Execution Model

## Overview

Paperclip provides managed execution workspaces for agents. Each task can run in a shared project workspace or an isolated git worktree. Runtime services (dev servers, databases) have lease-based lifecycle management.

## Workspace Strategies

| Strategy | Isolation | Use case |
|----------|-----------|----------|
| `project_primary` | Shared | Simple tasks, same codebase |
| `git_worktree` | Isolated | Feature branches, parallel work |

## Workspace Realization

```typescript
interface RealizedExecutionWorkspace {
  strategy: "project_primary" | "git_worktree";
  cwd: string;              // resolved working directory
  branchName: string | null; // for worktree strategy
  worktreePath: string | null;
  warnings: string[];
  created: boolean;         // was it newly created?
}
```

The heartbeat service calls `realizeExecutionWorkspace()` before each run to ensure the workspace exists and is ready.

## Managed Project Workspaces

For projects with a repo URL:
```typescript
async function ensureManagedProjectWorkspace(input) {
  const cwd = resolveManagedProjectWorkspaceDir({
    companyId, projectId, repoName
  });
  // Check if git repo exists
  // If not, clone with timeout (10 min)
  // Return { cwd, warning }
}
```

Clone timeout: 10 minutes. If repo is already cloned, workspace is reused.

## Runtime Services

Agents may need runtime services (dev servers, databases) during execution:

```typescript
interface RuntimeServiceRef {
  serviceName: string;
  status: "starting" | "running" | "stopped" | "failed";
  lifecycle: "shared" | "ephemeral";
  scopeType: "project_workspace" | "execution_workspace" | "run" | "agent";
  provider: "local_process" | "adapter_managed";
  port: number;
  url: string;
  healthStatus: "unknown" | "healthy" | "unhealthy";
  reused: boolean;
}
```

Key features:
- **Lease-based lifecycle** — Services have `leaseRunIds` (set of runs using them)
- **Reuse** — Shared services are reused across runs if the environment matches
- **Idle cleanup** — Services with no active leases are cleaned up after timeout
- **Health checks** — Periodic health monitoring

## Workspace Operations

Tracked via `workspaceOperations` table:
- Git clone, branch creation, worktree management
- Build commands, setup scripts
- Operation logging for auditability

## Key Patterns
- Git worktree for task isolation (no branch conflicts)
- Lease-based service lifecycle (automatic cleanup)
- Environment fingerprinting for service reuse decisions
- Managed workspace state in DB (not just filesystem)
- Operation logging for auditability

## Relevance to Our Project
Our `Sandbox` provides Docker-based isolation. Paperclip's git worktree approach is lighter-weight — no container overhead. The lease-based runtime service management is sophisticated — our sandbox has simple start/stop. The environment fingerprinting pattern (decide whether to reuse based on config hash) is worth adopting for our cache system.
