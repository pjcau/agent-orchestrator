# 05 - Cache & Session Management

## Overview
llm-use uses SQLite for response/scrape caching and JSON files for session persistence. Both are designed for local single-user operation.

## Cache System (SQLite)

The `Cache` class (lines 280-375) manages three SQLite tables in `~/.llm-use/cache.sqlite`:

### LLM Response Cache
```sql
CREATE TABLE llm_cache (
    key TEXT PRIMARY KEY,        -- MD5(provider:model:max_tokens:temp:prompt)
    provider TEXT,
    model TEXT,
    prompt_hash TEXT,            -- MD5(prompt) for reference
    response TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    created REAL                 -- Unix timestamp
)
```
- Cache key includes temperature and max_tokens (same prompt, different params = different cache entries)
- No TTL or expiration — entries persist forever
- Can be disabled with `--no-cache`

### Scrape Cache
```sql
CREATE TABLE scrape_cache (
    key TEXT PRIMARY KEY,        -- MD5(url)
    url TEXT,
    content TEXT,               -- Extracted text (max 4000 chars)
    created REAL
)
```
- Caches scraped web content to avoid repeated fetches
- No expiration — stale content will be served indefinitely

### Router Examples (Learning Data)
```sql
CREATE TABLE router_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT,
    mode TEXT,                  -- "single" or "parallel"
    created REAL,
    confidence REAL             -- Added via ALTER TABLE migration
)
```
- Max 500 rows (old entries pruned on insert)
- Used by the learned router for similarity matching

### Thread Safety
- `threading.Lock()` protects all SQLite operations
- `check_same_thread=False` enables cross-thread access

## Session Management

The `SessionManager` class (lines 428-482) persists execution sessions:

### Storage Format
Sessions are saved as JSON files at `~/.llm-use/sessions/{session_id}.json`:
```json
{
  "id": "abc123",
  "task": "Compare 5 products",
  "mode": "parallel",
  "orchestrator_call": { "id": "...", "model": "...", "cost": 0.003, ... },
  "worker_calls": [ ... ],
  "synthesis_call": { ... },
  "output": "Final answer...",
  "total_cost": 0.007,
  "total_duration": 8.2,
  "created": "2025-01-15T10:30:00",
  "completed": "2025-01-15T10:30:08"
}
```

### Session Lifecycle
1. `create()` — Initialize with task, mode, and orchestrator call
2. `add_worker()` — Append worker calls (accumulates cost/duration)
3. `add_synthesis()` — Add synthesis call
4. `complete()` — Save to JSON file, log total cost

### Limitations
- No session indexing (scans directory for `*.json`)
- No concurrent access protection on session files
- `load_recent()` reads ALL files then sorts — O(n) on session count
- Deserialization rebuilds entire object graph from JSON

## Key Patterns
- SQLite for structured cache data; JSON files for session history
- Thread-safe cache but not concurrent-safe sessions
- No cache eviction or TTL policies
- Schema migration via `ALTER TABLE` with exception swallowing

## Relevance to Our Project
Our project uses PostgreSQL for persistence and InMemoryCache for LLM responses. Their SQLite approach is zero-config and works offline, which is appealing for local-first tools. The lack of TTL/eviction is a clear gap compared to our TTL-based `InMemoryCache` and `BaseStore`.
