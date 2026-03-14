# 25 - Embedded Client

## DeerFlowClient

Direct in-process access to all DeerFlow capabilities — no HTTP services needed.

```python
from deerflow.client import DeerFlowClient

client = DeerFlowClient()

# Simple chat
response = client.chat("Analyze this paper", thread_id="my-thread")

# Streaming
for event in client.stream("hello"):
    if event.type == "messages-tuple":
        print(event.data["content"])
```

## API Surface

### Agent Conversation
| Method | Purpose |
|--------|---------|
| `chat(message, thread_id)` | Synchronous, returns final text |
| `stream(message, thread_id)` | Yields StreamEvent (SSE protocol) |
| `reset_agent()` | Force agent recreation |

### Gateway Equivalent Methods
| Category | Methods |
|----------|---------|
| Models | `list_models()`, `get_model(name)` |
| MCP | `get_mcp_config()`, `update_mcp_config(servers)` |
| Skills | `list_skills()`, `get_skill()`, `update_skill()`, `install_skill()` |
| Memory | `get_memory()`, `reload_memory()`, `get_memory_config/status()` |
| Uploads | `upload_files()`, `list_uploads()`, `delete_upload()` |
| Artifacts | `get_artifact()` → `(bytes, mime_type)` |

## Design Principles

1. **Same code path**: Uses same `deerflow` modules as LangGraph Server and Gateway
2. **Same config**: Shares config.yaml and data directories
3. **No FastAPI dependency**: Pure Python, no HTTP overhead
4. **Gateway conformance**: Return types match HTTP API response schemas
5. **Lazy agent creation**: Agent created on first use, cached until config changes

## Gateway Conformance Tests

```python
class TestGatewayConformance:
    """Validate client returns match Gateway Pydantic models."""

    def test_models_list(self):
        result = client.list_models()
        ModelsListResponse(**result)  # Raises if schema drift
```

CI catches drift between embedded client and HTTP API.

## Checkpointer Support

```python
client = DeerFlowClient(checkpointer=SqliteSaver("checkpoints.db"))
# Multi-turn conversations now persist across process restarts
```

Without checkpointer, each call is stateless (thread_id only used for file isolation).

## Key Insight

This is a pattern we should consider — our orchestrator requires running the full dashboard service. An embedded client would enable:
- Python scripts using agents directly
- Jupyter notebook integration
- CLI tools
- Testing without HTTP overhead
- Library distribution (pip install)
