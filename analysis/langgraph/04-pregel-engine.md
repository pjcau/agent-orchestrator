# LangGraph — Pregel Engine (Runtime)

## Inspiration

The Pregel class implements Google's **Pregel Algorithm / Bulk Synchronous Parallel (BSP)** model:
- **Actors** (PregelNodes) read from channels and write to channels
- **Channels** mediate all communication
- Execution proceeds in discrete **supersteps**

## Key Data Structures

```python
class Pregel:
    nodes: dict[str, PregelNode]                    # actors
    channels: dict[str, BaseChannel | ManagedValueSpec]  # state + triggers
    input_channels: str | Sequence[str]             # where input goes
    output_channels: str | Sequence[str]            # what's returned
    trigger_to_nodes: Mapping[str, Sequence[str]]   # channel -> triggered nodes
```

### PregelNode

```python
class PregelNode:
    channels: dict          # which channels to read as input
    triggers: list[str]     # which channels cause execution
    writers: list           # ChannelWrite operations after node runs
    bound: Runnable         # the actual callable
    retry_policy: ...
    cache_policy: ...
    metadata: dict
```

## Superstep Phases

### 1. PLAN (`prepare_next_tasks`)

For each node, check if any trigger channels updated since last run:
- Compare `channel_versions` against `versions_seen[node_name]`
- Create `PregelExecutableTask` for each triggered node
- Task ID: deterministic UUID5 from (checkpoint_id, node_name, path)
- Also process PUSH tasks (from `Send`)
- Union of PULL tasks (edge-triggered) and PUSH tasks = superstep tasks

### 2. EXECUTE (`runner.tick`)

All triggered nodes run **in parallel** via thread pool (`BackgroundExecutor`):
- Each task reads input from channels
- Runs bound function with retry support
- Writes results via `ChannelWrite`
- Channel isolation: reads see state from step start; writes are buffered

### 3. UPDATE (`apply_writes`)

Applied atomically after all tasks complete:
1. Sort tasks by path (deterministic ordering)
2. Update `versions_seen` — mark consumed channel versions
3. Consume triggered channels (`channel.consume()` — EphemeralValue clears)
4. Group writes by channel
5. Apply: call `channel.update(values)` — **reducers run here**
6. Notify un-updated channels (`update([])` — EphemeralValue clears)
7. Finish check: if no channels can trigger nodes, call `channel.finish()` (activates deferred nodes)

### 4. CHECKPOINT

Save state snapshot after each superstep.

## Loop Termination

The loop ends when:
- No actors are triggered (no channels updated that trigger any node)
- Recursion limit hit (`step > stop`) → `"out_of_steps"` status
- `interrupt_before` / `interrupt_after` triggered → `GraphInterrupt`

## Parallelism Levels

1. **Within superstep** — All triggered nodes run concurrently via `PregelRunner` (thread pool)
2. **Dynamic via Send** — Nodes return `Send` objects creating new tasks in same superstep, accepted via `loop.accept_push()`

## invoke() vs stream()

- **`stream()`** — Primary method. Yields events between steps and task completions
- **`invoke()`** — Wraps `stream()`, returns last "values" event or list of all chunks

## Durability Modes

| Mode | Behavior |
|------|----------|
| `"sync"` (default) | Save checkpoint after every superstep |
| `"async"` | Same with async I/O |
| `"exit"` | Only save on context manager exit (performance optimization) |
