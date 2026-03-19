# Feature: Dangling Tool Call Recovery

## Context

From DeerFlow analysis (analysis/deepflow/22-error-handling.md, 29-learnings.md L8).
When a user interrupts an agent mid-execution, AIMessage tool_calls may exist without matching ToolMessage responses. This breaks the conversation state for the next turn.

## What to Build

Implement dangling tool call detection and recovery in `src/agent_orchestrator/core/tool_recovery.py`:

### Core Logic

1. **Detection**: Before sending messages to LLM, scan the message history for `AIMessage` entries with `tool_calls` that have no matching `ToolMessage` response (match by `tool_call_id`).
2. **Recovery**: For each dangling tool call, inject a placeholder `ToolMessage`:
   ```python
   ToolMessage(
       tool_call_id=dangling_call.id,
       content="[Tool call interrupted — no result available]",
       name=dangling_call.name,
   )
   ```
3. **Logging**: Log each recovered dangling call at WARNING level with session_id, tool name, and call_id.

### API

```python
def recover_dangling_tool_calls(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Scan messages and inject placeholder responses for dangling tool calls.

    Returns a new list with placeholders inserted after their corresponding AIMessages.
    Does NOT mutate the input list.
    """
    ...
```

### Integration Points

- **Agent.execute()** in `src/agent_orchestrator/core/agent.py`: Call `recover_dangling_tool_calls()` on the conversation history before each LLM call.
- **ConversationManager** in `src/agent_orchestrator/core/conversation.py`: Call recovery when loading a persisted thread (threads may have been interrupted).

## Files to Modify

- **Create**: `src/agent_orchestrator/core/tool_recovery.py`
- **Modify**: `src/agent_orchestrator/core/agent.py` (call recovery before LLM)
- **Modify**: `src/agent_orchestrator/core/conversation.py` (call recovery on thread load)

## Tests

- Test no-op when all tool calls have responses
- Test single dangling call gets placeholder
- Test multiple dangling calls in sequence
- Test mixed: some responded, some dangling
- Test placeholder content and structure
- Test original list is not mutated
- Test integration with conversation reload

## Acceptance Criteria

- [ ] `recover_dangling_tool_calls()` function
- [ ] Integrated into Agent.execute() pre-LLM step
- [ ] Integrated into ConversationManager thread loading
- [ ] WARNING log for each recovered call
- [ ] All tests pass
- [ ] Existing tests still pass
