# LangGraph — Pregel Loop (Execution Details)

## PregelLoop Class

A 50+ field class managing the full execution context. Status state machine:

```
"input" → "pending" → "done" | "interrupt_before" | "interrupt_after" | "out_of_steps"
```

## tick() — One Superstep

```python
def tick(self):
    # 1. Check recursion limit → "out_of_steps"
    # 2. prepare_next_tasks() — which nodes to run
    # 3. Emit checkpoint debug event
    # 4. No tasks → "done", return False
    # 5. If resuming, match_writes() to restore previous writes
    # 6. Check interrupt_before → raise GraphInterrupt
    # 7. Emit task debug events + cached output
    # 8. Return True (more work)
```

## after_tick() — Post-Superstep

```python
def after_tick(self):
    # 1. apply_writes() — update channels
    # 2. Emit "values" stream events if output channels changed
    # 3. Clear pending writes
    # 4. Save checkpoint (source: "loop")
    # 5. Check interrupt_after → raise GraphInterrupt
```

## _first() — Initial Setup

Determines if this is a fresh start or a resume:
- **Resume**: existing checkpoint + `None` input or `RESUMING` flag
- **Command input**: maps resume values to task IDs
- **New input**: discard unfinished tasks, apply input writes, save "input" checkpoint

## put_writes() — Write Management

1. Deduplicate writes to special channels (last-write-wins)
2. For `NULL_TASK_ID`, accumulate instead of replace
3. Filter out `UntrackedValue` channel writes from persisted set
4. If durability != `"exit"`, immediately submit to checkpointer
5. Call `output_writes()` to emit stream events

## Checkpoint Ordering Guarantee

```python
def _checkpointer_put_after_previous(prev_fut, config, checkpoint, metadata, new_versions):
    prev_fut.result()  # Wait for previous checkpoint to complete
    checkpointer.put(config, checkpoint, metadata, new_versions)
```

Chained-future pattern ensures checkpointers receive saves in strict monotonic step order even when submitted concurrently.

## _suppress_interrupt() — Context Manager Exit

- If `durability == "exit"`: saves checkpoint and pending writes on exit
- Suppresses `GraphInterrupt` at top-level graph (not nested subgraphs)
- On suppress: emits final "values" event, saves final output

## DuplexStream

`StreamProtocol` that fans out to multiple streams, filtering by `stream.modes`. Modes include: values, messages, updates, events, tasks, checkpoints, debug, custom.

## Stream Modes

| Mode | Content |
|------|---------|
| `values` | Full state after each superstep |
| `updates` | Per-node output dicts |
| `messages` | LLM message tokens (streaming) |
| `debug` | Task/checkpoint debug payloads with timestamps |
| `tasks` | Task state changes |
| `checkpoints` | Checkpoint snapshots |
| `custom` | User-defined via StreamWriter |

## SyncPregelLoop / AsyncPregelLoop

Both are context managers. Enter:
1. Load checkpoint (or start with empty, step=-2)
2. Enter BackgroundExecutor
3. Initialize channels from checkpoint
4. Push `_suppress_interrupt` onto exit stack
5. Compute step/stop, call `_first()`
