"""Tests for task-level result caching (v1.1 Sprint 1).

Covers: InMemoryCache, CachePolicy, cached_node, make_cache_key,
LLM node caching (Level 1), skill cache middleware (Level 2).
"""

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
from agent_orchestrator.core.skill import (
    Skill,
    SkillRegistry,
    SkillResult,
    cache_middleware,
)
from agent_orchestrator.core.provider import (
    Completion,
    ModelCapabilities,
    Provider,
    StreamChunk,
    Usage,
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

        result1 = asyncio.run(my_node({"x": 5}))
        result2 = asyncio.run(my_node({"x": 5}))
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

        asyncio.run(my_node({"x": 1}))
        asyncio.run(my_node({"x": 2}))
        assert call_count == 2

    def test_disabled_policy(self):
        cache = InMemoryCache()
        call_count = 0

        @cached_node(cache, CachePolicy(enabled=False))
        async def my_node(state):
            nonlocal call_count
            call_count += 1
            return {"result": 1}

        asyncio.run(my_node({"x": 1}))
        asyncio.run(my_node({"x": 1}))
        assert call_count == 2  # no caching

    def test_custom_key_fn(self):
        cache = InMemoryCache()
        policy = CachePolicy(cache_key_fn=lambda s: f"custom-{s.get('id')}")

        @cached_node(cache, policy)
        async def my_node(state):
            return {"out": state["id"]}

        asyncio.run(my_node({"id": "abc", "extra": 1}))
        result = asyncio.run(my_node({"id": "abc", "extra": 999}))
        assert result == {"out": "abc"}  # cached despite different "extra"

    def test_none_result_not_cached(self):
        cache = InMemoryCache()
        call_count = 0

        @cached_node(cache)
        async def my_node(state):
            nonlocal call_count
            call_count += 1
            return None

        asyncio.run(my_node({"x": 1}))
        asyncio.run(my_node({"x": 1}))
        assert call_count == 2  # None results not cached


# ─── Fake provider for LLM node tests ──────────────────────────────


class _FakeProvider(Provider):
    """Minimal provider for testing llm_node caching."""

    def __init__(self, model_id: str = "fake-model"):
        self._model_id = model_id
        self.call_count = 0

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(max_context=4096, max_output_tokens=1024)

    @property
    def input_cost_per_million(self) -> float:
        return 1.0

    @property
    def output_cost_per_million(self) -> float:
        return 2.0

    async def complete(self, messages, **kwargs) -> Completion:
        self.call_count += 1
        content = messages[-1].content if messages else ""
        return Completion(
            content=f"response to: {content}",
            usage=Usage(input_tokens=10, output_tokens=20, cost_usd=0.001),
        )

    async def stream(self, messages, **kwargs):
        yield StreamChunk(content="chunk", usage=Usage(input_tokens=5, output_tokens=5))


# ─── LLM Node Caching (Level 1) ────────────────────────────────────


class TestLlmNodeCache:
    """Test that llm_node() respects cache_policy."""

    def test_cache_hit_skips_llm_call(self):
        from agent_orchestrator.core.llm_nodes import llm_node

        provider = _FakeProvider()
        cache = InMemoryCache()
        node = llm_node(
            provider=provider,
            system="test",
            prompt_key="input",
            output_key="output",
            cache_policy=CachePolicy(ttl_seconds=300),
            cache=cache,
        )

        r1 = asyncio.run(node({"input": "hello"}))
        r2 = asyncio.run(node({"input": "hello"}))
        assert r1["output"] == r2["output"]
        assert provider.call_count == 1  # second call was cached
        assert cache.get_stats().hits == 1

    def test_different_prompts_are_separate(self):
        from agent_orchestrator.core.llm_nodes import llm_node

        provider = _FakeProvider()
        cache = InMemoryCache()
        node = llm_node(
            provider=provider,
            system="test",
            cache_policy=CachePolicy(ttl_seconds=300),
            cache=cache,
        )

        asyncio.run(node({"input": "hello"}))
        asyncio.run(node({"input": "world"}))
        assert provider.call_count == 2  # different inputs = different calls

    def test_temperature_bypasses_cache(self):
        from agent_orchestrator.core.llm_nodes import llm_node

        provider = _FakeProvider()
        cache = InMemoryCache()
        node = llm_node(
            provider=provider,
            system="test",
            temperature=0.7,
            cache_policy=CachePolicy(ttl_seconds=300),
            cache=cache,
        )

        asyncio.run(node({"input": "hello"}))
        asyncio.run(node({"input": "hello"}))
        assert provider.call_count == 2  # temperature > 0 bypasses cache
        assert cache.get_stats().hits == 0

    def test_no_cache_policy_means_no_caching(self):
        from agent_orchestrator.core.llm_nodes import llm_node

        provider = _FakeProvider()
        node = llm_node(
            provider=provider,
            system="test",
        )

        asyncio.run(node({"input": "hello"}))
        asyncio.run(node({"input": "hello"}))
        assert provider.call_count == 2  # no caching

    def test_cache_with_prompt_template(self):
        from agent_orchestrator.core.llm_nodes import llm_node

        provider = _FakeProvider()
        cache = InMemoryCache()
        node = llm_node(
            provider=provider,
            system="test",
            prompt_template=lambda s: f"analyze: {s['code']}",
            output_key="analysis",
            cache_policy=CachePolicy(ttl_seconds=300),
            cache=cache,
        )

        r1 = asyncio.run(node({"code": "x = 1"}))
        r2 = asyncio.run(node({"code": "x = 1"}))
        assert r1["analysis"] == r2["analysis"]
        assert provider.call_count == 1

    def test_disabled_policy(self):
        from agent_orchestrator.core.llm_nodes import llm_node

        provider = _FakeProvider()
        cache = InMemoryCache()
        node = llm_node(
            provider=provider,
            system="test",
            cache_policy=CachePolicy(enabled=False),
            cache=cache,
        )

        asyncio.run(node({"input": "hello"}))
        asyncio.run(node({"input": "hello"}))
        assert provider.call_count == 2


class TestMultiProviderNodeCache:
    """Test caching on multi_provider_node."""

    def test_cache_hit_skips_all_providers(self):
        from agent_orchestrator.core.llm_nodes import multi_provider_node

        p1 = _FakeProvider("model-a")
        p2 = _FakeProvider("model-b")
        cache = InMemoryCache()
        node = multi_provider_node(
            providers=[p1, p2],
            system="test",
            cache_policy=CachePolicy(ttl_seconds=300),
            cache=cache,
        )

        asyncio.run(node({"input": "hello"}))
        asyncio.run(node({"input": "hello"}))
        assert p1.call_count == 1
        assert p2.call_count == 0
        assert cache.get_stats().hits == 1


# ─── Skill Cache Middleware (Level 2) ──────────────────────────────


class _FakeSkill(Skill):
    """Fake skill for testing cache middleware."""

    def __init__(self, name: str = "file_read"):
        self._name = name
        self.call_count = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "fake"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"file_path": {"type": "string"}}}

    async def execute(self, params: dict) -> SkillResult:
        self.call_count += 1
        return SkillResult(success=True, output=f"content of {params.get('file_path', '')}")


