"""Tests for the core knowledge / RAG subsystem (P1).

Covered:
- HashEmbedder determinism + correct dimensionality + L2-normalised
- TextChunker windowing and overlap
- MarkdownChunker section detection + header path + fallback
- InMemoryKnowledgeStore add / search / delete / count / list_namespaces
- Ingester pipeline end-to-end
- Retriever returns ranked hits and renders a Markdown context block
- LSP: an alternative embedder substitutes cleanly
"""

from __future__ import annotations

import math

import pytest

from agent_orchestrator.core.knowledge import (
    Chunker,
    HashEmbedder,
    InMemoryKnowledgeStore,
    Ingester,
    IngestRequest,
    KnowledgeChunk,
    MarkdownChunker,
    Retriever,
    TextChunker,
)
from agent_orchestrator.core.knowledge.chunker import Chunk
from agent_orchestrator.core.knowledge.embeddings import EmbeddingModelInfo, EmbeddingProvider
from agent_orchestrator.core.knowledge.store import (
    SHARED_NAMESPACE,
    agent_namespace,
    user_namespace,
)


# ---------------------------------------------------------------------------
# HashEmbedder
# ---------------------------------------------------------------------------


class TestHashEmbedder:
    @pytest.mark.asyncio
    async def test_returns_correct_dim(self):
        e = HashEmbedder(dim=32)
        v = await e.embed_one("hello")
        assert len(v) == 32

    @pytest.mark.asyncio
    async def test_is_deterministic(self):
        e = HashEmbedder(dim=16)
        a = await e.embed_one("the quick brown fox")
        b = await e.embed_one("the quick brown fox")
        assert a == b

    @pytest.mark.asyncio
    async def test_different_inputs_produce_different_vectors(self):
        e = HashEmbedder(dim=16)
        a = await e.embed_one("alpha")
        b = await e.embed_one("beta")
        assert a != b

    @pytest.mark.asyncio
    async def test_l2_normalised(self):
        e = HashEmbedder(dim=64)
        v = await e.embed_one("anything")
        norm = math.sqrt(sum(x * x for x in v))
        assert pytest.approx(norm, abs=1e-6) == 1.0

    @pytest.mark.asyncio
    async def test_batch_preserves_order(self):
        e = HashEmbedder(dim=8)
        vs = await e.embed(["a", "b", "c"])
        assert len(vs) == 3
        # Each batched element matches its single-call equivalent.
        for text, vec in zip(["a", "b", "c"], vs, strict=True):
            assert vec == await e.embed_one(text)

    def test_invalid_dim_rejected(self):
        with pytest.raises(ValueError):
            HashEmbedder(dim=0)


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------


class TestTextChunker:
    def test_empty_input_yields_no_chunks(self):
        assert TextChunker().chunk("") == []
        assert TextChunker().chunk("   \n  ") == []

    def test_small_input_yields_single_chunk(self):
        chunks = TextChunker(window=100, overlap=10).chunk("hello")
        assert len(chunks) == 1
        assert chunks[0].text == "hello"

    def test_large_input_is_split_with_overlap(self):
        text = "x" * 250
        chunks = TextChunker(window=100, overlap=20).chunk(text)
        # step = 80, expected positions: 0, 80, 160, 240
        assert [c.location for c in chunks] == ["char:0", "char:80", "char:160", "char:240"]
        assert len(chunks) == 4

    def test_overlap_must_be_smaller_than_window(self):
        with pytest.raises(ValueError):
            TextChunker(window=100, overlap=100)


class TestMarkdownChunker:
    def test_falls_back_to_text_chunker_when_no_headers(self):
        c = MarkdownChunker(max_section_chars=50, overlap=10)
        chunks = c.chunk("plain text without headers")
        assert len(chunks) == 1
        assert "char:" in chunks[0].location

    def test_splits_on_headers_and_keeps_path(self):
        md = "# A\n\nbody A\n\n## B\n\nbody B\n\n# C\n\nbody C"
        chunks = MarkdownChunker(max_section_chars=200).chunk(md)
        locations = [c.location for c in chunks]
        assert "A" in locations
        assert "A / B" in locations
        assert "C" in locations

    def test_large_section_is_subchunked_with_header_path(self):
        body = "x" * 5000
        md = f"# Big\n\n{body}"
        chunks = MarkdownChunker(max_section_chars=1000, overlap=100).chunk(md)
        assert len(chunks) > 1
        # Each sub-chunk preserves the header path
        assert all(c.location.startswith("Big") for c in chunks)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


