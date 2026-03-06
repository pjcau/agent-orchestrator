# LangGraph — Architecture & Design Philosophy

## Core Design Principles

1. **Graph-as-state-machine** — Agents are modeled as `StateGraph` with explicit nodes and conditional edges
2. **BSP execution** — Bulk Synchronous Parallel model with supersteps (plan → execute → update)
3. **Channel-based state** — Every state key maps to a typed channel that determines concurrency semantics
4. **Dual API surface** — Declarative (StateGraph) and imperative (@entrypoint/@task) compile to same runtime
5. **Write buffering** — All node outputs buffered during execution, applied atomically at step boundary

## Key Abstractions

| Abstraction | Role |
|-------------|------|
| **StateGraph** | Builder class — defines nodes, edges, state schema |
| **CompiledStateGraph** | Executable graph (extends Pregel) |
| **Pregel** | Runtime engine — superstep execution, channel management |
| **Channel** | State primitive — stores values, triggers nodes, manages concurrency |
| **Checkpoint** | Persistence — snapshot of all channel state at a step |
| **Store** | Long-term memory — cross-thread persistent key-value storage |
| **PregelNode** | Actor — reads channels, runs callable, writes channels |
| **ManagedValue** | Computed state — injected into nodes but never persisted |

## Execution Flow

```
Input → START channel → Pregel Loop:
  ┌─────────────────────────────────────┐
  │ 1. PLAN: Which nodes are triggered? │
  │    (check channel versions_seen)    │
  │ 2. EXECUTE: Run all triggered nodes │
  │    (parallel, writes buffered)      │
  │ 3. UPDATE: Apply writes to channels │
  │    (reducers fold concurrent writes)│
  │ 4. CHECKPOINT: Save state           │
  └─────────────────────────────────────┘
  Repeat until no nodes triggered or recursion limit
→ Output from END channel
```

## Config as Universal Injection Bus

The `config["configurable"]` dict injects runtime capabilities into nodes:

| Key | Injected Value |
|-----|---------------|
| `CONFIG_KEY_READ` | Channel read function |
| `CONFIG_KEY_SEND` | Channel write function |
| `CONFIG_KEY_CALL` | Submit sub-task function |
| `CONFIG_KEY_CHECKPOINTER` | Checkpointer instance |
| `CONFIG_KEY_STREAM` | Stream writer |
| `CONFIG_KEY_SCRATCHPAD` | Per-task mutable state |
| `CONFIG_KEY_RUNTIME` | Full Runtime object |
| `CONFIG_KEY_STORE` | BaseStore instance |

This avoids threading dependencies through function signatures at every level.

## String Interning

All internal string keys are `sys.intern()`-ed for identity-based dict lookups and zero string allocation overhead in hot paths.
