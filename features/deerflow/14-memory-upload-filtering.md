# Feature: Memory Upload Filtering

## Context

From DeerFlow analysis (analysis/deepflow/12-memory-system.md, 29-learnings.md L7).
When agents upload/create files during a session, file paths get recorded in conversation memory. In future sessions, agents search for these non-existent session-scoped files, causing errors and wasted cycles.

## What to Build

### 1. Upload Path Filter

```python
# src/agent_orchestrator/core/memory_filter.py

import re

# Patterns that indicate session-scoped file references
SESSION_FILE_PATTERNS = [
    r"jobs/job_[a-f0-9\-]+/",              # Session job directories
    r"/tmp/[a-f0-9\-]+",                     # Temp files with UUIDs
    r"uploads/[a-f0-9\-]+/",                 # Upload directories
    r"/workspace/[a-f0-9\-]+/",              # Sandbox workspace paths
]

class MemoryFilter:
    """Filter session-scoped file references from persistent memory."""

    def __init__(self, patterns: list[str] | None = None):
        self._patterns = [re.compile(p) for p in (patterns or SESSION_FILE_PATTERNS)]

    def filter_message(self, content: str) -> str:
        """Remove or replace session-scoped file paths in message content.

        Replaces matched paths with [session-file] placeholder to preserve
        the intent without the specific path.
        """
        result = content
        for pattern in self._patterns:
            result = pattern.sub("[session-file]", result)
        return result

    def should_persist(self, content: str) -> bool:
        """Check if a message contains ONLY session-file references.

        If the entire message is about session files, skip persisting it entirely.
        """
        filtered = self.filter_message(content)
        # If filtering removed most of the content, skip it
        stripped = filtered.replace("[session-file]", "").strip()
        return len(stripped) > 20  # Arbitrary threshold: at least 20 chars of real content

    def filter_messages(self, messages: list) -> list:
        """Filter a list of messages for persistent memory storage."""
        result = []
        for msg in messages:
            if hasattr(msg, "content") and isinstance(msg.content, str):
                if self.should_persist(msg.content):
                    # Create a copy with filtered content
                    filtered_msg = msg.copy()
                    filtered_msg.content = self.filter_message(msg.content)
                    result.append(filtered_msg)
            else:
                result.append(msg)
        return result
```

### 2. Integration with ConversationManager

Apply filtering when persisting conversation memory:

```python
# In conversation.py

class ConversationManager:
    def __init__(self, ..., memory_filter: MemoryFilter | None = None):
        self._filter = memory_filter or MemoryFilter()

    async def persist(self, thread_id: str) -> None:
        """Persist conversation to checkpoint, filtering session-scoped paths."""
        messages = self._threads[thread_id]
        filtered = self._filter.filter_messages(messages)
        # Persist filtered messages
        ...
```

### 3. Integration with Store

Also filter when writing to BaseStore (cross-thread memory):

```python
# In store.py

class BaseStore:
    def put(self, namespace: tuple, key: str, value: dict) -> None:
        if "content" in value and isinstance(value["content"], str):
            value = {**value, "content": self._filter.filter_message(value["content"])}
        # ... existing put logic
```

## Files to Modify

- **Create**: `src/agent_orchestrator/core/memory_filter.py`
- **Modify**: `src/agent_orchestrator/core/conversation.py` (apply filter on persist)
- **Modify**: `src/agent_orchestrator/core/store.py` (apply filter on put)

## Tests

- Test session job paths are replaced with [session-file]
- Test temp file paths are replaced
- Test upload paths are replaced
- Test non-session paths are preserved
- Test should_persist returns False for file-only messages
- Test should_persist returns True for mixed messages
- Test filter_messages filters a list correctly
- Test custom patterns work
- Test integration with ConversationManager.persist()
- Test integration with BaseStore.put()

## Acceptance Criteria

- [ ] MemoryFilter class with configurable patterns
- [ ] Session-scoped paths replaced with [session-file] placeholder
- [ ] Integrated into ConversationManager persistence
- [ ] Integrated into BaseStore writes
- [ ] Messages that are ONLY about session files are dropped
- [ ] All tests pass
- [ ] Existing tests still pass
