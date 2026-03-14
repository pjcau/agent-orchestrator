# 06 - Sub-Agent System

## Architecture

DeerFlow's sub-agent system uses a `task` tool that the lead agent calls to delegate work. Sub-agents run in background threads with timeout support.

```
Lead Agent
  │
  ├── task("research X", general-purpose)  ──→ Thread Pool ──→ Sub-agent 1
  ├── task("research Y", general-purpose)  ──→ Thread Pool ──→ Sub-agent 2
  └── task("run tests", bash)              ──→ Thread Pool ──→ Sub-agent 3
```

## Built-in Sub-agents

| Name | Purpose | Tools |
|------|---------|-------|
| `general-purpose` | Any non-trivial task | All tools except `task` |
| `bash` | Command execution | Bash-focused tools |

## Execution Engine

### Dual Thread Pool

```python
_scheduler_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="subagent-scheduler-")
_execution_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="subagent-exec-")
```

- **Scheduler pool**: Manages task lifecycle (submit, timeout, status)
- **Execution pool**: Runs actual sub-agent invocations
- Separation prevents blocking: scheduler can timeout while execution runs

### Flow

1. Lead agent calls `task()` tool
2. `SubagentExecutor.execute_async()` creates result holder
3. Submitted to scheduler pool
4. Scheduler submits to execution pool with timeout (default 900s / 15 min)
5. Sub-agent streams results via `astream()` (captures all AI messages)
6. On completion/timeout/failure, result stored in `_background_tasks` dict
7. `task_tool` polls every 5s for completion, returns result to lead agent

### SubagentResult

```python
@dataclass
class SubagentResult:
    task_id: str
    trace_id: str              # For distributed tracing
    status: SubagentStatus     # PENDING/RUNNING/COMPLETED/FAILED/TIMED_OUT
    result: str | None
    error: str | None
    started_at: datetime
    completed_at: datetime
    ai_messages: list[dict]    # All AI messages captured
```

## Concurrency Control

- **MAX_CONCURRENT_SUBAGENTS = 3** (hard limit)
- `SubagentLimitMiddleware` truncates excess `task` calls in model output
- Lead agent system prompt instructs batching: max 3 per turn, then wait
- For >3 sub-tasks: multi-turn sequential batches

## Key Design Decision: Tool-based Delegation

Unlike our orchestrator which has explicit agent routing and cooperation protocols, DeerFlow delegates via a single `task` tool call. This is simpler but less structured:

**Pros**:
- Single unified interface
- Lead agent decides everything (routing, prompts, context)
- Sub-agents are stateless workers
- No complex routing logic needed

**Cons**:
- Lead agent is a bottleneck
- No specialized agent personas (all sub-agents use same prompt)
- Limited cooperation between sub-agents
- No category-based routing

## Memory Cleanup

Completed tasks are cleaned up from `_background_tasks` after result is returned:
- Only removes terminal states (COMPLETED/FAILED/TIMED_OUT)
- Thread-safe with lock
- Prevents memory leaks
