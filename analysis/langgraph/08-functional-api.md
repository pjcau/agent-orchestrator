# LangGraph — Functional API (@task, @entrypoint)

## Overview

The functional API provides an **imperative** alternative to the declarative StateGraph API. Both compile to the same Pregel runtime.

## @task Decorator

Wraps a function into a `_TaskFunction` that returns a `SyncAsyncFuture` instead of executing immediately:

```python
from langgraph.func import task

@task
def add_one(x: int) -> int:
    return x + 1

# Inside an entrypoint:
future = add_one(5)
result = future.result()  # 6
```

### Options
- `retry_policy` — RetryPolicy for automatic retries
- `cache_policy` — CachePolicy for result caching
- `name` — Custom task name

### Parallelism

Call multiple tasks and collect futures:

```python
@entrypoint(checkpointer=saver)
def parallel_workflow(inputs: list[str]) -> list[str]:
    futures = [process_item(item) for item in inputs]
    return [f.result() for f in futures]
```

## @entrypoint Decorator

Compiles a function directly into a Pregel graph:

```python
from langgraph.func import entrypoint
from langgraph.checkpoint.memory import InMemorySaver

@entrypoint(checkpointer=InMemorySaver())
def my_workflow(topic: str) -> str:
    essay = compose_essay(topic).result()
    return essay
```

### Under the Hood

Creates a minimal Pregel graph:
- **One node** — the decorated function
- **Three channels**:
  - `START` (EphemeralValue) — input
  - `END` (LastValue) — output
  - `PREVIOUS` (LastValue) — saved state from prior invocation

### entrypoint.final — Split Return vs Save

```python
@dataclass
class final(Generic[R, S]):
    value: R   # returned to caller
    save: S    # saved to checkpoint as "previous"
```

Allows returning a value to the caller while saving different state for next invocation.

### Previous State

The `previous` parameter receives saved state from prior invocation on the same thread:

```python
@entrypoint(checkpointer=saver)
def counter(input: str, *, previous: int = 0) -> entrypoint.final[str, int]:
    new_count = previous + 1
    return entrypoint.final(value=f"Count: {new_count}", save=new_count)
```

## Key Difference from Graph API

| Aspect | Graph API | Functional API |
|--------|-----------|---------------|
| Style | Declarative (nodes + edges) | Imperative (Python control flow) |
| Parallelism | Static edges + Send | Multiple task futures |
| Underlying | Static Pregel graph | Dynamic task spawning (PUSH/Send) |
| Runtime | Same Pregel engine | Same Pregel engine |

Tasks can **only** be called within an entrypoint or StateGraph context (they need the Pregel runtime to manage state).
