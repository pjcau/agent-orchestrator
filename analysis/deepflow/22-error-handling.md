# 22 - Error Handling

## Loop Detection (P0 Safety)

`LoopDetectionMiddleware` prevents infinite tool call loops:

```
Step 1: Hash tool calls (name + args, order-independent)
Step 2: Track in sliding window (last 20 per thread)
Step 3: At 3 identical calls → inject warning message
Step 4: At 5 identical calls → strip ALL tool_calls, force text output
```

Per-thread tracking with LRU eviction (max 100 threads). Thread-safe with locks.

Warning message:
> "[LOOP DETECTED] You are repeating the same tool calls. Stop calling tools and produce your final answer now."

Hard stop:
> "[FORCED STOP] Repeated tool calls exceeded the safety limit."

## Tool Error Handling

`ToolErrorHandlingMiddleware`:
- Catches exceptions from tool execution
- Converts to ToolMessage with error content
- Prevents agent crash from tool failures

Two variants:
- `build_lead_runtime_middlewares()` — for lead agent
- `build_subagent_runtime_middlewares()` — for sub-agents

## Dangling Tool Call Recovery

`DanglingToolCallMiddleware`:
- Scans for AIMessage `tool_calls` without matching ToolMessage responses
- Happens when user interrupts mid-execution
- Injects placeholder ToolMessages so model isn't confused
- Prevents "tool_call without result" errors

## Sandbox Error Handling

Custom exception hierarchy:
```python
SandboxError (base)
├── SandboxNotFoundError
└── SandboxRuntimeError
```

Every sandbox tool wraps operations in try/except:
```python
try:
    sandbox = ensure_sandbox_initialized(runtime)
    ...
except SandboxError as e:
    return f"Error: {e}"
except PermissionError as e:
    return f"Error: {e}"
except Exception as e:
    return f"Error: Unexpected: {type(e).__name__}: {e}"
```

## Sub-agent Error Handling

```python
class SubagentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
```

- Execution pool timeout (default 900s)
- Exception caught and stored in `SubagentResult.error`
- `asyncio.run()` failures handled separately
- All errors returned as status, never crash the lead agent

## MCP Error Handling

```python
try:
    tools = await client.get_tools()
except ImportError:
    logger.warning("langchain-mcp-adapters not installed")
    return []
except Exception as e:
    logger.error(f"Failed to load MCP tools: {e}")
    return []
```

Graceful degradation — MCP failures don't prevent agent from working.

## Memory Error Handling

- Atomic file writes (temp file + rename)
- JSON parse errors → return empty memory
- File I/O errors → log and continue
- LLM response parse errors → skip update

## Key Patterns We Should Adopt

1. **Loop detection** — we have no equivalent; agents could loop forever
2. **Dangling tool call recovery** — handles interrupted conversations gracefully
3. **Consistent error return format** — all errors as strings, never exceptions to the LLM
4. **Graceful degradation** — MCP, memory, skills failures don't crash the system
