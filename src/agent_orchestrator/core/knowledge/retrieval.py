"""Retrieval orchestrator: query → embedding → store.search."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .embeddings import EmbeddingProvider
from .store import QueryInterface, SearchHit

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalResult:
    """Output of ``Retriever.retrieve``."""

    namespace: tuple[str, ...]
    query: str
    hits: list[SearchHit] = field(default_factory=list)
    embedding_model: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.hits

    def as_context_block(self, max_chars: int | None = None) -> str:
        """Render hits as a Markdown context block ready for the LLM prompt.

        Each hit shows its location and a score, so the model can cite the
        source. Optionally truncated to ``max_chars`` total.
        """
        if not self.hits:
            return ""
        parts: list[str] = ["## Retrieved context\n"]
        budget = max_chars
        for hit in self.hits:
            loc = hit.chunk.metadata.get("location") or hit.chunk.metadata.get("source_id", "?")
            block = f"\n### {loc} (score={hit.score:.2f})\n\n{hit.chunk.text.strip()}\n"
            if budget is not None:
                if budget <= 0:
                    break
                if len(block) > budget:
                    block = block[:budget] + " …"
                    budget = 0
                else:
                    budget -= len(block)
            parts.append(block)
        return "".join(parts).rstrip() + "\n"


class Retriever:
    """Embed a query and search a single namespace.

    Multi-namespace fan-out is intentionally NOT here — agents that need
    "shared + per-agent" merging compose two ``Retriever.retrieve`` calls
    rather than complicating this class. SRP wins, ranking remains
    explicit and predictable.
    """

    def __init__(self, embedder: EmbeddingProvider, store: QueryInterface) -> None:
        self._embedder = embedder
        self._store = store

    async def retrieve(
        self,
        query: str,
        namespace: tuple[str, ...],
        k: int = 5,
    ) -> RetrievalResult:
        query = (query or "").strip()
        if not query or k <= 0:
            return RetrievalResult(
                namespace=namespace,
                query=query,
                hits=[],
                embedding_model=self._embedder.info.name,
            )

        vec = await self._embedder.embed_one(query)
        hits = await self._store.search(namespace, vec, k=k)
        logger.info(
            "Retrieved %d chunks for namespace=%s (k=%d, model=%s)",
            len(hits),
            namespace,
            k,
            self._embedder.info.name,
        )
        return RetrievalResult(
            namespace=namespace,
            query=query,
            hits=hits,
            embedding_model=self._embedder.info.name,
        )
