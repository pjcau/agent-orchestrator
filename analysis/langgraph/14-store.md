# LangGraph — Store (Long-Term Memory)

## Purpose

Store provides **cross-thread persistent key-value storage** — long-term memory that persists across conversations and threads, unlike checkpoints which are per-thread.

## Item Model

```python
class Item:
    __slots__ = ("value", "key", "namespace", "created_at", "updated_at")
    value: dict[str, Any]        # stored data; keys are filterable
    key: str                     # unique within namespace
    namespace: tuple[str, ...]   # hierarchical path, e.g. ("users", "user123")
    created_at: datetime
    updated_at: datetime

class SearchItem(Item):
    score: float | None          # for vector/semantic search results
```

## Operation Types (NamedTuples)

```python
GetOp(namespace, key, refresh_ttl=True)
PutOp(namespace, key, value, index, ttl)    # value=None means DELETE
SearchOp(namespace_prefix, filter, limit, offset, query, refresh_ttl)
ListNamespacesOp(match_conditions, max_depth, limit, offset)
```

`Op = GetOp | SearchOp | PutOp | ListNamespacesOp`

## Filter Operators

```python
filter = {
    "field": {"$eq": "value"},
    "count": {"$gt": 10},
    "status": {"$ne": "deleted"},
}
```

Supported: `$eq`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte`

## BaseStore Interface

```python
class BaseStore(ABC):
    # Required
    def batch(ops: Iterable[Op]) -> list[Result]
    async def abatch(ops) -> list[Result]

    # Convenience (delegate to batch)
    def get(namespace, key) -> Item | None
    def search(namespace_prefix, *, query, filter, limit, offset) -> list[SearchItem]
    def put(namespace, key, value, index, *, ttl) -> None
    def delete(namespace, key) -> None
    def list_namespaces(*, prefix, suffix, max_depth, limit, offset) -> list[tuple]

    # Async mirrors
    async def aget(...), asearch(...), aput(...), adelete(...), alist_namespaces(...)
```

## AsyncBatchedBaseStore

Wraps any BaseStore for automatic batching of async operations:
- Uses `asyncio.Queue` with background `asyncio.Task`
- Operations enqueued in same event-loop tick are deduplicated (`_dedupe_ops`)
- Dispatched together via `abatch`

## TTL & Index Support

- `TTLConfig` — time-to-live for items
- `IndexConfig` — vector search configuration:
  - `dims`: embedding vector dimensions
  - `embed`: model identifier or custom function path
  - `fields`: JSON fields to extract before embedding

## Injection into Nodes

Store is injected via `InjectedStore` annotation in tool parameters:

```python
from langgraph.prebuilt import InjectedStore

def my_tool(query: str, store: Annotated[BaseStore, InjectedStore]) -> str:
    items = store.search(("memories",), query=query)
    return str(items)
```

## Store vs Checkpoint

| Aspect | Checkpoint | Store |
|--------|-----------|-------|
| Scope | Per-thread (conversation) | Cross-thread (global) |
| Content | Full channel state snapshot | Key-value items |
| Access | Automatic (Pregel manages) | Explicit API calls |
| Search | By metadata filter | By filter + semantic query |
| Use case | Conversation memory, resume | User profiles, knowledge base |
