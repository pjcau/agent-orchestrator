# 13 - State Management

## ThreadState

Extends LangGraph's `AgentState`:

```python
class ThreadState(AgentState):
    messages: list[BaseMessage]           # Core (from AgentState)
    sandbox: SandboxState | None          # {sandbox_id}
    thread_data: ThreadDataState | None   # {workspace, uploads, outputs paths}
    title: str | None                     # Auto-generated title
    artifacts: Annotated[list[str], merge_artifacts]     # Custom reducer
    todos: list | None                    # Plan mode tasks
    uploaded_files: list[dict] | None     # Uploaded file metadata
    viewed_images: Annotated[dict, merge_viewed_images]  # Custom reducer
```

## Custom Reducers

### merge_artifacts
```python
def merge_artifacts(existing, new):
    # Merges + deduplicates using dict.fromkeys (preserves order)
    return list(dict.fromkeys(existing + new))
```

### merge_viewed_images
```python
def merge_viewed_images(existing, new):
    # Special: empty dict {} clears all images
    if len(new) == 0:
        return {}
    return {**existing, **new}
```

## Checkpointing

Three backends:

| Type | Use Case | Persistence |
|------|----------|-------------|
| `memory` | Default | In-process only, lost on restart |
| `sqlite` | Single-process | File-based, survives restarts |
| `postgres` | Multi-process | Full persistence |

```yaml
checkpointer:
  type: sqlite
  connection_string: checkpoints.db
```

## State Flow

```
User Message → ThreadDataMiddleware (set paths)
            → UploadsMiddleware (inject files)
            → SandboxMiddleware (acquire sandbox)
            → Model (generate response)
            → TitleMiddleware (set title)
            → MemoryMiddleware (queue update)
            → Response streamed to client
```

Each middleware can read and modify state. State changes are persisted by the checkpointer.

## Per-Thread Isolation

Each thread has completely isolated:
- File directories (`backend/.deer-flow/threads/{thread_id}/`)
- Sandbox instance
- Conversation history
- Memory updates

## Comparison with Our StateGraph

| Aspect | DeerFlow | Our Orchestrator |
|--------|----------|-----------------|
| Engine | LangGraph AgentState | Custom StateGraph |
| Reducers | Custom functions | append, merge, replace, etc. |
| Channels | LangGraph built-in | Typed channels (LastValue, Topic, etc.) |
| Streaming | LangGraph SSE | Custom astream() |
| Checkpointing | SQLite/Postgres | InMemory/SQLite/Postgres |

DeerFlow uses LangGraph's state management directly. We built our own StateGraph engine with more channel types but less battle-tested.
