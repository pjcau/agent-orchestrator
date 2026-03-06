# LangGraph — Python SDK Client

## Architecture

HTTP client for the LangGraph API Server, built on `httpx`.

```
langgraph_sdk/
├── _async/          # Async implementations (primary)
│   ├── client.py    # LangGraphClient (facade)
│   ├── http.py      # HttpClient (HTTP + SSE)
│   ├── assistants.py
│   ├── threads.py
│   ├── runs.py
│   ├── store.py
│   └── cron.py
├── _sync/           # Sync mirrors (identical API)
├── _shared/         # Shared utilities, types
├── schema.py        # All TypedDicts
├── errors.py        # Typed HTTP error hierarchy
├── runtime.py       # ServerRuntime for graph factories
└── sse.py           # SSE decoder
```

## Client Construction

```python
from langgraph_sdk import get_client, get_sync_client

client = get_client(url="http://localhost:8123", api_key="...")
sync_client = get_sync_client(url="http://localhost:8123")
```

- When `url=None` → attempts in-process ASGI transport (for self-calls inside server)
- API key auto-loads: `LANGGRAPH_API_KEY` > `LANGSMITH_API_KEY` > `LANGCHAIN_API_KEY`
- Default timeouts: connect=5s, read=300s, write=300s, pool=5s

## LangGraphClient (Facade)

```python
class LangGraphClient:
    http: HttpClient
    assistants: AssistantsClient
    threads: ThreadsClient
    runs: RunsClient
    crons: CronClient
    store: StoreClient
```

Supports async context manager: `async with get_client() as client:`

## HttpClient (Transport)

Built on `httpx.AsyncClient`:
- JSON encoding/decoding via `orjson` (offloaded to executor for large payloads)
- SSE streaming with automatic reconnection (up to 5 attempts)
- `Last-Event-ID` header support for resumable streams
- `request_reconnect` follows `Location` headers for long-running operations

## Error Hierarchy

```
LangGraphError
  APIError(httpx.HTTPStatusError, LangGraphError)
    APIStatusError
      BadRequestError (400)
      AuthenticationError (401)
      PermissionDeniedError (403)
      NotFoundError (404)
      ConflictError (409)
      UnprocessableEntityError (422)
      RateLimitError (429)
      InternalServerError (5xx)
    APIResponseValidationError
    APIConnectionError
      APITimeoutError
```

## Key Schema Types (TypedDicts)

| Type | Purpose |
|------|---------|
| `Assistant` | Versioned graph config (id, graph_id, config, version) |
| `Thread` | Conversation state (id, status, values, interrupts) |
| `ThreadState` | Full snapshot (values, next, checkpoint, tasks) |
| `Run` | Single execution (id, thread_id, status, multitask_strategy) |
| `Cron` | Scheduled task (id, schedule, payload, next_run_date) |
| `Item` | Store entry (namespace, key, value, timestamps) |
| `Command` | Control flow (goto, update, resume) |
| `StreamPart` | SSE event (event, data, id) |

## Key Enums

- `StreamMode`: values/messages/updates/events/tasks/checkpoints/debug/custom
- `MultitaskStrategy`: reject/interrupt/rollback/enqueue
- `DisconnectMode`: cancel/continue
- `Durability`: sync/async/exit
