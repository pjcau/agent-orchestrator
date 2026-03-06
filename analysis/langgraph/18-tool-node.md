# LangGraph — ToolNode Implementation

## ToolNode (RunnableCallable)

```python
class ToolNode(RunnableCallable):
    def __init__(
        self,
        tools: Sequence[BaseTool | Callable],
        *,
        name: str = "tools",
        handle_tool_errors: bool | str | Callable | type[Exception] | tuple[type[Exception], ...],
        messages_key: str = "messages",
        wrap_tool_call: ToolCallWrapper | None = None,
    )
```

## Dependency Injection System

### InjectedState
Injects graph state (whole or specific field) into tool parameters.

### InjectedStore
Injects persistent `BaseStore` into tool parameters.

### ToolRuntime
Bundles all injectable context into one object:
```python
ToolRuntime:
    state: Any
    context: Any
    config: RunnableConfig
    stream_writer: StreamWriter
    tool_call_id: str
    store: BaseStore | None
```

## Tool Call Interceptors (Middleware)

```python
ToolCallWrapper = Callable[
    [ToolCallRequest, Callable[[ToolCallRequest], ToolMessage | Command]],
    ToolMessage | Command,
]
```

Enables: retry logic, caching, request modification, short-circuiting.

### ToolCallRequest

Immutable-style request with `override(**kwargs)` for creating modified copies (no direct mutation).

## Error Handling

Highly configurable `handle_tool_errors`:

| Type | Behavior |
|------|----------|
| `bool` | True: catch all, return error message |
| `str` | Return this string on any error |
| `Callable` | Custom handler; annotations inspected for exception types |
| `type[Exception]` | Only catch this type |
| `tuple[type, ...]` | Catch any of these types |

Custom callables: `_infer_handled_types` inspects parameter annotations to determine handled exceptions.

## ToolCallWithContext

Internal structure for v2 mode:

```python
class ToolCallWithContext(TypedDict):
    tool_call: ToolCall
    state: dict        # snapshot of state at tool call time
    # ... context fields
```

Used by the `Send` API to distribute individual tool calls across separate ToolNode instances.

## Command-Based Control Flow

Tools can return `Command` objects to:
- Update state directly
- Navigate to other nodes
- Trigger sends (fan-out)

## ValidationNode

Validates tool calls against Pydantic schemas **without executing them**:
- Accepts `BaseModel`, `BaseTool`, or plain functions
- Returns `ToolMessage` with validated JSON or error message
- Sets `is_error` in `additional_kwargs`
- Useful for extraction workflows

## HumanInterrupt System

```python
class HumanInterrupt(TypedDict):
    action_request: ActionRequest    # action: str, args: dict
    config: HumanInterruptConfig     # allow_ignore, allow_respond, allow_edit, allow_accept
    description: str

class HumanResponse(TypedDict):
    type: Literal["accept", "ignore", "response", "edit"]
    args: None | str | ActionRequest
```
