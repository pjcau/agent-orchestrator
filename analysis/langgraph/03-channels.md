# LangGraph — Channels (State Primitives)

## What Channels Are

Channels are the **state management primitives** of LangGraph. Each key in the state schema maps to exactly one channel. Three responsibilities:

1. **Store state** between supersteps
2. **Accept updates** from nodes (via `update(values)`)
3. **Trigger nodes** when their value changes

## BaseChannel Interface

```python
class BaseChannel(Generic[Value, Update, Checkpoint], ABC):
    # Read
    def get(self) -> Value                          # raises EmptyChannelError if empty
    def is_available(self) -> bool

    # Write
    def update(self, values: Sequence[Update]) -> bool  # returns True if changed
    def consume(self) -> bool                        # called after subscribed task runs
    def finish(self) -> bool                         # called when graph is about to end

    # Checkpoint
    def checkpoint(self) -> Checkpoint               # serialize current state
    def from_checkpoint(self, checkpoint) -> Self     # restore from checkpoint
    def copy(self) -> Self                           # shallow clone
```

## Channel Types

| Channel | Behavior | Multi-writer | Persists |
|---------|----------|-------------|----------|
| **LastValue** | Stores exactly one value. Error if multiple per step | No (error) | Yes |
| **LastValueAfterFinish** | Like LastValue but available only after `finish()` | No | Yes |
| **BinaryOperatorAggregate** | Folds updates via binary operator (e.g., `operator.add`). Supports `Overwrite` | Yes (folded) | Yes |
| **EphemeralValue** | One value, clears after one step. Used for triggers (`START`, `branch:to:X`) | Configurable | Cleared |
| **AnyValue** | Takes last value if multiple arrive. Clears to MISSING if no updates | Yes (last wins) | Yes |
| **Topic** | PubSub — accumulates a list. Configurable clear-per-step | Yes (appended) | Configurable |
| **NamedBarrierValue** | Waits for all named values before available. Used for fan-in joins | Yes (additive) | Yes |
| **NamedBarrierValueAfterFinish** | Barrier only available after `finish()`. Deferred fan-in | Yes | Yes |
| **UntrackedValue** | Like LastValue but `checkpoint()` returns MISSING. Never persisted | Configurable | Never |

## How Reducers Work Through Channels

When a state key has a reducer annotation:

```python
class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
```

The system creates `BinaryOperatorAggregate(list, add_messages)`. During `apply_writes()`:

```python
def update(self, values: Sequence[Value]) -> bool:
    if self.value is MISSING:
        self.value = values[0]
        values = values[1:]
    for value in values:
        self.value = self.operator(self.value, value)
    return True
```

The `Overwrite` wrapper bypasses the reducer to set an absolute value (only one per step).

## MessagesState Convenience

```python
class MessagesState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
```

The `add_messages` reducer merges by message ID — new messages appended, matching IDs replaced, `RemoveMessage` deletes by ID.

## Trigger-Based Scheduling

Nodes subscribe to trigger channels. A node runs only when its trigger channel has been updated since it last ran, tracked via `versions_seen[node_name]` compared against `channel_versions`.
