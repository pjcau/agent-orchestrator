# Feature: Configurable Context Summarization

## Context

From DeerFlow analysis (analysis/deepflow/12-memory-system.md, 29-learnings.md L13).
Without summarization, long multi-step tasks blow the context window. We have basic conversation memory but no configurable summarization triggers.

## What to Build

Add configurable context summarization to `src/agent_orchestrator/core/conversation.py`:

### 1. Summarization Triggers

```python
from enum import Enum

class SummarizationTrigger(Enum):
    TOKEN_COUNT = "token_count"      # Trigger when total tokens exceed threshold
    MESSAGE_COUNT = "message_count"  # Trigger when message count exceeds threshold
    FRACTION = "fraction"            # Trigger when used context > fraction of max_context

@dataclass
class SummarizationConfig:
    trigger: SummarizationTrigger = SummarizationTrigger.MESSAGE_COUNT
    threshold: int | float = 20          # 20 messages, or 8000 tokens, or 0.7 fraction
    retain_last: int = 4                 # Always keep last N messages verbatim
    summary_model: str | None = None     # Use lightweight model for summarization (None = same model)
    enabled: bool = True
```

### 2. Summarization Logic

When a trigger fires:

1. Take all messages EXCEPT the last `retain_last` messages
2. Send them to the summarization model with prompt:
   ```
   Summarize the following conversation concisely. Keep: key decisions made,
   artifacts created (file names, function names), unresolved questions, and
   current task status. Drop: greetings, repetitive back-and-forth, resolved issues.
   ```
3. Replace the old messages with a single `SystemMessage`:
   ```
   [Conversation summary] <summary text>
   ```
4. Append the retained recent messages after the summary

### 3. Integration with ConversationManager

```python
class ConversationManager:
    def __init__(self, ..., summarization_config: SummarizationConfig | None = None):
        ...

    async def add_message(self, thread_id: str, message: BaseMessage) -> None:
        # After adding, check if summarization trigger fires
        if self._should_summarize(thread_id):
            await self._summarize(thread_id)

    def _should_summarize(self, thread_id: str) -> bool:
        """Check if summarization trigger threshold is exceeded."""
        ...

    async def _summarize(self, thread_id: str) -> None:
        """Summarize old messages and replace them with a summary."""
        ...
```

### 4. Token Counting

For the `TOKEN_COUNT` and `FRACTION` triggers, implement a simple token estimator:

```python
def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English."""
    return len(text) // 4
```

This is intentionally simple — no need for tiktoken dependency. Rough estimate is fine for triggering.

## Files to Modify

- **Modify**: `src/agent_orchestrator/core/conversation.py` (add SummarizationConfig, trigger logic, summarize method)
- **Modify**: `src/agent_orchestrator/dashboard/agent_runner.py` (pass summarization config when creating ConversationManager)
- **Modify**: `src/agent_orchestrator/core/metrics.py` (add summarization_count counter, tokens_saved gauge)

## Tests

- Test MESSAGE_COUNT trigger fires at threshold
- Test TOKEN_COUNT trigger fires at threshold
- Test FRACTION trigger fires at threshold
- Test retain_last keeps recent messages
- Test summary replaces old messages with single SystemMessage
- Test disabled config never triggers
- Test token estimator approximation
- Test integration: long conversation gets summarized mid-flow

## Acceptance Criteria

- [ ] SummarizationConfig with 3 trigger types
- [ ] Automatic summarization in ConversationManager
- [ ] Last N messages always retained verbatim
- [ ] Lightweight model option for summarization
- [ ] Metrics: summarization count, tokens saved
- [ ] All tests pass
- [ ] Existing tests still pass
