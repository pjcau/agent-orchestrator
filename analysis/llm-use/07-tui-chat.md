# 07 - TUI Chat Mode

## Overview
llm-use includes a curses-based Terminal User Interface for interactive chat. It features a 3-pane layout with chat history, status panel, and log viewer.

## Layout (lines 1077-1156)

```
┌──────────────────────────────────────────┬──────────────┐
│ Chat History                             │ Status       │
│                                          │ Mode: ...    │
│ You: Hello                               │ Workers 3/5  │
│ AI: Hi there!                            │ [####----]   │
│ You: Compare 5 products                  │ Cost: $0.003 │
│ AI: Working...                           │ Time: 4.2s   │
│                                          │              │
├──────────────────────────────────────────┴──────────────┤
│ Logs: Working...                                        │
│ 10:30:05 [INFO] Analyzing...                           │
│ 10:30:06 [INFO] PARALLEL (5 workers)                   │
├─────────────────────────────────────────────────────────┤
│ > your input here                                       │
└─────────────────────────────────────────────────────────┘
```

## Key Implementation Details

### Event-Driven Status Updates
The TUI hooks into the orchestrator's event callback:
```python
def on_event(name, payload):
    if name == "parallel_start":
        state["workers_total"] = payload.get("workers", 0)
    elif name == "worker_done":
        state["workers_done"] += 1
```

### Threaded Execution
User input triggers a background thread:
```python
t = threading.Thread(target=worker, args=(prompt,), daemon=True)
t.start()
```
Results are passed back via `queue.Queue`.

### Log Capture
Console log handlers are replaced with a custom `UILogHandler` that feeds a `deque(maxlen=200)`:
```python
class UILogHandler(logging.Handler):
    def emit(self, record):
        with log_lock:
            log_lines.append(self.format(record))
```

### Chat History
Simple tuple list: `history: List[Tuple[str, str]]` with `("user", msg)` or `("assistant", msg)`. The `build_chat_prompt()` function converts this to a prompt string (last 6 turns).

## Controls
- Type + Enter: send message
- `/quit` or `/exit`: exit chat
- Ctrl+C: exit

## Key Patterns
- curses-based TUI with non-blocking input (`nodelay=True`)
- 20ms polling loop for responsive UI
- Thread + Queue for async LLM execution
- Custom log handler for in-TUI log display
- Progress bar for parallel worker tracking

## Relevance to Our Project
Our dashboard is web-based (FastAPI + WebSocket). The curses TUI approach is interesting for terminal-first workflows. The event callback pattern they use (`event_cb`) is similar to our `EventBus` but much simpler — a single callback function instead of pub/sub.
