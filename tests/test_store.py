"""Tests for Store — cross-thread persistent key-value storage."""

from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.core.store import (
    InMemoryStore,
    SearchItem,
    _match_filter,
    run_store_conformance,
)


# ─── Unit: filter matching ────────────────────────────────────────────


class TestMatchFilter:
    def test_eq(self):
        assert _match_filter({"x": 1}, {"x": {"$eq": 1}}) is True
        assert _match_filter({"x": 2}, {"x": {"$eq": 1}}) is False

    def test_ne(self):
        assert _match_filter({"x": 1}, {"x": {"$ne": 2}}) is True
        assert _match_filter({"x": 1}, {"x": {"$ne": 1}}) is False

    def test_gt_gte(self):
        assert _match_filter({"x": 10}, {"x": {"$gt": 5}}) is True
        assert _match_filter({"x": 5}, {"x": {"$gt": 5}}) is False
        assert _match_filter({"x": 5}, {"x": {"$gte": 5}}) is True

    def test_lt_lte(self):
        assert _match_filter({"x": 3}, {"x": {"$lt": 5}}) is True
        assert _match_filter({"x": 5}, {"x": {"$lt": 5}}) is False
        assert _match_filter({"x": 5}, {"x": {"$lte": 5}}) is True

    def test_shorthand_eq(self):
        assert _match_filter({"x": "hello"}, {"x": "hello"}) is True
        assert _match_filter({"x": "hello"}, {"x": "world"}) is False

    def test_missing_field(self):
        assert _match_filter({}, {"x": {"$eq": 1}}) is False

    def test_multiple_conditions(self):
        val = {"x": 10, "y": "ok"}
        assert _match_filter(val, {"x": {"$gt": 5}, "y": {"$eq": "ok"}}) is True
        assert _match_filter(val, {"x": {"$gt": 5}, "y": {"$eq": "bad"}}) is False

    def test_unknown_operator(self):
        with pytest.raises(ValueError, match="Unknown filter operator"):
            _match_filter({"x": 1}, {"x": {"$unknown": 1}})


# ─── InMemoryStore ────────────────────────────────────────────────────


class TestInMemoryStore:
    @pytest.fixture
    def store(self):
        return InMemoryStore()

    @pytest.mark.asyncio
    async def test_put_and_get(self, store):
        await store.aput(("users",), "alice", {"name": "Alice", "age": 30})
        item = await store.aget(("users",), "alice")
        assert item is not None
        assert item.key == "alice"
        assert item.namespace == ("users",)
        assert item.value == {"name": "Alice", "age": 30}

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, store):
        item = await store.aget(("nope",), "nope")
        assert item is None

    @pytest.mark.asyncio
    async def test_update(self, store):
        await store.aput(("test",), "k", {"v": 1})
        item1 = await store.aget(("test",), "k")
        created = item1.created_at

        await store.aput(("test",), "k", {"v": 2})
        item2 = await store.aget(("test",), "k")
        assert item2.value == {"v": 2}
        assert item2.created_at == created
        assert item2.updated_at >= item1.updated_at

    @pytest.mark.asyncio
    async def test_delete(self, store):
        await store.aput(("test",), "d", {"x": 1})
        await store.adelete(("test",), "d")
        assert await store.aget(("test",), "d") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, store):
        await store.adelete(("nope",), "nope")  # should not raise

    @pytest.mark.asyncio
    async def test_search_prefix(self, store):
        await store.aput(("org", "a"), "m1", {"name": "Bob"})
        await store.aput(("org", "a"), "m2", {"name": "Carol"})
        await store.aput(("org", "b"), "m1", {"name": "Dave"})
        results = await store.asearch(("org", "a"))
        assert len(results) == 2
        names = {r.value["name"] for r in results}
        assert names == {"Bob", "Carol"}

    @pytest.mark.asyncio
    async def test_search_filter(self, store):
        await store.aput(("agents",), "be", {"model": "sonnet"})
        await store.aput(("agents",), "ai", {"model": "opus"})
        await store.aput(("agents",), "sc", {"model": "opus"})
        results = await store.asearch(("agents",), filter={"model": {"$eq": "opus"}})
        assert len(results) == 2
        assert {r.key for r in results} == {"ai", "sc"}

    @pytest.mark.asyncio
    async def test_search_limit_offset(self, store):
        for i in range(5):
            await store.aput(("p",), f"i{i}", {"i": i})
        p1 = await store.asearch(("p",), limit=2)
        assert len(p1) == 2
        p2 = await store.asearch(("p",), limit=2, offset=2)
        assert len(p2) == 2
        p3 = await store.asearch(("p",), limit=2, offset=4)
        assert len(p3) == 1

    @pytest.mark.asyncio
    async def test_list_namespaces(self, store):
        await store.aput(("ns", "a"), "k", {"x": 1})
        await store.aput(("ns", "b"), "k", {"x": 2})
        nss = await store.alist_namespaces(prefix=("ns",))
        assert ("ns", "a") in nss
        assert ("ns", "b") in nss

    @pytest.mark.asyncio
    async def test_list_namespaces_max_depth(self, store):
        await store.aput(("d", "l1"), "k", {"x": 1})
        await store.aput(("d", "l1", "l2"), "k", {"x": 2})
        nss = await store.alist_namespaces(prefix=("d",), max_depth=1)
        for ns in nss:
            assert len(ns) <= 2

    @pytest.mark.asyncio
    async def test_ttl_expiration(self, store):
        await store.aput(("ttl",), "temp", {"v": 1}, ttl=0.01)
        assert await store.aget(("ttl",), "temp") is not None
        await asyncio.sleep(0.05)
        assert await store.aget(("ttl",), "temp") is None

    @pytest.mark.asyncio
    async def test_ttl_not_expired(self, store):
        await store.aput(("ttl",), "long", {"v": 1}, ttl=60)
        item = await store.aget(("ttl",), "long")
        assert item is not None

    @pytest.mark.asyncio
    async def test_search_returns_search_items(self, store):
        await store.aput(("s",), "k", {"v": 1})
        results = await store.asearch(("s",))
        assert len(results) == 1
        assert isinstance(results[0], SearchItem)
        assert results[0].score is None

    @pytest.mark.asyncio
    async def test_namespace_isolation(self, store):
        await store.aput(("iso", "a"), "k", {"from": "a"})
        await store.aput(("iso", "b"), "k", {"from": "b"})
        ra = await store.asearch(("iso", "a"))
        rb = await store.asearch(("iso", "b"))
        assert len(ra) == 1 and ra[0].value["from"] == "a"
        assert len(rb) == 1 and rb[0].value["from"] == "b"

    @pytest.mark.asyncio
    async def test_empty_namespace(self, store):
        await store.aput((), "global", {"v": 1})
        item = await store.aget((), "global")
        assert item is not None
        assert item.value == {"v": 1}


# ─── Conformance ──────────────────────────────────────────────────────


class TestStoreConformance:
    @pytest.mark.asyncio
    async def test_inmemory_conformance(self):
        store = InMemoryStore()
        report = await run_store_conformance(store)
        assert report.all_passed, (
            f"Conformance failed: {[r.name for r in report.results if r.status.value == 'failed']}"
        )
