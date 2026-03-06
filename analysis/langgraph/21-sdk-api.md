# LangGraph — SDK API Endpoints

## AssistantsClient

| Method | HTTP | Endpoint |
|--------|------|----------|
| `get(id)` | GET | `/assistants/{id}` |
| `get_graph(id, xray)` | GET | `/assistants/{id}/graph` |
| `get_schemas(id)` | GET | `/assistants/{id}/schemas` |
| `get_subgraphs(id, ns, recurse)` | GET | `/assistants/{id}/subgraphs` |
| `create(graph_id, config, ...)` | POST | `/assistants` |
| `update(id, ...)` | PATCH | `/assistants/{id}` |
| `delete(id, delete_threads)` | DELETE | `/assistants/{id}` |
| `search(metadata, graph_id, ...)` | POST | `/assistants/search` |
| `count(metadata, graph_id, ...)` | POST | `/assistants/count` |
| `get_versions(id, ...)` | POST | `/assistants/{id}/versions` |
| `set_latest(id, version)` | POST | `/assistants/{id}/latest` |

## ThreadsClient

| Method | HTTP | Endpoint |
|--------|------|----------|
| `get(id)` | GET | `/threads/{id}` |
| `create(...)` | POST | `/threads` |
| `update(id, ...)` | PATCH | `/threads/{id}` |
| `delete(id)` | DELETE | `/threads/{id}` |
| `search(...)` | POST | `/threads/search` |
| `count(...)` | POST | `/threads/count` |
| `copy(id)` | POST | `/threads/{id}/copy` |
| `prune(id, ...)` | POST | `/threads/{id}/prune` |
| `get_state(id, checkpoint)` | GET/POST | `/threads/{id}/state` |
| `update_state(id, values, as_node)` | POST | `/threads/{id}/state` |
| `get_history(id, limit, before)` | POST | `/threads/{id}/history` |
| `join_stream(id)` | GET | `/threads/{id}/stream` (SSE) |

## RunsClient

| Method | HTTP | Endpoint |
|--------|------|----------|
| `stream(thread_id, assistant_id, ...)` | POST (SSE) | `/threads/{id}/runs/stream` |
| `create(thread_id, assistant_id, ...)` | POST | `/threads/{id}/runs` |
| `create_batch(payloads)` | POST | `/threads/{id}/runs/batch` |
| `wait(thread_id, assistant_id, ...)` | POST | `/threads/{id}/runs/wait` |
| `get(thread_id, run_id)` | GET | `/threads/{id}/runs/{run_id}` |
| `list(thread_id, ...)` | GET | `/threads/{id}/runs` |
| `delete(thread_id, run_id)` | DELETE | `/threads/{id}/runs/{run_id}` |
| `cancel(thread_id, run_id, ...)` | POST | `/threads/{id}/runs/{run_id}/cancel` |
| `join(thread_id, run_id, ...)` | GET | `/threads/{id}/runs/{run_id}/join` |
| `bulk_cancel_runs(thread_ids)` | POST | `/runs/bulk_cancel` |

## StoreClient

| Method | HTTP | Endpoint |
|--------|------|----------|
| `put_item(ns, key, value, index, ttl)` | POST | `/store/items` |
| `get_item(ns, key, refresh_ttl)` | POST | `/store/items/get` |
| `delete_item(ns, key)` | POST | `/store/items/delete` |
| `search_items(ns_prefix, filter, query, ...)` | POST | `/store/items/search` |
| `list_namespaces(prefix, suffix, ...)` | POST | `/store/namespaces` |

## CronClient

CRUD + search for scheduled runs. Cron jobs trigger graph runs on a schedule.

## Server Runtime

```python
ServerRuntime = _ExecutionRuntime[ContextT] | _ReadRuntime[ContextT]
```

- `access_context`: `"threads.create_run"` | `"threads.update"` | `"threads.read"` | `"assistants.read"`
- `user`: Authenticated `BaseUser` or `None`
- `store`: `BaseStore` instance
- `execution_runtime`: only during `threads.create_run` (with `context: ContextT`)

Enables conditional expensive resource setup only during execution, not during schema introspection.
