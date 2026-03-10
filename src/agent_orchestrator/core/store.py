"""Store — cross-thread persistent key-value storage (long-term memory).

Separate from checkpoints:
- Checkpoints = per-thread conversation state (automatic, managed by graph)
- Store = cross-thread persistent memory (explicit API, namespace-based)

Use cases: user profiles, shared knowledge base, agent learning, cross-agent memory.

Inspired by LangGraph's Store abstraction (analysis/langgraph/14-store.md).

Usage:
    store = InMemoryStore()
    await store.aput(("users", "alice"), "profile", {"role": "admin"})
    item = await store.aget(("users", "alice"), "profile")
    results = await store.asearch(("users",), filter={"role": {"$eq": "admin"}})
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# ─── Data Models ──────────────────────────────────────────────────────

Namespace = tuple[str, ...]


@dataclass
class Item:
    """A stored item with namespace-based hierarchy."""

    namespace: Namespace
    key: str
    value: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class SearchItem(Item):
    """An Item returned from search, with optional relevance score."""

    score: float | None = None


# ─── Filter Operators ─────────────────────────────────────────────────

FILTER_OPS = {"$eq", "$ne", "$gt", "$gte", "$lt", "$lte"}


def _match_filter(value: dict[str, Any], filter_dict: dict[str, Any]) -> bool:
    """Check if a value dict matches a filter specification.

    Filter format: {"field": {"$op": target}, ...}
    Supported ops: $eq, $ne, $gt, $gte, $lt, $lte
    """
    for filter_field, condition in filter_dict.items():
        actual = value.get(filter_field)
        if not isinstance(condition, dict):
            # Shorthand: {"field": value} means {"field": {"$eq": value}}
            if actual != condition:
                return False
            continue
        for op, target in condition.items():
            if op not in FILTER_OPS:
                raise ValueError(f"Unknown filter operator: {op}")
            if actual is None:
                return False
            if op == "$eq" and actual != target:
                return False
            if op == "$ne" and actual == target:
                return False
            if op == "$gt" and not (actual > target):
                return False
            if op == "$gte" and not (actual >= target):
                return False
            if op == "$lt" and not (actual < target):
                return False
            if op == "$lte" and not (actual <= target):
                return False
    return True


# ─── Base Store Interface ─────────────────────────────────────────────


class BaseStore(ABC):
    """Abstract base class for cross-thread persistent storage.

    All implementations must support namespace-based key-value storage
    with filter-based search.
    """

    @abstractmethod
    async def aget(self, namespace: Namespace, key: str) -> Item | None:
        """Get a single item by namespace and key."""
        ...

    @abstractmethod
    async def aput(
        self,
        namespace: Namespace,
        key: str,
        value: dict[str, Any],
        *,
        ttl: float | None = None,
    ) -> None:
        """Store an item. If key exists, update it. ttl in seconds."""
        ...

    @abstractmethod
    async def adelete(self, namespace: Namespace, key: str) -> None:
        """Delete an item by namespace and key."""
        ...

    @abstractmethod
    async def asearch(
        self,
        namespace_prefix: Namespace,
        *,
        filter: dict[str, Any] | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SearchItem]:
        """Search items under a namespace prefix with optional filters."""
        ...

    @abstractmethod
    async def alist_namespaces(
        self,
        *,
        prefix: Namespace | None = None,
        max_depth: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Namespace]:
        """List distinct namespaces, optionally filtered by prefix and depth."""
        ...

    # ─── Sync convenience wrappers ────────────────────────────────────

    def get(self, namespace: Namespace, key: str) -> Item | None:
        """Sync wrapper for aget (for non-async contexts)."""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            raise RuntimeError("Use aget() in async context")
        return asyncio.run(self.aget(namespace, key))

    def put(
        self,
        namespace: Namespace,
        key: str,
        value: dict[str, Any],
        *,
        ttl: float | None = None,
    ) -> None:
        """Sync wrapper for aput."""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            raise RuntimeError("Use aput() in async context")
        asyncio.run(self.aput(namespace, key, value, ttl=ttl))

    def delete(self, namespace: Namespace, key: str) -> None:
        """Sync wrapper for adelete."""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            raise RuntimeError("Use adelete() in async context")
        asyncio.run(self.adelete(namespace, key))


# ─── InMemoryStore ────────────────────────────────────────────────────


class InMemoryStore(BaseStore):
    """In-memory store for development and testing.

    Items are stored in a flat dict keyed by (namespace, key).
    TTL is enforced on read (lazy expiration).
    """

    def __init__(self) -> None:
        self._data: dict[tuple[Namespace, str], Item] = {}
        self._ttls: dict[tuple[Namespace, str], float] = {}  # expiry timestamps

    def _is_expired(self, compound_key: tuple[Namespace, str]) -> bool:
        expiry = self._ttls.get(compound_key)
        if expiry is None:
            return False
        return time.time() > expiry

    def _cleanup_expired(self, compound_key: tuple[Namespace, str]) -> None:
        if self._is_expired(compound_key):
            self._data.pop(compound_key, None)
            self._ttls.pop(compound_key, None)

    async def aget(self, namespace: Namespace, key: str) -> Item | None:
        compound_key = (namespace, key)
        self._cleanup_expired(compound_key)
        return self._data.get(compound_key)

    async def aput(
        self,
        namespace: Namespace,
        key: str,
        value: dict[str, Any],
        *,
        ttl: float | None = None,
    ) -> None:
        compound_key = (namespace, key)
        now = time.time()
        existing = self._data.get(compound_key)
        created_at = existing.created_at if existing else now
        self._data[compound_key] = Item(
            namespace=namespace,
            key=key,
            value=value,
            created_at=created_at,
            updated_at=now,
        )
        if ttl is not None:
            self._ttls[compound_key] = now + ttl
        elif compound_key in self._ttls:
            del self._ttls[compound_key]

    async def adelete(self, namespace: Namespace, key: str) -> None:
        compound_key = (namespace, key)
        self._data.pop(compound_key, None)
        self._ttls.pop(compound_key, None)

    async def asearch(
        self,
        namespace_prefix: Namespace,
        *,
        filter: dict[str, Any] | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SearchItem]:
        results: list[SearchItem] = []
        prefix_len = len(namespace_prefix)

        for compound_key, item in list(self._data.items()):
            self._cleanup_expired(compound_key)
            if compound_key not in self._data:
                continue

            ns = item.namespace
            # Check namespace prefix match
            if len(ns) < prefix_len:
                continue
            if ns[:prefix_len] != namespace_prefix:
                continue

            # Apply filter
            if filter and not _match_filter(item.value, filter):
                continue

            results.append(
                SearchItem(
                    namespace=item.namespace,
                    key=item.key,
                    value=item.value,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                    score=None,
                )
            )

        # Sort by updated_at descending (most recent first)
        results.sort(key=lambda x: x.updated_at, reverse=True)

        return results[offset : offset + limit]

    async def alist_namespaces(
        self,
        *,
        prefix: Namespace | None = None,
        max_depth: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Namespace]:
        namespaces: set[Namespace] = set()

        for compound_key, item in list(self._data.items()):
            self._cleanup_expired(compound_key)
            if compound_key not in self._data:
                continue

            ns = item.namespace

            # Filter by prefix
            if prefix:
                if len(ns) < len(prefix):
                    continue
                if ns[: len(prefix)] != prefix:
                    continue

            # Apply max_depth (relative to prefix)
            if max_depth is not None:
                prefix_len = len(prefix) if prefix else 0
                ns = ns[: prefix_len + max_depth]

            namespaces.add(ns)

        sorted_ns = sorted(namespaces)
        return sorted_ns[offset : offset + limit]


# ─── Store Conformance ────────────────────────────────────────────────


async def run_store_conformance(store: BaseStore) -> dict[str, Any]:
    """Run conformance tests against a Store implementation.

    Returns a dict with test results compatible with ConformanceReport.
    """
    from .conformance import ConformanceReport, _run_test

    report = ConformanceReport(
        suite="Store",
        implementation=type(store).__name__,
    )

    async def test_put_and_get():
        await store.aput(("users", "alice"), "profile", {"name": "Alice", "role": "admin"})
        item = await store.aget(("users", "alice"), "profile")
        assert item is not None, "put item must be retrievable"
        assert item.key == "profile"
        assert item.namespace == ("users", "alice")
        assert item.value["name"] == "Alice"
        assert item.value["role"] == "admin"

    async def test_get_nonexistent():
        item = await store.aget(("nonexistent",), "key")
        assert item is None, "nonexistent item must return None"

    async def test_update_preserves_created_at():
        await store.aput(("test",), "update-test", {"v": 1})
        item1 = await store.aget(("test",), "update-test")
        assert item1 is not None
        created = item1.created_at
        await store.aput(("test",), "update-test", {"v": 2})
        item2 = await store.aget(("test",), "update-test")
        assert item2 is not None
        assert item2.value["v"] == 2
        assert item2.created_at == created, "created_at must not change on update"
        assert item2.updated_at >= item1.updated_at

    async def test_delete():
        await store.aput(("test",), "delete-me", {"x": 1})
        await store.adelete(("test",), "delete-me")
        item = await store.aget(("test",), "delete-me")
        assert item is None, "deleted item must return None"

    async def test_delete_nonexistent():
        # Should not raise
        await store.adelete(("nonexistent",), "nope")

    async def test_search_by_prefix():
        await store.aput(("org", "teamA"), "member1", {"name": "Bob"})
        await store.aput(("org", "teamA"), "member2", {"name": "Carol"})
        await store.aput(("org", "teamB"), "member1", {"name": "Dave"})
        results = await store.asearch(("org", "teamA"))
        assert len(results) == 2, f"expected 2 results, got {len(results)}"
        names = {r.value["name"] for r in results}
        assert names == {"Bob", "Carol"}

    async def test_search_with_filter():
        await store.aput(("agents",), "backend", {"model": "sonnet", "category": "sw"})
        await store.aput(("agents",), "ai-eng", {"model": "opus", "category": "sw"})
        await store.aput(("agents",), "scout", {"model": "opus", "category": "sw"})
        results = await store.asearch(("agents",), filter={"model": {"$eq": "opus"}})
        assert len(results) == 2
        keys = {r.key for r in results}
        assert keys == {"ai-eng", "scout"}

    async def test_search_filter_operators():
        await store.aput(("scores",), "a", {"score": 10})
        await store.aput(("scores",), "b", {"score": 20})
        await store.aput(("scores",), "c", {"score": 30})
        gt = await store.asearch(("scores",), filter={"score": {"$gt": 15}})
        assert len(gt) == 2
        lte = await store.asearch(("scores",), filter={"score": {"$lte": 20}})
        assert len(lte) == 2
        ne = await store.asearch(("scores",), filter={"score": {"$ne": 20}})
        assert len(ne) == 2

    async def test_search_limit_offset():
        for i in range(5):
            await store.aput(("paged",), f"item-{i}", {"i": i})
        page1 = await store.asearch(("paged",), limit=2, offset=0)
        assert len(page1) == 2
        page2 = await store.asearch(("paged",), limit=2, offset=2)
        assert len(page2) == 2
        page3 = await store.asearch(("paged",), limit=2, offset=4)
        assert len(page3) == 1

    async def test_list_namespaces():
        await store.aput(("ns", "a"), "k", {"x": 1})
        await store.aput(("ns", "b"), "k", {"x": 2})
        await store.aput(("ns", "a", "sub"), "k", {"x": 3})
        nss = await store.alist_namespaces(prefix=("ns",))
        assert len(nss) >= 3

    async def test_list_namespaces_max_depth():
        await store.aput(("deep", "l1"), "k", {"x": 1})
        await store.aput(("deep", "l1", "l2"), "k", {"x": 2})
        await store.aput(("deep", "l1", "l2", "l3"), "k", {"x": 3})
        nss = await store.alist_namespaces(prefix=("deep",), max_depth=1)
        # All should be truncated to depth 1 from prefix
        for ns in nss:
            assert len(ns) <= 2  # prefix(1) + max_depth(1)

    async def test_namespace_isolation():
        await store.aput(("isolated", "a"), "key", {"from": "a"})
        await store.aput(("isolated", "b"), "key", {"from": "b"})
        results_a = await store.asearch(("isolated", "a"))
        results_b = await store.asearch(("isolated", "b"))
        assert len(results_a) == 1
        assert len(results_b) == 1
        assert results_a[0].value["from"] == "a"
        assert results_b[0].value["from"] == "b"

    async def test_ttl_expiration():
        await store.aput(("ttl",), "expires", {"temp": True}, ttl=0.01)
        item = await store.aget(("ttl",), "expires")
        assert item is not None, "item should exist immediately"
        import asyncio

        await asyncio.sleep(0.05)
        item = await store.aget(("ttl",), "expires")
        assert item is None, "item should have expired"

    tests = [
        ("put_and_get", test_put_and_get),
        ("get_nonexistent", test_get_nonexistent),
        ("update_preserves_created_at", test_update_preserves_created_at),
        ("delete", test_delete),
        ("delete_nonexistent", test_delete_nonexistent),
        ("search_by_prefix", test_search_by_prefix),
        ("search_with_filter", test_search_with_filter),
        ("search_filter_operators", test_search_filter_operators),
        ("search_limit_offset", test_search_limit_offset),
        ("list_namespaces", test_list_namespaces),
        ("list_namespaces_max_depth", test_list_namespaces_max_depth),
        ("namespace_isolation", test_namespace_isolation),
        ("ttl_expiration", test_ttl_expiration),
    ]

    for name, fn in tests:
        result = await _run_test(name, fn)
        report.results.append(result)

    return report
