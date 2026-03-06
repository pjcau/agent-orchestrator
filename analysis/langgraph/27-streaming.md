# LangGraph — Streaming

## Stream Modes

| Mode | Content | Use Case |
|------|---------|----------|
| `values` | Full state after each superstep | State snapshots |
| `updates` | Per-node output dicts | Incremental changes |
| `messages` | LLM message tokens | Chat UI streaming |
| `debug` | Task/checkpoint payloads with timestamps | Debugging |
| `tasks` | Task state changes | Progress tracking |
| `checkpoints` | Checkpoint snapshots | State persistence monitoring |
| `custom` | User-defined via StreamWriter | Application-specific |

## DuplexStream

`StreamProtocol` that fans out to multiple streams, filtering by `stream.modes`.

## StreamMessagesHandler

`BaseCallbackHandler` for `stream_mode=messages`:

- Intercepts: `on_chat_model_start`, `on_llm_new_token`, `on_llm_end`, `on_llm_error`, `on_chain_start`, `on_chain_end`
- Tracks `run_id → (namespace_tuple, metadata)`
- Deduplicates messages by `message.id` using `self.seen`
- `subgraphs=True` enables streaming from nested subgraphs
- `TAG_NOSTREAM` suppresses streaming from tagged LLM runs
- `TAG_HIDDEN` suppresses message collection from tagged nodes
- Handles `Command` responses by scanning `response.update` for messages
- `run_inline = True` for main-thread execution (no race conditions)

## StreamWriter (Custom Streaming)

Nodes can emit custom stream events:

```python
def my_node(state, *, writer: StreamWriter):
    writer("Processing step 1...")
    # ... do work
    writer("Step 1 complete.")
    return {"result": "done"}
```

Custom events appear in `stream_mode="custom"`.

## SDK SSE Streaming

The Python SDK streams via Server-Sent Events:
- Automatic reconnection (up to 5 attempts)
- `Last-Event-ID` header for resumable streams
- `Location` header following for long-running operations

```python
async for event in client.runs.stream(thread_id, assistant_id, input=...):
    if event.event == "values":
        print(event.data)
    elif event.event == "messages/partial":
        print(event.data, end="", flush=True)
```

## Debug Output

`map_debug_tasks(tasks)` → `TaskPayload` dicts
`map_debug_checkpoint(...)` → `CheckpointPayload` with:
- Current channel values
- Metadata
- Per-task state (including nested subgraph config references)

`tasks_w_writes(tasks, pending_writes, ...)` → Merges pending writes into task representations. Multiple writes to same channel aggregated as `{"$writes": [...]}`.

## Output Emission Flow

```
output_writes(task_id, writes, cached)
  │
  ├── INTERRUPT writes → emit "updates" and/or "values"
  │   (skip PUSH tasks where call=True)
  │
  ├── Normal writes → emit "updates" via map_output_updates
  │
  └── Always emit "tasks" (debug) for non-cached writes

_emit(mode, values_fn, *args)
  │
  ├── "checkpoints"/"tasks" → remapped to "debug" with timestamp wrapper
  └── Only call values_fn if mode is registered in stream.modes
```
