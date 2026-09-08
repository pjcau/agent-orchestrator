"""Semantic knowledge / RAG subsystem (P1).

Public API surface (Single Responsibility — see SOLID rationale below):

- ``EmbeddingProvider`` — turns text into a vector. Pluggable.
- ``Chunker``         — splits a document into searchable units. Pluggable.
- ``KnowledgeStore``  — persists vectors + metadata, returns nearest neighbours.
- ``Ingester``        — orchestrates chunker → embedder → store.
- ``Retriever``       — orchestrates embedder → store.search.

SOLID rationale:

- **S**RP: every class above has exactly one reason to change. Embedding
  models change independently of vector DBs; chunking heuristics change
  independently of ingestion plumbing.
- **O**CP: new providers/stores/chunkers plug in by subclassing the ABCs.
  No conditionals in `Agent` or the API layer reference concrete classes.
- **L**SP: every concrete impl honours the ABC contracts (return types,
  async signatures, error semantics).
- **I**SP: ``IngestInterface`` and ``QueryInterface`` are split so a
  read-only client never has to know about chunking.
- **D**IP: the agent-side ``RetrievalSkill`` depends on the abstractions,
  not on PgVector or sentence-transformers.

This module is part of the **harness** layer — it must NEVER import from
``dashboard/`` or ``integrations/`` (enforced by
``tests/test_import_boundary.py``).
"""

from __future__ import annotations

from .chunker import Chunker, MarkdownChunker, TextChunker
from .embeddings import (
    EmbeddingProvider,
    HashEmbedder,
    LocalEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from .ingestion import Ingester, IngestRequest, IngestResult
from .retrieval import RetrievalResult, Retriever
from .store import (
    InMemoryKnowledgeStore,
    KnowledgeChunk,
    KnowledgeStore,
    SearchHit,
)

__all__ = [
    "Chunker",
    "EmbeddingProvider",
    "HashEmbedder",
    "InMemoryKnowledgeStore",
    "IngestRequest",
    "IngestResult",
    "Ingester",
    "KnowledgeChunk",
    "KnowledgeStore",
    "LocalEmbeddingProvider",
    "MarkdownChunker",
    "OpenAIEmbeddingProvider",
    "RetrievalResult",
    "Retriever",
    "SearchHit",
    "TextChunker",
]
