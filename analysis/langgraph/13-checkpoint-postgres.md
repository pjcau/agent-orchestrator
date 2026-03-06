# LangGraph — Postgres Checkpointer

## PostgresSaver (BasePostgresSaver) — Full History

Uses `psycopg` with pipeline support for batch operations.

## Migration System

`MIGRATIONS` list of SQL strings; position = version number. Tracked in `checkpoint_migrations(v INTEGER PRIMARY KEY)`. Incremental, idempotent.

## Schema (10 migrations)

```sql
-- Checkpoint headers
CREATE TABLE checkpoints (
    thread_id              TEXT NOT NULL,
    checkpoint_ns          TEXT NOT NULL DEFAULT '',
    checkpoint_id          TEXT NOT NULL,
    parent_checkpoint_id   TEXT,
    type                   TEXT,
    checkpoint             JSONB NOT NULL,  -- primitives inline, complex extracted
    metadata               JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

-- Channel value blobs (content-addressed)
CREATE TABLE checkpoint_blobs (
    thread_id     TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel       TEXT NOT NULL,
    version       TEXT NOT NULL,
    type          TEXT NOT NULL,    -- "empty" if no data
    blob          BYTEA,           -- NULL if type="empty"
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

-- Pending writes
CREATE TABLE checkpoint_writes (
    thread_id     TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id       TEXT NOT NULL,
    idx           INTEGER NOT NULL,
    channel       TEXT NOT NULL,
    type          TEXT,
    blob          BYTEA NOT NULL,
    task_path     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
```

## Blob Architecture (Key Design Decision)

`put()` splits `channel_values` into two groups:

| Value Type | Storage |
|-----------|---------|
| **Primitive** (None, str, int, float, bool) | Inline in `checkpoint` JSONB column |
| **Complex** (objects, lists, etc.) | Separate rows in `checkpoint_blobs` |

Blobs keyed by `(thread_id, ns, channel, version)`. Uses `ON CONFLICT DO NOTHING` — same version never re-written.

**Result**: Identical blobs shared across checkpoints (content-addressed). Minimal duplication.

## SELECT Query (Single Round-Trip)

```sql
SELECT thread_id, checkpoint, checkpoint_ns, checkpoint_id,
    parent_checkpoint_id, metadata,
    (SELECT array_agg(array[bl.channel::bytea, bl.type::bytea, bl.blob])
     FROM jsonb_each_text(checkpoint -> 'channel_versions')
     INNER JOIN checkpoint_blobs bl ON ...
    ) AS channel_values,
    (SELECT array_agg(array[cw.task_id::text::bytea, cw.channel::bytea, ...])
     FROM checkpoint_writes cw WHERE ...
    ) AS pending_writes
FROM checkpoints ...
```

## Metadata Filtering

Uses Postgres native JSONB containment: `metadata @> %s` with `Jsonb(filter)`. Much more efficient than SQLite's `json_extract`.

## Pipeline Support

`_cursor(pipeline=True)` wraps operations in a `psycopg` pipeline (batch multiple `executemany` in single round-trip).

## ShallowPostgresSaver (DEPRECATED)

Only keeps latest checkpoint per `(thread_id, checkpoint_ns)`. No history. Deprecated in 2.0.20 — use `PostgresSaver` with `durability='exit'`.

## Async Variants

`AsyncPostgresSaver` / `AsyncShallowPostgresSaver` — uses `psycopg.AsyncConnection`, `AsyncPipeline`, `AsyncConnectionPool`.

## Postgres Store Schema

```sql
CREATE TABLE store (
    prefix        TEXT NOT NULL,
    key           TEXT NOT NULL,
    value         JSONB NOT NULL,
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at    TIMESTAMP WITH TIME ZONE,
    ttl_minutes   REAL,
    PRIMARY KEY (prefix, key)
);
-- btree index with text_pattern_ops for prefix queries
-- pgvector VECTOR column + HNSW index for semantic search
```
