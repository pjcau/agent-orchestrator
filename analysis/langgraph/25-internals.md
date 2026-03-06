# LangGraph — Internal Utilities

## Module Structure

All internals are in `langgraph/_internal/` (non-public, stability not guaranteed). The `utils/` module is a deprecated compatibility shim.

## _constants.py — String Intern Registry

All keys are `sys.intern()`-ed for identity comparisons:

- **Reserved write keys**: `INPUT`, `INTERRUPT`, `RESUME`, `ERROR`, `NO_WRITES`, `TASKS`, `RETURN`, `PREVIOUS`
- **Config keys**: `CONFIG_KEY_SEND`, `CONFIG_KEY_READ`, `CONFIG_KEY_CALL`, `CONFIG_KEY_CHECKPOINTER`, `CONFIG_KEY_STREAM`, `CONFIG_KEY_SCRATCHPAD`, etc.
- **Topology markers**: `PUSH` (dynamic tasks from Send) vs `PULL` (edge-triggered)
- **Namespace encoding**: `NS_SEP = "|"`, `NS_END = ":"` (e.g., `parent|child:task_uuid`)

## _config.py — Config Manipulation

- `DEFAULT_RECURSION_LIMIT = 10000` (env `LANGGRAPH_DEFAULT_RECURSION_LIMIT`)
- `patch_configurable(config, patch)` — inject context into running nodes
- `merge_configs(*configs)` — domain-specific merging (metadata merges dicts, tags concatenate, callbacks handle 6 combinations)
- `ensure_config(*configs)` — build fully-populated RunnableConfig, excludes sensitive keys (token, key, secret, password, auth) from metadata

## _runnable.py — Core Node Wrappers

### RunnableCallable

Core node wrapper:
- Inspects function signature **once** at `__init__`
- Records which injectable kwargs the function accepts
- At `invoke`/`ainvoke`, injects from Runtime or config
- Runs in `copy_context()` scope for context var propagation
- `recurse` flag: if function returns a Runnable, invoke it

### RunnableSeq

Simpler pipeline than `RunnableSequence`. First step runs in copied context (the node); subsequent steps (channel writers) don't.

### coerce_to_runnable(thing)

Converts: callables → RunnableCallable, generators → RunnableLambda, dicts → RunnableParallel.

### KWARGS_CONFIG_KEYS

6 injectable kwargs: `config`, `writer`, `store` (required/optional), `previous`, `runtime`.

## _future.py — Cross-Thread/Loop Bridge

- `chain_future(source, dest)` — links futures across threads/loops
- `run_coroutine_threadsafe(coro, loop)` — with eager task support on Python 3.12+
- Handles all 4 combinations of asyncio vs concurrent.futures

## _fields.py — State Schema Introspection

- `get_field_default(name, type_, schema)` — handles TypedDict, dataclass, Pydantic BaseModel
- `get_cached_annotated_keys(obj)` — WeakKeyDictionary cache for MRO traversal
- Handles Python 3.14+ Pydantic descriptor changes

## _cache.py — Cache Key Generation

```python
def default_cache_key(*args, **kwargs) -> bytes:
    frozen = _freeze(args, kwargs)  # depth limit: 10
    return pickle.dumps(frozen, protocol=5)
```

## _pydantic.py — Dynamic Model Construction

- `create_model(name, fields)` — LRU-cached factory
- Remaps `_`-prefixed fields as `private_<name>` with `alias=<original>`
- `is_supported_by_pydantic(type_)` — detects dataclasses, BaseModel, TypedDict

## _retry.py — Default Retry Predicate

```python
def default_retry_on(exc):
    # Retry: ConnectionError, HTTP 5xx (httpx + requests)
    # Don't retry: ValueError, TypeError, ArithmeticError, ImportError,
    #              LookupError, NameError, SyntaxError, RuntimeError, ...
```

## _queue.py — Custom Queues

- `AsyncQueue` — async with `wait()` (peek without consume)
- `SyncQueue` — deque + Semaphore with `wait()` and timeout support

## _scratchpad.py — Per-Task State

Frozen dataclass with: `step`, `stop`, `call_counter`, `interrupt_counter`, `resume`, `subgraph_counter`.
