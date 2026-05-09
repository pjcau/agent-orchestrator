"""Embedding providers for the knowledge subsystem.

Three concrete implementations, all behind the ``EmbeddingProvider`` ABC:

- ``HashEmbedder`` — deterministic, dependency-free, **dev/test only**.
  Uses MD5-derived float hashing so the same text yields the same vector
  across runs. NOT suitable for real semantic search; ships with the
  package so the rest of the pipeline can be exercised without any extra
  install.

- ``LocalEmbeddingProvider`` — wraps ``sentence-transformers``.
  Default model: ``all-MiniLM-L6-v2`` (384 dim, ~100 MB). Optional dep:
  ``pip install 'agent-orchestrator[rag]'``.

- ``OpenAIEmbeddingProvider`` — calls the OpenAI embeddings API.
  Default model: ``text-embedding-3-small`` (1536 dim).
  Requires ``openai`` package and ``OPENAI_API_KEY``.

LSP holds: every provider returns ``list[float]`` of fixed dimensionality
(``provider.dim``) and supports both single-text and batch calls.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmbeddingModelInfo:
    """Static metadata about an embedding model."""

    name: str
    dim: int
    provider: str


class EmbeddingProvider(ABC):
    """Abstract embedding provider. SRP: convert text → vector(s)."""

    @property
    @abstractmethod
    def info(self) -> EmbeddingModelInfo: ...

    @property
    def dim(self) -> int:
        return self.info.dim

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text. Order is preserved."""
        ...

    async def embed_one(self, text: str) -> list[float]:
        """Convenience: embed a single string."""
        result = await self.embed([text])
        return result[0]


# ---------------------------------------------------------------------------
# HashEmbedder — dev/test default
# ---------------------------------------------------------------------------


class HashEmbedder(EmbeddingProvider):
    """Deterministic hash-based embedder for tests and offline dev.

    Uses repeated MD5 digests of token-prefixed text to fill a fixed-length
    vector with values in [-1, 1]. Same input → same output. Not semantic;
    similarity reflects byte-level overlap only. Use for plumbing tests,
    NOT for real retrieval quality.
    """

    def __init__(self, dim: int = 64) -> None:
        if dim <= 0 or dim > 4096:
            raise ValueError("HashEmbedder dim must be between 1 and 4096")
        self._dim = dim

    @property
    def info(self) -> EmbeddingModelInfo:
        return EmbeddingModelInfo(name="hash-md5", dim=self._dim, provider="builtin")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_single(t) for t in texts]

    def _embed_single(self, text: str) -> list[float]:
        norm = (text or "").strip().lower()
        out: list[float] = []
        counter = 0
        # Generate `dim` floats by chaining MD5 digests (16 bytes each).
        while len(out) < self._dim:
            digest = hashlib.md5(f"{counter}|{norm}".encode("utf-8")).digest()
            for i in range(0, len(digest), 2):
                if len(out) >= self._dim:
                    break
                # Map two bytes to a float in [-1, 1)
                pair = int.from_bytes(digest[i : i + 2], "big")
                out.append((pair / 32768.0) - 1.0)
            counter += 1
        # L2-normalise so cosine similarity behaves like an inner product.
        norm_factor = math.sqrt(sum(x * x for x in out)) or 1.0
        return [x / norm_factor for x in out]


# ---------------------------------------------------------------------------
# LocalEmbeddingProvider — sentence-transformers
# ---------------------------------------------------------------------------


class LocalEmbeddingProvider(EmbeddingProvider):
    """Wraps a local sentence-transformers model. Optional dep."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "LocalEmbeddingProvider requires sentence-transformers. "
                "Install with: pip install 'agent-orchestrator[rag]'"
            ) from exc
        self._model_name = model_name
        self._model = SentenceTransformer(model_name)
        self._dim = int(self._model.get_sentence_embedding_dimension())

    @property
    def info(self) -> EmbeddingModelInfo:
        return EmbeddingModelInfo(
            name=self._model_name, dim=self._dim, provider="sentence-transformers"
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # sentence-transformers is synchronous; run in a thread so the
        # event loop is not blocked on long batches.
        import asyncio

        def _encode() -> list[list[float]]:
            arr = self._model.encode(texts, normalize_embeddings=True)
            return [list(map(float, row)) for row in arr]

        return await asyncio.get_running_loop().run_in_executor(None, _encode)


# ---------------------------------------------------------------------------
# OpenAIEmbeddingProvider
# ---------------------------------------------------------------------------


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Calls OpenAI's embeddings endpoint."""

    _DEFAULT_DIMS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
    ) -> None:
        try:
            from openai import AsyncOpenAI  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "OpenAIEmbeddingProvider requires the openai package. "
                "Install with: pip install 'agent-orchestrator[openai]'"
            ) from exc
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "OpenAIEmbeddingProvider requires an API key (OPENAI_API_KEY)"
            )
        self._client = AsyncOpenAI(api_key=key)
        self._model = model
        self._dim = self._DEFAULT_DIMS.get(model, 1536)

    @property
    def info(self) -> EmbeddingModelInfo:
        return EmbeddingModelInfo(name=self._model, dim=self._dim, provider="openai")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = await self._client.embeddings.create(model=self._model, input=texts)
        return [list(item.embedding) for item in resp.data]
