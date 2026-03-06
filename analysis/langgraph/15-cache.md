# LangGraph — Cache System

## Purpose

Caches task results to avoid re-executing nodes with identical inputs. Tasks with a `cache_key` are checked against cache before execution.

## BaseCache[ValueT] Interface

```python
class BaseCache(ABC, Generic[ValueT]):
    def get(keys: Sequence[FullKey]) -> dict[FullKey, ValueT]
    async def aget(keys) -> dict[FullKey, ValueT]

    def set(pairs: Mapping[FullKey, tuple[ValueT, int | None]]) -> None  # int = TTL seconds
    async def aset(pairs) -> None

    def clear(namespaces: Sequence[Namespace] | None = None) -> None
    async def aclear(namespaces) -> None
```

`FullKey = tuple[Namespace, str]` where `Namespace = tuple[str, ...]`

Default serde: `JsonPlusSerializer(pickle_fallback=False)`

## InMemoryCache

```python
# Internal structure:
dict[Namespace, dict[str, tuple[str, bytes, float | None]]]
# namespace → key → (encoding, serialized_bytes, expiry_timestamp)
```

- Thread-safe via `threading.RLock`
- Expiry checked on `get()`
- Async methods delegate synchronously

## RedisCache

- Key format: `"{prefix}{ns_part1}:{ns_part2}:{key}"` (prefix defaults to `"langgraph:cache:"`)
- Values stored as `b"{encoding}:{data}"`
- Uses `MGET` for batch get, `pipeline()` for batch set
- TTL handled with `SETEX`
- Failures silently swallowed
- Currently sync-only (async delegate to sync)

## Cache Integration in Pregel Loop

### Check Phase (`match_cached_writes`)

Before executing tasks:
1. Look up `(ns, key)` pairs from cache for tasks with `cache_key` and no existing writes
2. Tasks with cache hits skip execution entirely
3. Their writes are populated from cache

### Write Phase (`put_writes`)

After successful execution:
- Save task writes to cache (excluding `INTERRUPT` and `ERROR`)
- Cache writes happen asynchronously via `submit`

## Cache Key Generation (`_cache.py`)

```python
def default_cache_key(*args, **kwargs) -> bytes:
    frozen = _freeze(args, kwargs)  # recursively convert unhashable → hashable
    return pickle.dumps(frozen, protocol=5)

def _freeze(obj):
    # dict → frozenset of items
    # list → tuple of frozen items
    # numpy array → (dtype, shape, bytes)
    # depth limit: 10
```

## CachePolicy

Applied per-node via `cache_policy` parameter:
```python
graph.add_node("my_node", func, cache_policy=CachePolicy(...))
```
