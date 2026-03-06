# LangGraph — SQLite Checkpointer

## SqliteSaver (BaseCheckpointSaver[str])

Thread-safe via `threading.Lock` around all cursor operations. WAL mode enabled.

## Schema

```sql
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id              TEXT NOT NULL,
    checkpoint_ns          TEXT NOT NULL DEFAULT '',
    checkpoint_id          TEXT NOT NULL,
    parent_checkpoint_id   TEXT,
    type                   TEXT,          -- "msgpack", "null", etc.
    checkpoint             BLOB,          -- full serialized Checkpoint dict
    metadata               BLOB,          -- JSON-encoded CheckpointMetadata
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS writes (
    thread_id              TEXT NOT NULL,
    checkpoint_ns          TEXT NOT NULL DEFAULT '',
    checkpoint_id          TEXT NOT NULL,
    task_id                TEXT NOT NULL,
    idx                    INTEGER NOT NULL,
    channel                TEXT NOT NULL,
    type                   TEXT,
    value                  BLOB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
```

## Key Behaviors

| Operation | Implementation |
|-----------|---------------|
| `get_tuple` | Fetch by exact `checkpoint_id` or latest (`ORDER BY checkpoint_id DESC LIMIT 1`), then join pending writes |
| `list` | Parameterized WHERE via `search_where()`, uses `json_extract(CAST(metadata AS TEXT), '$.key')` for metadata filtering |
| `put` | `INSERT OR REPLACE` with JSON-serialized metadata |
| `put_writes` | `INSERT OR REPLACE` for special channels, `INSERT OR IGNORE` for regular (first-write-wins) |
| `delete_thread` | Cascade DELETE to both tables by `thread_id` |

## Version Format

```python
def get_next_version(current, channel):
    return f"{current_v+1:032}.{random():016}"
    # Zero-padded int + random float for uniqueness
```

## SQLite Store Schema

```sql
CREATE TABLE IF NOT EXISTS store (
    prefix        TEXT NOT NULL,     -- dot-joined namespace
    key           TEXT NOT NULL,
    value         TEXT NOT NULL,     -- JSON
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at    TIMESTAMP,         -- TTL support
    ttl_minutes   REAL,
    PRIMARY KEY (prefix, key)
);

-- Optional vector extension (sqlite-vec)
CREATE TABLE IF NOT EXISTS store_vectors (
    prefix        TEXT NOT NULL,
    key           TEXT NOT NULL,
    field_name    TEXT NOT NULL,
    embedding     BLOB,
    PRIMARY KEY (prefix, key, field_name)
);
```

## Design Note

SQLite stores the **full serialized Checkpoint dict** per row (including all channel values). No blob deduplication — simpler but more storage than Postgres approach.

## Limitations

- Sync-only — async methods raise `NotImplementedError` (use `AsyncSqliteSaver`)
- No blob normalization (every checkpoint duplicates unchanged channel values)