class TestSkillCacheMiddleware:
    """Test cache_middleware for skill execution."""

    def test_caches_whitelisted_skill(self):
        cache = InMemoryCache()
        skill = _FakeSkill("file_read")
        registry = SkillRegistry()
        registry.register(skill)
        registry.use(
            cache_middleware(
                cache=cache,
                cacheable_skills={"file_read"},
                ttl_seconds=60,
            )
        )

        asyncio.run(registry.execute("file_read", {"file_path": "/a.txt"}))
        asyncio.run(registry.execute("file_read", {"file_path": "/a.txt"}))
        assert skill.call_count == 1
        assert cache.get_stats().hits == 1

    def test_does_not_cache_non_whitelisted(self):
        cache = InMemoryCache()
        skill = _FakeSkill("shell_exec")
        registry = SkillRegistry()
        registry.register(skill)
        registry.use(
            cache_middleware(
                cache=cache,
                cacheable_skills={"file_read"},
                ttl_seconds=60,
            )
        )

        asyncio.run(registry.execute("shell_exec", {"command": "ls"}))
        asyncio.run(registry.execute("shell_exec", {"command": "ls"}))
        assert skill.call_count == 2
        assert cache.get_stats().hits == 0

    def test_write_invalidates_read_cache(self):
        cache = InMemoryCache()
        read_skill = _FakeSkill("file_read")
        write_skill = _FakeSkill("file_write")
        registry = SkillRegistry()
        registry.register(read_skill)
        registry.register(write_skill)
        registry.use(
            cache_middleware(
                cache=cache,
                cacheable_skills={"file_read"},
                ttl_seconds=60,
                invalidate_on={"file_write": "file_path"},
            )
        )

        # Read file -> cached
        asyncio.run(registry.execute("file_read", {"file_path": "/a.txt"}))
        asyncio.run(registry.execute("file_read", {"file_path": "/a.txt"}))
        assert read_skill.call_count == 1  # second read was cached

        # Write to same file -> invalidates cache
        asyncio.run(registry.execute("file_write", {"file_path": "/a.txt", "content": "new"}))

        # Read again -> cache miss (invalidated)
        asyncio.run(registry.execute("file_read", {"file_path": "/a.txt"}))
        assert read_skill.call_count == 2  # had to re-read

    def test_different_paths_cached_separately(self):
        cache = InMemoryCache()
        skill = _FakeSkill("file_read")
        registry = SkillRegistry()
        registry.register(skill)
        registry.use(
            cache_middleware(
                cache=cache,
                cacheable_skills={"file_read"},
                ttl_seconds=60,
            )
        )

        asyncio.run(registry.execute("file_read", {"file_path": "/a.txt"}))
        asyncio.run(registry.execute("file_read", {"file_path": "/b.txt"}))
        assert skill.call_count == 2  # different paths

        asyncio.run(registry.execute("file_read", {"file_path": "/a.txt"}))
        assert skill.call_count == 2  # /a.txt was cached

    def test_failed_result_not_cached(self):
        cache = InMemoryCache()

        class FailingSkill(Skill):
            call_count = 0

            @property
            def name(self):
                return "file_read"  # noqa: E704

            @property
            def description(self):
                return "f"  # noqa: E704

            @property
            def parameters(self):
                return {}  # noqa: E704

            async def execute(self, params):
                self.call_count += 1
                return SkillResult(success=False, output=None, error="not found")

        skill = FailingSkill()
        registry = SkillRegistry()
        registry.register(skill)
        registry.use(
            cache_middleware(
                cache=cache,
                cacheable_skills={"file_read"},
                ttl_seconds=60,
            )
        )

        asyncio.run(registry.execute("file_read", {"file_path": "/missing.txt"}))
        asyncio.run(registry.execute("file_read", {"file_path": "/missing.txt"}))
        assert skill.call_count == 2  # errors are not cached


# ─── get_llm_cache / get_tool_cache accessors ──────────────────────


class TestCacheAccessors:
    def test_get_llm_cache(self):
        from agent_orchestrator.core.llm_nodes import get_llm_cache

        cache = get_llm_cache()
        assert isinstance(cache, InMemoryCache)

    def test_get_tool_cache(self):
        from agent_orchestrator.dashboard.agent_runner import get_tool_cache

        cache = get_tool_cache()
        assert isinstance(cache, InMemoryCache)
