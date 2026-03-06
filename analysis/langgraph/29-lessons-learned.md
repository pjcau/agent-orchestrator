# LangGraph — Key Takeaways for Our Orchestrator

## 1. State Management: Adopt Channels with Reducers

LangGraph's biggest insight: **each state field is a typed channel** with explicit concurrency semantics. When multiple agents write to the same field:

- `LastValue` → error (single writer)
- `BinaryOperatorAggregate` → fold via reducer (e.g., `operator.add`)
- `Topic` → append all

**Action**: Extend our `Agent` state to use typed channels. This would solve concurrent state updates in multi-agent cooperation.

## 2. Interrupt/Resume is Essential for Production

LangGraph treats interrupts as **first-class control flow**, not errors:
- Persists state at interrupt point
- Supports `Command(resume=value)` to continue
- Multiple concurrent interrupts aggregated

**Action**: Add `interrupt()` and `resume()` to our orchestrator. Required for human-in-the-loop workflows.

## 3. Separate Checkpoint from Store

LangGraph cleanly separates:
- **Checkpoint** = per-thread conversation state (automatic)
- **Store** = cross-thread persistent memory (explicit API)

**Action**: Our checkpoint system handles per-thread. Add a `Store` abstraction for cross-agent shared memory (user profiles, knowledge base).

## 4. Content-Addressed Blobs Save Storage

Postgres checkpointer stores complex values as blobs keyed by `(thread, ns, channel, version)`. Same blob shared across checkpoints. `ON CONFLICT DO NOTHING`.

**Action**: Consider for our Postgres checkpointer when storage becomes a concern.

## 5. Conformance Tests for Pluggable Systems

LangGraph has a dedicated conformance suite that any checkpointer can run against. This ensures all implementations behave identically.

**Action**: Create conformance suites for our Provider and Checkpoint interfaces.

## 6. Anti-Stall via Managed Values

`RemainingSteps` managed value lets nodes gracefully degrade instead of hitting recursion limits:

```python
if remaining_steps < 2:
    return "Need more steps..."
```

**Action**: Our anti-stall system could benefit from injecting step metadata into agents.

## 7. Dynamic Model Selection at Runtime

`create_react_agent` accepts `model: Callable[[State, Runtime], BaseChatModel]` — the model can change per-invocation based on state.

**Action**: Our routing already does this at the orchestrator level. Consider exposing it at the agent level too.

## 8. Tool Middleware Pattern

`ToolCallWrapper` wraps tool execution with a middleware pattern:

```python
def my_wrapper(request, next_fn):
    # pre-processing
    result = next_fn(request)
    # post-processing
    return result
```

**Action**: Add middleware support to our Skill execution pipeline.

## 9. Task-Level Caching

Cache task results by input hash. Cache miss → execute. Cache hit → skip.

**Action**: Add `CachePolicy` to our skill execution. Low effort, high impact for repeated operations.

## 10. Deprecation Strategy

LangGraph deprecated `prebuilt` toward `langchain.agents` with clear migration paths. Their `@deprecated` decorator with `LangGraphDeprecatedSinceV10` category is clean.

**Action**: Establish deprecation patterns early for our public API stability.

## Priority Roadmap

### Phase 1 (Next Sprint)
- [ ] Channel-based state with reducers
- [ ] Conformance test suite for Provider interface
- [ ] Task-level result caching

### Phase 2 (Following Sprint)
- [ ] Interrupt/resume HITL
- [ ] Store abstraction (cross-agent memory)
- [ ] Skill middleware pattern

### Phase 3 (Future)
- [ ] Content-addressed checkpoint blobs
- [ ] Encrypted serialization
- [ ] SSE streaming improvements