@pytest.fixture
def chunk_factory():
    def make(ns: tuple[str, ...], cid: str, text: str, vec: list[float]):
        return KnowledgeChunk(
            namespace=ns,
            chunk_id=cid,
            text=text,
            embedding=vec,
            metadata={"source_id": cid.split(":")[0]},
        )

    return make


class TestInMemoryKnowledgeStore:
    @pytest.mark.asyncio
    async def test_add_and_count(self, chunk_factory):
        store = InMemoryKnowledgeStore()
        ns = SHARED_NAMESPACE
        await store.add([chunk_factory(ns, "x:0", "hello", [1.0, 0.0])])
        assert await store.count(ns) == 1

    @pytest.mark.asyncio
    async def test_dedupe_by_chunk_id(self, chunk_factory):
        store = InMemoryKnowledgeStore()
        ns = SHARED_NAMESPACE
        await store.add([chunk_factory(ns, "x:0", "v1", [1.0, 0.0])])
        await store.add([chunk_factory(ns, "x:0", "v2", [0.0, 1.0])])
        assert await store.count(ns) == 1

    @pytest.mark.asyncio
    async def test_search_ranks_by_cosine(self, chunk_factory):
        store = InMemoryKnowledgeStore()
        ns = SHARED_NAMESPACE
        await store.add(
            [
                chunk_factory(ns, "a:0", "near", [1.0, 0.0]),
                chunk_factory(ns, "b:0", "orthogonal", [0.0, 1.0]),
                chunk_factory(ns, "c:0", "opposite", [-1.0, 0.0]),
            ]
        )
        hits = await store.search(ns, [1.0, 0.0], k=3)
        assert [h.chunk.chunk_id for h in hits] == ["a:0", "b:0", "c:0"]
        assert hits[0].score > hits[1].score > hits[2].score

    @pytest.mark.asyncio
    async def test_search_respects_namespace(self, chunk_factory):
        store = InMemoryKnowledgeStore()
        await store.add([chunk_factory(("a", "x"), "a:0", "x", [1.0])])
        await store.add([chunk_factory(("a", "y"), "a:1", "y", [1.0])])
        hits = await store.search(("a", "x"), [1.0], k=5)
        assert [h.chunk.chunk_id for h in hits] == ["a:0"]

    @pytest.mark.asyncio
    async def test_delete_namespace_removes_all(self, chunk_factory):
        store = InMemoryKnowledgeStore()
        ns = SHARED_NAMESPACE
        await store.add(
            [
                chunk_factory(ns, "a:0", "x", [1.0]),
                chunk_factory(ns, "b:0", "y", [0.0, 1.0]),
            ]
        )
        removed = await store.delete_namespace(ns)
        assert removed == 2
        assert await store.count(ns) == 0
        assert ns not in await store.list_namespaces()

    @pytest.mark.asyncio
    async def test_list_namespaces(self, chunk_factory):
        store = InMemoryKnowledgeStore()
        await store.add([chunk_factory(agent_namespace("backend"), "a:0", "x", [1.0])])
        await store.add([chunk_factory(user_namespace("u-1"), "u:0", "y", [1.0])])
        result = set(await store.list_namespaces())
        assert ("agent", "backend") in result
        assert ("user", "u-1") in result


# ---------------------------------------------------------------------------
# Ingester
# ---------------------------------------------------------------------------


class TestIngester:
    @pytest.mark.asyncio
    async def test_pipeline_end_to_end(self):
        chunker = MarkdownChunker(max_section_chars=1000)
        embedder = HashEmbedder(dim=16)
        store = InMemoryKnowledgeStore()
        ingester = Ingester(chunker, embedder, store)

        result = await ingester.ingest(
            IngestRequest(
                text="# Title\n\nbody text here\n\n## Sub\n\nmore body",
                namespace=agent_namespace("backend"),
                source_id="doc1",
            )
        )

        assert result.chunks_added > 0
        assert result.embedding_model == "hash-md5"
        assert await store.count(agent_namespace("backend")) == result.chunks_added

    @pytest.mark.asyncio
    async def test_empty_text_yields_zero_chunks(self):
        ingester = Ingester(TextChunker(), HashEmbedder(dim=8), InMemoryKnowledgeStore())
        result = await ingester.ingest(
            IngestRequest(text="   ", namespace=SHARED_NAMESPACE, source_id="x")
        )
        assert result.chunks_added == 0

    @pytest.mark.asyncio
    async def test_reingestion_is_idempotent(self):
        text = "# A\n\nbody"
        chunker = MarkdownChunker()
        embedder = HashEmbedder(dim=8)
        store = InMemoryKnowledgeStore()
        ingester = Ingester(chunker, embedder, store)

        r1 = await ingester.ingest(
            IngestRequest(text=text, namespace=SHARED_NAMESPACE, source_id="d1")
        )
        r2 = await ingester.ingest(
            IngestRequest(text=text, namespace=SHARED_NAMESPACE, source_id="d1")
        )
        # Re-ingesting the same source_id with same content should not grow
        # the namespace.
        assert await store.count(SHARED_NAMESPACE) == r1.chunks_added == r2.chunks_added


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------


