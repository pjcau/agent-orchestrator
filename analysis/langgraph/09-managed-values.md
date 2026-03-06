# LangGraph — Managed Values (Computed State)

## What They Are

Managed values are **computed state** injected into nodes but never stored in channels or checkpoints. They derive their value from runtime metadata (`PregelScratchpad`).

## Base Class

```python
class ManagedValue(ABC, Generic[V]):
    @staticmethod
    @abstractmethod
    def get(scratchpad: PregelScratchpad) -> V: ...
```

Detection: `is_managed_value(value)` → checks `isclass(value) and issubclass(value, ManagedValue)`

## Built-in Managed Values

### IsLastStep

```python
class IsLastStepManager(ManagedValue[bool]):
    @staticmethod
    def get(scratchpad: PregelScratchpad) -> bool:
        return scratchpad.step == scratchpad.stop - 1

IsLastStep = Annotated[bool, IsLastStepManager]
```

### RemainingSteps

```python
class RemainingStepsManager(ManagedValue[int]):
    @staticmethod
    def get(scratchpad: PregelScratchpad) -> int:
        return scratchpad.stop - scratchpad.step

RemainingSteps = Annotated[int, RemainingStepsManager]
```

## Usage

```python
from langgraph.managed import IsLastStep, RemainingSteps

class State(TypedDict):
    messages: Annotated[list, add_messages]
    is_last_step: IsLastStep        # True on final allowed step
    remaining_steps: RemainingSteps  # Steps before recursion limit
```

## Properties

- **Read-only** — computed on the fly
- **Never checkpointed** — not stored in channels
- **Excluded from input/output schemas** — `_add_schema(schema, allow_managed=False)`

## PregelScratchpad

The data source for managed values:

```python
@dataclass(frozen=True)
class PregelScratchpad:
    step: int                       # current step number
    stop: int                       # recursion limit
    call_counter: Callable          # incrementing IDs for sub-tasks
    interrupt_counter: Callable     # IDs for interrupt instances
    get_null_resume: Callable       # HITL resume state
    resume: Any                     # resume value
    subgraph_counter: Callable      # numbering concurrent subgraphs
```

## Anti-Stall Pattern

The `create_react_agent` uses `RemainingSteps` to gracefully degrade instead of hitting `GraphRecursionError`:

```python
if remaining_steps < 2 and has_tool_calls:
    return "Sorry, need more steps to process this request."
```
