# LangGraph — Retry Logic & Error Handling

## RetryPolicy

```python
class RetryPolicy:
    initial_interval: float = 0.5     # first retry delay
    backoff_factor: float = 2.0       # exponential multiplier
    max_interval: float = 128.0       # cap on delay
    max_attempts: int = 3             # total attempts
    jitter: bool = True               # uniform random [0, 1) added
    retry_on: Callable[[Exception], bool] = default_retry_on
```

Applied per-node: `graph.add_node("name", func, retry_policy=RetryPolicy(...))`

## run_with_retry / arun_with_retry

Top-level task executors:

### Normal Flow

1. Clear task writes
2. Invoke `task.proc`
3. On success → return
4. On `ParentCommand` → check if targets current graph, pass to writers
5. On `GraphBubbleUp` (interrupts) → re-raise immediately

### Retry Flow

1. Check each `RetryPolicy` in order (first-match wins)
2. `_should_retry_on` supports: sequence of exception classes, single class, callable predicate
3. On match:
   - Increment attempts
   - Compute backoff: `min(max_interval, initial_interval * backoff_factor^(attempts-1))`
   - Add jitter `[0, 1)` if enabled
   - Sleep (sync or async)
   - Set `CONFIG_KEY_RESUMING=True` to signal subgraphs
4. On Python 3.11+: `exc.add_note(...)` annotates errors with task name and ID

## Default Retry Predicate

```python
def default_retry_on(exc):
    # RETRY:
    #   ConnectionError
    #   httpx.HTTPStatusError with 5xx
    #   requests.HTTPError with 5xx
    #   Unknown exception types (conservative)

    # DO NOT RETRY:
    #   ValueError, TypeError, ArithmeticError
    #   ImportError, LookupError, NameError
    #   SyntaxError, RuntimeError, ReferenceError
    #   StopIteration, StopAsyncIteration, OSError
```

## Error Hierarchy in Pregel

| Exception | Treatment |
|-----------|-----------|
| `GraphInterrupt` | Not an error. Persists interrupt payload, pauses execution |
| `GraphBubbleUp` | Re-raised immediately, no retry |
| `CancelledError` | Writes error to task, saves to checkpointer |
| `ParentCommand` | Routed to parent graph or task writers |
| Other | Retry if policy matches, else write error to checkpointer |

## Error Aggregation

When multiple concurrent tasks fail:
- All exceptions collected
- Multiple `GraphInterrupt`s aggregated into single one with combined payloads
- Non-interrupt exceptions: first is raised, rest attached

## Traceback Cleaning

`EXCLUDED_FRAME_FNAMES` list removes LangGraph internal frames from tracebacks:
- Users see errors at their node code, not inside framework
- Frames from `_runner.py`, `_retry.py`, `_loop.py`, etc. are stripped

## Task-Level Error Persistence

Errors are persisted via `put_writes` with the `ERROR` special channel (idx=-1). This enables:
- Post-mortem debugging via checkpoint inspection
- Error recovery on resume
- State rollback