class TestRetriever:
    @pytest.mark.asyncio
    async def test_retrieves_relevant_chunks(self):
        store = InMemoryKnowledgeStore()
        embedder = HashEmbedder(dim=32)
        ingester = Ingester(TextChunker(window=120), embedder, store)
        ns = agent_namespace("data-science")

        # Three short docs; the third is the one we'll search for.
        for src, body in [
            ("a", "alpha alpha alpha"),
            ("b", "beta beta beta"),
            ("c", "gamma gamma gamma"),
        ]:
            await ingester.ingest(IngestRequest(text=body, namespace=ns, source_id=src))

        retriever = Retriever(embedder, store)
        result = await retriever.retrieve("gamma gamma gamma", ns, k=3)

        assert not result.is_empty
        assert result.embedding_model == "hash-md5"
        # The matching doc must be the top hit (with HashEmbedder this is
        # guaranteed because identical text → identical vector → cosine=1).
        assert result.hits[0].chunk.metadata.get("source_id") == "c"

    @pytest.mark.asyncio
    async def test_empty_query_returns_no_hits(self):
        retriever = Retriever(HashEmbedder(dim=8), InMemoryKnowledgeStore())
        result = await retriever.retrieve("", SHARED_NAMESPACE, k=5)
        assert result.is_empty
        assert result.as_context_block() == ""

    @pytest.mark.asyncio
    async def test_context_block_includes_scores_and_locations(self):
        store = InMemoryKnowledgeStore()
        embedder = HashEmbedder(dim=16)
        ns = SHARED_NAMESPACE
        await Ingester(TextChunker(window=200), embedder, store).ingest(
            IngestRequest(text="hello world", namespace=ns, source_id="doc")
        )

        result = await Retriever(embedder, store).retrieve("hello world", ns, k=1)
        block = result.as_context_block()
        assert "## Retrieved context" in block
        assert "score=" in block
        assert "hello world" in block

    @pytest.mark.asyncio
    async def test_context_block_respects_max_chars(self):
        store = InMemoryKnowledgeStore()
        embedder = HashEmbedder(dim=16)
        ns = SHARED_NAMESPACE
        await Ingester(TextChunker(window=200), embedder, store).ingest(
            IngestRequest(text="x" * 500, namespace=ns, source_id="big")
        )
        result = await Retriever(embedder, store).retrieve("x" * 500, ns, k=1)
        block = result.as_context_block(max_chars=120)
        assert len(block) <= 200  # header + truncated body


# ---------------------------------------------------------------------------
# LSP — alternative embedder substitutes cleanly
# ---------------------------------------------------------------------------


class StubEmbedder(EmbeddingProvider):
    """Always returns a fixed unit vector. LSP-conformant."""

    @property
    def info(self) -> EmbeddingModelInfo:
        return EmbeddingModelInfo(name="stub", dim=4, provider="test")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


@pytest.mark.asyncio
async def test_alternative_embedder_satisfies_pipeline():
    """Substituting a different embedder must not require pipeline changes."""
    store = InMemoryKnowledgeStore()
    ingester = Ingester(TextChunker(window=200), StubEmbedder(), store)

    await ingester.ingest(IngestRequest(text="anything", namespace=SHARED_NAMESPACE, source_id="d"))
    retriever = Retriever(StubEmbedder(), store)
    result = await retriever.retrieve("anything", SHARED_NAMESPACE, k=1)

    assert result.embedding_model == "stub"
    assert not result.is_empty


# ---------------------------------------------------------------------------
# ISP — Chunker contract
# ---------------------------------------------------------------------------


def test_text_chunker_is_a_chunker():
    """TextChunker satisfies the Chunker ABC (LSP)."""
    assert isinstance(TextChunker(), Chunker)
    assert isinstance(MarkdownChunker(), Chunker)


def test_chunk_dataclass_is_frozen():
    c = Chunk(text="hi", location="char:0")
    with pytest.raises(Exception):
        c.text = "changed"  # type: ignore[misc]
