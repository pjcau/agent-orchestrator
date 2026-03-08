"""Tests for task-level result caching (v1.1 Sprint 1)."""

import asyncio
import time

from agent_orchestrator.core.cache import (
    InMemoryCache,
    CachePolicy,
    CacheEntry,
    CacheStats,
    cached_node,
    make_cache_key,
)


# ─── CacheEntry ──────────────────────────────────────────────────────


class TestCacheEntry:
    def test_not_expired(self):
        entry = CacheEntry(key="k", value="v", ttl_seconds=3600)
        assert not entry.is_expired

    def test_expired(self):
        entry = CacheEntry(
            key="k",
            value="v",
            created_at=time.time() - 100,
            ttl_seconds=10,
        )
        assert entry.is_expired


# ─── CacheStats ──────────────────────────────────────────────────────


class TestCacheStats:
    def test_hit_rate_zero(self):
        stats = CacheStats()
        assert stats.hit_rate == 0.0

    def test_hit_rate(self):
        stats = CacheStats()
        stats.hits = 3
        stats.misses = 1
        assert stats.hit_rate == 0.75

    def test_to_dict(self):
        stats = CacheStats()
        stats.hits = 10
        stats.misses = 5
        d = stats.to_dict()
        assert d["hits"] == 10
        assert d["misses"] == 5
        assert d["hit_rate"] == 0.667


# ─── InMemoryCache ───────────────────────────────────────────────────


class TestInMemoryCache:
    def test_put_and_get(self):
        cache = InMemoryCache()
        cache.put("key1", {"result": 42}, node_name="test_node")
        entry = cache.get("key1")
        assert entry is not None
        assert entry.value == {"result": 42}
        assert entry.node_name == "test_node"

    def test_miss(self):
        cache = InMemoryCache()
        assert cache.get("missing") is None
        assert cache.get_stats().misses == 1

    def test_expired_entry(self):
        cache = InMemoryCache()
        cache.put("k", "v", ttl_seconds=0)
        # Force expiry
        cache._store["k"].created_at = time.time() - 10
        cache._store["k"].ttl_seconds = 1
        assert cache.get("k") is None
        assert cache.get_stats().evictions == 1

    def test_invalidate(self):
        cache = InMemoryCache()
        cache.put("k", "v")
        assert cache.invalidate("k") is True
        assert cache.invalidate("k") is False
        assert cache.get("k") is None

    def test_clear(self):
        cache = InMemoryCache()
        cache.put("a", 1)
        cache.put("b", 2)
        assert cache.clear() == 2
        assert cache.size() == 0

    def test_eviction_at_capacity(self):
        cache = InMemoryCache(max_entries=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)  # should evict oldest ("a")
        assert cache.get("a") is None
        assert cache.get("c") is not None
        assert cache.size() == 2

    def test_hit_count_increments(self):
        cache = InMemoryCache()
        cache.put("k", "v")
        cache.get("k")
        cache.get("k")
        entry = cache.get("k")
        assert entry.hit_count == 3

    def test_stats_tracking(self):
        cache = InMemoryCache()
        cache.put("k", "v")
        cache.get("k")  # hit
        cache.get("k")  # hit
        cache.get("miss")  # miss
        stats = cache.get_stats()
        assert stats.hits == 2
        assert stats.misses == 1


# ─── make_cache_key ──────────────────────────────────────────────────


class TestMakeCacheKey:
    def test_deterministic(self):
        k1 = make_cache_key("node", {"x": 1})
        k2 = make_cache_key("node", {"x": 1})
        assert k1 == k2

    def test_different_inputs(self):
        k1 = make_cache_key("node", {"x": 1})
        k2 = make_cache_key("node", {"x": 2})
        assert k1 != k2

    def test_kwargs(self):
        k1 = make_cache_key(a=1, b=2)
        k2 = make_cache_key(b=2, a=1)
        assert k1 == k2  # sort_keys ensures order independence


# ─── cached_node decorator ──────────────────────────────────────────


class TestCachedNode:
    def test_cache_hit(self):
        cache = InMemoryCache()
        call_count = 0

        @cached_node(cache)
        async def my_node(state):
            nonlocal call_count
            call_count += 1
            return {"result": state["x"] * 2}

        result1 = asyncio.get_event_loop().run_until_complete(my_node({"x": 5}))
        result2 = asyncio.get_event_loop().run_until_complete(my_node({"x": 5}))
        assert result1 == {"result": 10}
        assert result2 == {"result": 10}
        assert call_count == 1  # second call was cached

    def test_cache_miss_different_input(self):
        cache = InMemoryCache()
        call_count = 0

        @cached_node(cache)
        async def my_node(state):
            nonlocal call_count
            call_count += 1
            return {"result": state["x"]}

        asyncio.get_event_loop().run_until_complete(my_node({"x": 1}))
        asyncio.get_event_loop().run_until_complete(my_node({"x": 2}))
        assert call_count == 2

    def test_disabled_policy(self):
        cache = InMemoryCache()
        call_count = 0

        @cached_node(cache, CachePolicy(enabled=False))
        async def my_node(state):
            nonlocal call_count
            call_count += 1
            return {"result": 1}

        asyncio.get_event_loop().run_until_complete(my_node({"x": 1}))
        asyncio.get_event_loop().run_until_complete(my_node({"x": 1}))
        assert call_count == 2  # no caching

    def test_custom_key_fn(self):
        cache = InMemoryCache()
        policy = CachePolicy(cache_key_fn=lambda s: f"custom-{s.get('id')}")

        @cached_node(cache, policy)
        async def my_node(state):
            return {"out": state["id"]}

        asyncio.get_event_loop().run_until_complete(my_node({"id": "abc", "extra": 1}))
        result = asyncio.get_event_loop().run_until_complete(my_node({"id": "abc", "extra": 999}))
        assert result == {"out": "abc"}  # cached despite different "extra"

    def test_none_result_not_cached(self):
        cache = InMemoryCache()
        call_count = 0

        @cached_node(cache)
        async def my_node(state):
            nonlocal call_count
            call_count += 1
            return None

        asyncio.get_event_loop().run_until_complete(my_node({"x": 1}))
        asyncio.get_event_loop().run_until_complete(my_node({"x": 1}))
        assert call_count == 2  # None results not cached
