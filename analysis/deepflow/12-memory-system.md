# 12 - Memory System

## Overview

DeerFlow's memory system builds persistent knowledge across sessions. It's LLM-powered — using a model to extract facts and context from conversations.

## Data Structure (`memory.json`)

```json
{
  "version": "1.0",
  "lastUpdated": "2026-03-14T10:30:00Z",
  "user": {
    "workContext": {"summary": "User is building an AI agent framework", "updatedAt": "..."},
    "personalContext": {"summary": "Prefers concise responses", "updatedAt": "..."},
    "topOfMind": {"summary": "Currently focused on MCP integration", "updatedAt": "..."}
  },
  "history": {
    "recentMonths": {"summary": "...", "updatedAt": "..."},
    "earlierContext": {"summary": "...", "updatedAt": "..."},
    "longTermBackground": {"summary": "...", "updatedAt": "..."}
  },
  "facts": [
    {
      "id": "fact_a1b2c3d4",
      "content": "User prefers TypeScript over JavaScript",
      "category": "preference",
      "confidence": 0.9,
      "createdAt": "...",
      "source": "thread-123"
    }
  ]
}
```

## Categories

Facts are classified into:
- `preference` — user likes/dislikes
- `knowledge` — domain expertise
- `context` — situational info
- `behavior` — patterns observed
- `goal` — user objectives

## Update Flow

```
1. MemoryMiddleware filters messages (user inputs + final AI responses)
2. Queue debounces (30s), batches, deduplicates per-thread
3. Background thread invokes LLM with MEMORY_UPDATE_PROMPT
4. LLM extracts: context updates + new facts + facts to remove
5. Applies updates atomically (temp file + rename)
6. Cache invalidated for next read
```

## Injection

Top 15 facts + context summaries injected into system prompt:
```xml
<memory>
  ... fact content ...
</memory>
```

Max 2000 tokens for memory injection.

## Upload Filtering

Smart filtering removes file-upload references from memory:
```python
_UPLOAD_SENTENCE_RE = re.compile(
    r"[^.!?]*\b(?:upload(?:ed|ing)?...)[^.!?]*[.!?]?\s*",
    re.IGNORECASE,
)
```

Why: uploaded files are session-scoped. Recording "user uploaded report.pdf" in long-term memory causes the agent to search for non-existent files in future sessions.

## Per-Agent Memory

With `agent_name` parameter, each custom agent gets its own memory file:
```
backend/.deer-flow/agents/{agent_name}/memory.json
```

## Configuration

```yaml
memory:
  enabled: true
  storage_path: memory.json
  debounce_seconds: 30
  model_name: null        # Use default model
  max_facts: 100
  fact_confidence_threshold: 0.7
  injection_enabled: true
  max_injection_tokens: 2000
```

## Comparison with Our Memory

| Aspect | DeerFlow | Our Orchestrator |
|--------|----------|-----------------|
| Storage | JSON file | Markdown files |
| Update | LLM-powered extraction | Manual/auto save |
| Structure | user/history/facts | user/feedback/project/reference |
| Injection | XML tags in prompt | CLAUDE.md loading |
| Categories | 5 fact types | 4 memory types |
| Confidence | Per-fact confidence score | No confidence tracking |
| Debounce | 30s queue | Immediate |

DeerFlow's LLM-powered memory is more automated but costs tokens. Our file-based memory is free but requires more explicit management.
