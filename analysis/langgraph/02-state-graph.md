# LangGraph — StateGraph (Builder)

## Class Hierarchy

```
StateGraph[StateT, ContextT, InputT, OutputT]    # Builder (not executable)
    │
    │  .compile()
    ▼
CompiledStateGraph[StateT, ContextT, InputT, OutputT]
    extends Pregel[StateT, ContextT, InputT, OutputT]    # Executable runtime
```

## Type Parameters

- `StateT` — Full state schema (typically a TypedDict)
- `ContextT` — Optional immutable context schema
- `InputT` — Input schema (defaults to StateT)
- `OutputT` — Output schema (defaults to StateT)

## Internal Data Structures

```python
edges: set[tuple[str, str]]                        # direct edges
nodes: dict[str, StateNodeSpec[Any, ContextT]]     # node specs
branches: defaultdict[str, dict[str, BranchSpec]]  # conditional edges
channels: dict[str, BaseChannel]                   # derived from schemas
managed: dict[str, ManagedValueSpec]               # managed values
waiting_edges: set[tuple[tuple[str, ...], str]]    # fan-in edges
```

## State Schema → Channel Derivation

When a TypedDict is passed to `StateGraph(state_schema=...)`, each field is analyzed:

| Field Type | Channel Created |
|-----------|----------------|
| `x: int` (plain) | `LastValue(int)` — single writer per step |
| `x: Annotated[list, operator.add]` (reducer) | `BinaryOperatorAggregate(list, operator.add)` — multi-writer, folded |
| `x: Annotated[str, EphemeralValue]` (channel class) | Direct channel instantiation |
| `x: Annotated[bool, IsLastStepManager]` (managed) | Registered as managed value, not a channel |

## Adding Nodes

```python
graph.add_node("name", callable,
    input_schema=...,       # restrict which state keys the node reads
    retry_policy=...,       # RetryPolicy or list thereof
    cache_policy=...,       # CachePolicy
    defer=...,              # deferred execution (after main loop)
    destinations=...,       # hint for edge inference
    metadata=...            # arbitrary metadata dict
)
```

Node callables support various signatures via Protocol types:

```python
(state) -> Any                           # basic
(state, config) -> Any                   # with config
(state, *, writer) -> Any                # with stream writer
(state, *, store) -> Any                 # with store
(state, *, runtime) -> Any               # with full runtime
Runnable[InputT, Any]                    # LangChain Runnable
```

## Adding Edges

```python
# Direct edge
graph.add_edge("A", "B")

# Fan-in (wait for ALL of A and B before running C)
graph.add_edge(["A", "B"], "C")

# Conditional edge
graph.add_conditional_edges("source", path_function, path_map)
```

## START and END Sentinels

```python
START = sys.intern("__start__")   # Entry point — where input is injected
END = sys.intern("__end__")       # Terminal condition
```

## compile() — What It Produces

1. **Validates** — START has edges, all nodes exist, no cycles without reducers
2. **Creates channels**:
   - `START` → `EphemeralValue(input_schema)`
   - Per node → `branch:to:{node}` EphemeralValue trigger
   - Fan-in → `join:{A+B}:{C}` NamedBarrierValue
   - `__tasks__` → `Topic(Send)` for dynamic parallelism
3. **Wraps nodes** as `PregelNode` with triggers, channels, writers, mapper
4. **Wires edges** as `ChannelWrite` operations on node writers
5. Returns `CompiledStateGraph` (a `Pregel` instance)
