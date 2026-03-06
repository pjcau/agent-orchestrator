# LangGraph — Conditional Edges & Routing

## BranchSpec

```python
class BranchSpec(NamedTuple):
    path: Runnable[Any, Hashable | list[Hashable]]  # routing function
    ends: dict[Hashable, str] | None                 # return value → node name map
    input_schema: type[Any] | None = None
```

## How Conditional Edges Work

```python
# Define a routing function
def should_continue(state: State) -> str:
    if state["messages"][-1].tool_calls:
        return "tools"
    return END

# Add conditional edge
graph.add_conditional_edges("agent", should_continue, {
    "tools": "tool_node",
    END: END,
})
```

### Runtime Flow

1. Source node completes
2. `BranchSpec._route()` invokes the path function with current state
3. `_finish()` translates results into `ChannelWriteEntry` objects
4. Writes to `branch:to:{target_node}` channels
5. Target node triggers in next superstep

## Return Type Inference

If the path function has a return type annotation, the path_map is inferred automatically:

```python
def route(state) -> Literal["nodeA", "nodeB"]:
    ...
# BranchSpec.from_path() auto-constructs path_map from Literal
```

## Multi-Target Routing

The path function can return a **list** of destinations for fan-out:

```python
def route(state) -> list[str]:
    return ["nodeA", "nodeB"]  # both run in parallel
```

## Dynamic Routing via Send

Nodes can return `Send` objects for dynamic fan-out within the same superstep:

```python
from langgraph.constants import Send

def router(state):
    return [Send("worker", {"task": t}) for t in state["tasks"]]
```

Each `Send` creates a new PUSH task that runs concurrently. The `__tasks__` channel (`Topic(Send)`) accumulates these.

## Edge Types Summary

| Type | Syntax | Behavior |
|------|--------|----------|
| Direct | `add_edge(A, B)` | A always flows to B |
| Fan-in | `add_edge([A, B], C)` | Wait for ALL, then C |
| Conditional | `add_conditional_edges(A, fn, map)` | fn determines target |
| Dynamic | Return `Send(...)` from node | Runtime fan-out |
