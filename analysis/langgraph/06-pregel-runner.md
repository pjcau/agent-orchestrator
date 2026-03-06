# LangGraph — PregelRunner (Concurrent Task Execution)

## FuturesDict

A dict mapping futures to tasks with:
- Counter tracking in-flight tasks
- Event (threading or asyncio) set when counter reaches 0
- `done_callback` (WeakMethod) after each task

## PregelRunner

The concurrency coordinator for task execution within a superstep.

### tick() / atick() — Generator Methods

Yield control between scheduling and processing, enabling the Pregel loop to interleave execution with checkpoint/stream output.

### Fast Path

Single task, no timeout, no waiter → runs inline on current thread (avoids thread pool overhead).

### Multi-Task Path

Schedules all tasks via `submit()` (`BackgroundExecutor.submit`), wrapping each in:
- `run_with_retry` with configured retry policy
- `CONFIG_KEY_CALL` partial for nested `call()` support

### Waiter Mechanism

Optional `get_waiter` callable returns a no-op future. When it completes, another is scheduled. Allows periodic checkpoint-save "ticks" between task completions.

### _should_stop_others(done)

If any non-`GraphBubbleUp` exception occurs, cancels all remaining tasks. `GraphInterrupt` is not a failure.

### _panic_or_proceed(futs)

- Collects all exceptions
- Aggregates multiple `GraphInterrupt`s into single one with combined payloads
- Cancels inflight tasks on failure
- Raises `TimeoutError` for remaining inflight tasks

## commit(task, exception) — Post-Task Callback

| Scenario | Action |
|----------|--------|
| `CancelledError` | Write error to task, save to checkpointer |
| `GraphInterrupt` | Save interrupt payload + resume writes |
| `GraphBubbleUp` | No action (re-raised) |
| Other exception | Write error to checkpointer |
| Success | Call `node_finished`, add `NO_WRITES` if empty, save writes |

## _call / _acall — Nested call() Implementation

For dynamically spawning sub-tasks within a node:
1. Get scratchpad, call `schedule_task()` with `Call(func, input)`
2. If already running → chain to existing future
3. If already completed → create resolved future from writes
4. Otherwise → submit new task with `__next_tick__=True`
5. Return `chain_future(fut, new_future)`

## BackgroundExecutor (Sync)

- Uses `get_executor_for_config(config)` (LangChain's thread pool)
- `submit()` wraps calls in `ctx.run(fn, ...)` for context var propagation
- `__next_tick__=True` → `time.sleep(0)` to yield to other threads
- On exit: cancel `__cancel_on_exit__` tasks, wait all, re-raise first exception

## AsyncBackgroundExecutor

- Uses `asyncio.Semaphore` for `max_concurrency`
- `__next_tick__` → `lazy=True` in `run_coroutine_threadsafe`
- Context propagation via `copy_context()` on Python 3.11+

## Traceback Cleaning

`EXCLUDED_FRAME_FNAMES` removes internal LangGraph frames from exception tracebacks so errors surface at user's node code.
