"""Vector store abstractions.

ISP split:
- ``IngestInterface`` — write side (``add``, ``delete_namespace``)
- ``QueryInterface``  — read side  (``search``, ``list_namespaces``)
- ``KnowledgeStore``  — convenience composition of both, the typical case

This way a future read-only client can depend on ``QueryInterface`` alone.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# Standard namespace conventions. Code SHOULD prefer these helpers over
# free-form tuples so future migrations stay consistent.
SHARED_NAMESPACE = ("shared",)


def agent_namespace(name: str) -> tuple[str, str]:
    """Per-agent knowledge namespace: ``("agent", "<name>")``."""
    return ("agent", name)


def user_namespace(user_id: str) -> tuple[str, str]:
    """Per-user knowledge namespace: ``("user", "<user_id>")``.

    Reserved for P4 (personalised memory) — same store, different namespace.
    """
    return ("user", user_id)


@dataclass(frozen=True)
class KnowledgeChunk:
    """A retrievable unit of knowledge with its embedding and metadata."""

    namespace: tuple[str, ...]
    chunk_id: str
    text: str
    embedding: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchHit:
    """A single search result."""

    chunk: KnowledgeChunk
    score: float


# ---------------------------------------------------------------------------
# Interfaces (ISP)
# ---------------------------------------------------------------------------


class IngestInterface(ABC):
    """Write-side of the knowledge store."""

    @abstractmethod
    async def add(self, chunks: list[KnowledgeChunk]) -> None: ...

    @abstractmethod
    async def delete_namespace(self, namespace: tuple[str, ...]) -> int:
        """Delete every chunk in ``namespace``. Returns chunks removed."""
        ...


class QueryInterface(ABC):
    """Read-side of the knowledge store."""

    @abstractmethod
    async def search(
        self,
        namespace: tuple[str, ...],
        query_embedding: list[float],
        k: int = 5,
    ) -> list[SearchHit]:
        """Return the top-k nearest chunks in the namespace."""
        ...

    @abstractmethod
    async def list_namespaces(self) -> list[tuple[str, ...]]: ...

    @abstractmethod
    async def count(self, namespace: tuple[str, ...]) -> int: ...


class KnowledgeStore(IngestInterface, QueryInterface, ABC):
    """Combined interface — most callers want both halves."""


# ---------------------------------------------------------------------------
# In-memory implementation (default; no infra required)
# ---------------------------------------------------------------------------


class InMemoryKnowledgeStore(KnowledgeStore):
    """Process-local store for dev, tests, and small workloads.

    O(N) cosine search per namespace. Loses data on restart.
    For production with > a few thousand chunks, swap with a pgvector-backed
    impl (one of the next steps; the abstraction is identical).
    """

    def __init__(self) -> None:
        # namespace tuple → list of chunks
        self._data: dict[tuple[str, ...], list[KnowledgeChunk]] = {}

    async def add(self, chunks: list[KnowledgeChunk]) -> None:
        for chunk in chunks:
            bucket = self._data.setdefault(chunk.namespace, [])
            # De-dupe by chunk_id (latest wins).
            bucket[:] = [c for c in bucket if c.chunk_id != chunk.chunk_id]
            bucket.append(chunk)

    async def delete_namespace(self, namespace: tuple[str, ...]) -> int:
        bucket = self._data.pop(namespace, [])
        return len(bucket)

    async def search(
        self,
        namespace: tuple[str, ...],
        query_embedding: list[float],
        k: int = 5,
    ) -> list[SearchHit]:
        bucket = self._data.get(namespace, [])
        if not bucket or k <= 0:
            return []
        scored = [
            SearchHit(chunk=c, score=_cosine(query_embedding, c.embedding))
            for c in bucket
        ]
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:k]

    async def list_namespaces(self) -> list[tuple[str, ...]]:
        return list(self._data.keys())

    async def count(self, namespace: tuple[str, ...]) -> int:
        return len(self._data.get(namespace, []))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Returns 0 if either vector is degenerate."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
