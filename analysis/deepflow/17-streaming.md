# 17 - Streaming

## SSE Protocol

DeerFlow uses LangGraph's SSE streaming protocol:

### Event Types

| Event | Purpose | Data |
|-------|---------|------|
| `values` | Full state snapshot | title, messages, artifacts |
| `messages-tuple` | Per-message update | AI text, tool calls, tool results |
| `end` | Stream finished | Empty |

## Backend Streaming

### LangGraph Server
The LangGraph server handles SSE natively via `langgraph dev`.

### Embedded Client
```python
# DeerFlowClient.stream()
for event in client.stream("hello"):
    if event.type == "messages-tuple" and event.data.get("type") == "ai":
        print(event.data["content"])
```

### Sub-agent Streaming
Sub-agents use `astream(state, stream_mode="values")`:
```python
async for chunk in agent.astream(state, config=run_config, stream_mode="values"):
    # Extract AI messages as they appear
    last_message = chunk["messages"][-1]
    if isinstance(last_message, AIMessage):
        result.ai_messages.append(last_message.model_dump())
```

## Frontend Streaming

### API Client
```typescript
// core/api/api-client.ts
// Uses @langchain/langgraph-sdk for SSE connection
```

### streamdown Library
Custom markdown streaming renderer:
```typescript
// core/streamdown/index.ts + plugins.ts
// Progressive markdown rendering for streaming content
```

Features:
- Incremental markdown parsing
- Code block syntax highlighting during stream
- LaTeX rendering (KaTeX)
- Mermaid diagram support
- Citation link extraction

## IM Channel Streaming

Different strategies per channel:

| Channel | Method | Behavior |
|---------|--------|----------|
| Feishu | `runs.stream()` | Accumulate text, patch card in-place |
| Slack | `runs.wait()` | Wait for completion, post result |
| Telegram | `runs.wait()` | Wait for completion, send message |

Feishu is unique: creates a "running" reply card, then patches the same card with streaming updates (min 0.35s between updates).

## Key Insight

DeerFlow delegates all streaming to LangGraph's built-in capabilities. Our orchestrator implements custom `astream()` with `StreamEvent`, which gives more control but requires more maintenance.

The `streamdown` library is interesting — it handles the notoriously tricky problem of rendering markdown while it's still being streamed (partial code blocks, tables, etc.).
