"""Ingestion orchestrator: document → chunks → embeddings → store.

DIP: depends on ``Chunker``, ``EmbeddingProvider``, ``IngestInterface``
abstractions. Concrete implementations are injected by the caller.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from .chunker import Chunker
from .embeddings import EmbeddingProvider
from .store import IngestInterface, KnowledgeChunk

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestRequest:
    """Input to ``Ingester.ingest``."""

    text: str
    namespace: tuple[str, ...]
    source_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IngestResult:
    """Outcome of an ingestion call."""

    namespace: tuple[str, ...]
    source_id: str
    chunks_added: int
    embedding_model: str


class Ingester:
    """Pipeline: split → embed → persist.

    Single responsibility: glue between the three abstractions; never owns
    the embedding model itself, never owns the store. Construct once with
    your favourite implementations and reuse.
    """

    def __init__(
        self,
        chunker: Chunker,
        embedder: EmbeddingProvider,
        store: IngestInterface,
    ) -> None:
        self._chunker = chunker
        self._embedder = embedder
        self._store = store

    async def ingest(self, req: IngestRequest) -> IngestResult:
        chunks = self._chunker.chunk(req.text)
        if not chunks:
            return IngestResult(
                namespace=req.namespace,
                source_id=req.source_id,
                chunks_added=0,
                embedding_model=self._embedder.info.name,
            )

        # Embed in a single batch call when possible.
        texts = [c.text for c in chunks]
        vectors = await self._embedder.embed(texts)
        if len(vectors) != len(chunks):
            raise RuntimeError(
                f"Embedder returned {len(vectors)} vectors for "
                f"{len(chunks)} chunks (expected equal length)"
            )

        knowledge_chunks = [
            KnowledgeChunk(
                namespace=req.namespace,
                chunk_id=_chunk_id(req.source_id, idx, c.text),
                text=c.text,
                embedding=v,
                metadata={
                    **req.metadata,
                    "source_id": req.source_id,
                    "location": c.location,
                    "embedding_model": self._embedder.info.name,
                },
            )
            for idx, (c, v) in enumerate(zip(chunks, vectors, strict=True))
        ]

        await self._store.add(knowledge_chunks)

        logger.info(
            "Ingested %d chunks into namespace %s (source=%s, model=%s)",
            len(knowledge_chunks),
            req.namespace,
            req.source_id,
            self._embedder.info.name,
        )

        return IngestResult(
            namespace=req.namespace,
            source_id=req.source_id,
            chunks_added=len(knowledge_chunks),
            embedding_model=self._embedder.info.name,
        )


def _chunk_id(source_id: str, idx: int, text: str) -> str:
    """Deterministic ID so re-ingesting the same content is idempotent."""
    digest = hashlib.sha1(f"{source_id}|{idx}|{text}".encode()).hexdigest()[:16]
    return f"{source_id}:{idx}:{digest}"
