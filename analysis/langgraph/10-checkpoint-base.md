# LangGraph — Checkpoint Data Model & Interface

## Checkpoint (TypedDict)

The fundamental state snapshot written at each step:

```python
class Checkpoint(TypedDict):
    v: int                            # Schema version (currently 2)
    id: str                           # UUIDv6 (monotonically increasing)
    ts: str                           # ISO 8601 UTC timestamp
    channel_values: dict[str, Any]    # Deserialized channel snapshots
    channel_versions: ChannelVersions # dict[str, str|int|float] per channel
    versions_seen: dict[str, ChannelVersions]  # Per-node: last seen versions
    updated_channels: list[str] | None  # Which channels changed
    # implicit: pending_sends (list of Send objects)
```

## CheckpointMetadata (TypedDict)

```python
class CheckpointMetadata(TypedDict, total=False):
    source: Literal["input", "loop", "update", "fork"]
    step: int           # -1 for input, 0 for first loop, n thereafter
    parents: dict       # namespace -> checkpoint_id
    run_id: str         # ID of invoking run
    # + any extra string/int/float/bool keys from config
```

## CheckpointTuple (NamedTuple)

```python
class CheckpointTuple(NamedTuple):
    config: RunnableConfig
    checkpoint: Checkpoint
    metadata: CheckpointMetadata
    parent_config: RunnableConfig | None = None
    pending_writes: list[PendingWrite] | None = None
```

`PendingWrite = tuple[str, str, Any]` → `(task_id, channel_name, deserialized_value)`

## BaseCheckpointSaver[V] Interface

Generic over `V` — version number type (`int`, `float`, `str`).

### Required Methods (Sync)

```python
def get_tuple(config) -> CheckpointTuple | None
def list(config, *, filter, before, limit) -> Iterator[CheckpointTuple]
def put(config, checkpoint, metadata, new_versions) -> RunnableConfig
def put_writes(config, writes, task_id, task_path="") -> None
def delete_thread(thread_id) -> None
```

### Optional Methods

```python
def delete_for_runs(run_ids) -> None
def copy_thread(source_thread_id, target_thread_id) -> None
def prune(thread_ids, *, strategy="keep_latest") -> None
```

### Async Interface

Mirrors exactly: `aget_tuple`, `alist`, `aput`, `aput_writes`, `adelete_thread`, etc.

### Convenience

```python
def get(config) -> Checkpoint | None       # delegates to get_tuple
def get_next_version(current, channel) -> V  # default: integer increment
def with_allowlist(extra_allowlist) -> BaseCheckpointSaver  # clone with msgpack allowlist
```

## ID Generation (UUIDv6)

`uuid6(node, clock_seq)` — time-ordered UUID variant. `clock_seq` receives the current `step` number, making checkpoint IDs both globally unique and monotonically increasing.

`ORDER BY checkpoint_id DESC` → newest first (no separate timestamp index needed).

## Special Write Channels

```python
WRITES_IDX_MAP = {
    ERROR:     -1,
    SCHEDULED: -2,
    INTERRUPT: -3,
    RESUME:    -4,
}
```

Negative indices prevent collision with regular write indices.

## Write Semantics

| Channel Type | On Conflict |
|-------------|-------------|
| Special (ERROR, INTERRUPT, etc.) | Last-write-wins (REPLACE) |
| Regular | First-write-wins (IGNORE) — idempotent |
