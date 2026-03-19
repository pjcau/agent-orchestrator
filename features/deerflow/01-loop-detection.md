# Feature: Loop Detection Middleware

## Context

From DeerFlow analysis (analysis/deepflow/05-middleware-chain.md, 22-error-handling.md, 29-learnings.md L1).
Agents can burn unlimited tokens by repeating the same tool calls in an infinite loop. This is a P0 safety feature we're missing.

## What to Build

Implement a `LoopDetectionMiddleware` in `src/agent_orchestrator/core/loop_detection.py`:

### Core Logic

1. **Tool call hashing**: Hash each tool call (tool_name + parameters) using a fast hash (e.g., hashlib.md5 of JSON-serialized call).
2. **Sliding window tracker**: Per-session `deque(maxlen=20)` storing recent tool call hashes.
3. **Warn threshold**: When the same hash appears **3 times** in the window, emit a WARNING log + event (`loop.warning`).
4. **Hard stop threshold**: When the same hash appears **5 times**, raise a `LoopDetectedError` (new exception class) + emit `loop.hard_stop` event.
5. **LRU eviction**: Use `collections.OrderedDict` or similar — max 500 sessions tracked, evict oldest.

### Integration Points

- **Agent.execute()** in `src/agent_orchestrator/core/agent.py`: Wrap each tool/skill call through the loop detector before execution.
- **Dashboard events**: Emit `loop.warning` and `loop.hard_stop` via the existing EventBus (`src/agent_orchestrator/dashboard/events.py`).
- **Metrics**: Increment `loop_warnings_total` and `loop_hard_stops_total` counters in `src/agent_orchestrator/core/metrics.py`.

### API

```python
class LoopDetector:
    def __init__(self, warn_threshold: int = 3, stop_threshold: int = 5, window_size: int = 20):
        ...

    def check(self, session_id: str, tool_name: str, params: dict) -> LoopStatus:
        """Returns LoopStatus.OK, LoopStatus.WARNING, or LoopStatus.HARD_STOP"""
        ...

    def reset(self, session_id: str) -> None:
        """Clear tracking for a session"""
        ...
```

### Edge Cases

- Different parameter order should produce the same hash (sort keys before hashing)
- Empty params dict should be handled
- Session cleanup on session end (prevent memory leaks)

## Files to Modify

- **Create**: `src/agent_orchestrator/core/loop_detection.py`
- **Modify**: `src/agent_orchestrator/core/agent.py` (integrate check before each tool call)
- **Modify**: `src/agent_orchestrator/dashboard/events.py` (add loop event types)
- **Modify**: `src/agent_orchestrator/core/metrics.py` (add loop counters)

## Tests

- Test warn at 3 identical calls
- Test hard stop at 5 identical calls
- Test different params produce different hashes
- Test same params in different order produce same hash
- Test LRU eviction at 500 sessions
- Test reset clears session tracking
- Test integration with Agent.execute()

## Acceptance Criteria

- [ ] `LoopDetector` class with warn/stop thresholds
- [ ] Integrated into Agent.execute() tool call path
- [ ] Events emitted to dashboard EventBus
- [ ] Prometheus metrics registered
- [ ] All tests pass
- [ ] Existing tests still pass
