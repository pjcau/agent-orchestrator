# LangGraph — Human-in-the-Loop (HITL)

## Overview

LangGraph provides first-class HITL support via interrupts. An agent can pause at any point, persist its state, wait for human input, and resume exactly where it left off.

## Interrupt Mechanism

### interrupt_before / interrupt_after

Specified at compile time:

```python
graph = builder.compile(
    checkpointer=saver,
    interrupt_before=["human_review"],   # pause BEFORE this node
    interrupt_after=["tool_call"],       # pause AFTER this node
)
```

### GraphInterrupt

When an interrupt is triggered:
1. `GraphInterrupt` exception raised (not treated as an error)
2. State is checkpointed
3. Interrupt payload persisted in `INTERRUPT` special channel
4. Execution pauses, returns to caller

### Resume

```python
# Resume with human input
result = graph.invoke(
    Command(resume="approved"),
    config={"configurable": {"thread_id": "thread-1"}}
)
```

The `RESUME` special channel receives the value, and execution continues from where it paused.

## Interrupt as First-Class Control Flow

- `GraphInterrupt` bypasses retry logic
- Propagates through `GraphBubbleUp` mechanism
- Caught by `_suppress_interrupt` at top-level graph
- Aggregated across concurrent tasks (multiple nodes can interrupt simultaneously)
- `INTERRUPT` and `RESUME` are special write channels that persist across invocations

## HumanInterrupt Protocol (Prebuilt)

```python
class HumanInterruptConfig(TypedDict):
    allow_ignore: bool
    allow_respond: bool
    allow_edit: bool
    allow_accept: bool

class ActionRequest(TypedDict):
    action: str
    args: dict

class HumanInterrupt(TypedDict):
    action_request: ActionRequest
    config: HumanInterruptConfig
    description: str

class HumanResponse(TypedDict):
    type: Literal["accept", "ignore", "response", "edit"]
    args: None | str | ActionRequest
```

## Command Object

```python
Command(
    resume=value,          # resume value for interrupt
    update={"key": val},   # update state
    goto="node_name",      # navigate to specific node
    send=[Send(...)],      # fan-out
    graph=Command.PARENT,  # target parent graph
)
```

## Resume Value Mapping

- Single interrupt: `Command(resume=value)` maps to the interrupted task
- Multiple interrupts: `Command(resume={interrupt_id: value, ...})` maps by ID
- Interrupt IDs are xxHash-128 hexdigests for uniqueness

## v2 Tool Execution + HITL

In v2 mode, each tool call is a separate `Send` target. This enables per-tool-call human review:

```python
# Graph pauses before each tool_call
graph = create_react_agent(
    model, tools,
    interrupt_before=["tools"],
    version="v2",
)
```

## Persistence Requirements

HITL requires a checkpointer — without persistence, the agent cannot resume after interrupting.
