# 03 — Memory

Four distinct memory types in the diagram. They are *not* interchangeable — each has a different scope, lifetime, and retrieval model.

## 3a. Working Context (short-term)

### Motivation
The current conversation. Messages, tool calls, partial plans — everything the LLM sees *right now*.

### Reference implementations
- [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) — `Checkpointer`, threads
- [langchain-ai/langchain](https://github.com/langchain-ai/langchain) — `ConversationBufferMemory` (legacy)

### Match — ✅ HAVE
- `core/conversation.py` — `ConversationManager`, thread-based multi-turn memory, fork, clear
- `core/checkpoint.py` — `InMemoryCheckpointer`
- `core/checkpoint_postgres.py` — `PostgresCheckpointer` (durable, survives restarts)
- Session restore: `POST /api/jobs/{session_id}/restore` re-hydrates context

---

## 3b. Semantic Knowledge (RAG / KB)

### Motivation
External knowledge the agent *retrieves on demand* via similarity search. Documents, code, API docs, domain facts — everything that doesn't fit in the context window and doesn't belong to the conversation.

### Reference implementations

| Repo | What they do |
|------|-------------|
| [run-llama/llama_index](https://github.com/run-llama/llama_index) | RAG framework, retrievers, ingestion |
| [chroma-core/chroma](https://github.com/chroma-core/chroma) | Embedded vector DB |
| [weaviate/weaviate](https://github.com/weaviate/weaviate) | Self-hosted vector DB |
| [qdrant/qdrant](https://github.com/qdrant/qdrant) | Rust vector DB (fast, production-grade) |
| [pgvector/pgvector](https://github.com/pgvector/pgvector) | Postgres extension — **you already have Postgres** |
| [neuml/txtai](https://github.com/neuml/txtai) | All-in-one semantic search |
| [explodinggradients/ragas](https://github.com/explodinggradients/ragas) | RAG evaluation |

### Match — ❌ MISSING

Currently:

- No vector store
- No embedding provider abstraction (`EmbeddingProvider`)
- No retriever / retrieval skill
- No document chunking pipeline
- `document_converter.py` can read PDFs/Excel/Word but **dumps them raw into context** — no indexing

### Gap to close

**Biggest single unlock in the roadmap.** Proposed design:

```python
# core/knowledge.py
class EmbeddingProvider(ABC):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

class KnowledgeStore(ABC):
    async def add(self, docs: list[Document], namespace: str): ...
    async def search(self, query: str, namespace: str, k: int = 5) -> list[Document]: ...

class PgVectorStore(KnowledgeStore):
    # Reuse existing Postgres pool + pgvector extension
    ...
```

Then:
- `skills/retrieval_skill.py` — `retrieve(query, namespace)` callable tool
- Integration with `document_converter.py` for ingestion
- System-prompt injection pattern similar to the existing episodic memory block

---

## 3c. Episodic Experience (long-term memory)

### Motivation
Memory of *what happened* in past tasks/sessions. Lessons learned. Patterns that worked. Mistakes to avoid.

### Reference implementations

| Repo | Pattern |
|------|---------|
| [letta-ai/letta](https://github.com/letta-ai/letta) (ex-MemGPT) | Hierarchical memory, reflection |
| [mem0ai/mem0](https://github.com/mem0ai/mem0) | Per-agent long-term memory |
| [cpacker/MemGPT](https://github.com/cpacker/MemGPT) | Original paper implementation |

### Match — ✅ HAVE

- `core/store.py` — `BaseStore`, namespaces, filter, TTL
- `core/store_postgres.py` — durable Postgres backend (JSONB, dot-encoded namespaces, lazy TTL expiry)
- **Injection mechanism** — recent memories from `("agent", name)` + `("shared",)` prepended to system prompt as `<memory>` block (capped 2000 chars)
- **Auto-persist** — after each successful agent run, task summary stored with 30-day TTL
- API: `GET /api/memory/namespaces`, `GET /api/memory/{namespace}`, `DELETE /api/memory/{namespace}/{key}`, `GET /api/memory/stats`

---

## 3d. Personalized Memory (per-user)

### Motivation
User preferences, profile, personal history across sessions — "this user prefers terse answers", "uses pytest not unittest", "their timezone is CET".

### Reference implementations
- [mem0ai/mem0](https://github.com/mem0ai/mem0) — user-scoped memory is the core feature
- [letta-ai/letta](https://github.com/letta-ai/letta) — user profiles in persona

### Match — ⚠️ PARTIAL

- `core/users.py` has RBAC (admin/developer/viewer) but **no memory attached to user_id**
- Store exists but is only namespaced by `("agent", name)` and `("shared",)`

### Gap to close

Small, high-impact. Two changes:

1. Add namespace `("user", user_id)` when writing/reading memory.
2. In `Agent._build_system_prompt()` (or equivalent), also inject the top-N user memories.

Bonus: a tiny "user profile extractor" skill that scans recent messages for preferences and persists them.
