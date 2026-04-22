"""Tests for PromptRegistry (PR #56)."""

import pytest

from agent_orchestrator.core.metrics import MetricsRegistry
from agent_orchestrator.core.prompt_registry import (
    PROMPT_NAMESPACE,
    PromptRegistry,
    PromptTemplate,
)
from agent_orchestrator.core.store import InMemoryStore


@pytest.fixture
def store():
    return InMemoryStore()


@pytest.fixture
def registry(store):
    return PromptRegistry(store)


@pytest.fixture
def metrics_registry():
    return MetricsRegistry()


@pytest.fixture
def registry_with_metrics(store, metrics_registry):
    return PromptRegistry(store, metrics=metrics_registry)


class TestPromptTemplate:
    def test_format_renders_placeholders(self):
        t = PromptTemplate(name="t", content="hello {name}")
        assert t.format(name="world") == "hello world"

    def test_to_dict_round_trip(self):
        t = PromptTemplate(
            name="x",
            content="c",
            tags=["a", "b"],
            category="cat",
            version="2",
            description="d",
            metadata={"k": "v"},
        )
        d = t.to_dict()
        back = PromptTemplate.from_dict(d)
        assert back.name == t.name
        assert back.content == t.content
        assert back.tags == t.tags
        assert back.category == t.category
        assert back.version == t.version
        assert back.description == t.description
        assert back.metadata == t.metadata


class TestRegisterAndGet:
    async def test_register_then_get(self, registry):
        tpl = PromptTemplate(name="code_review", content="Review: {code}")
        await registry.register(tpl)
        got = await registry.get("code_review")
        assert got is not None
        assert got.content == "Review: {code}"

    async def test_get_unknown_returns_none(self, registry):
        assert await registry.get("nope") is None

    async def test_register_is_upsert(self, registry):
        t1 = PromptTemplate(name="x", content="v1", version="1")
        await registry.register(t1)
        t2 = PromptTemplate(name="x", content="v2", version="2")
        await registry.register(t2)
        got = await registry.get("x")
        assert got is not None
        assert got.content == "v2"
        assert got.version == "2"

    async def test_delete(self, registry):
        await registry.register(PromptTemplate(name="tmp", content="c"))
        await registry.delete("tmp")
        assert await registry.get("tmp") is None

    async def test_delete_unknown_is_noop(self, registry):
        await registry.delete("does-not-exist")


class TestSearch:
    async def test_search_by_single_tag(self, registry):
        await registry.register(PromptTemplate(name="a", content="c", tags=["python"]))
        await registry.register(PromptTemplate(name="b", content="c", tags=["rust"]))
        results = await registry.search(tags=["python"])
        assert len(results) == 1
        assert results[0].name == "a"

    async def test_search_and_intersection(self, registry):
        await registry.register(PromptTemplate(name="a", content="c", tags=["python", "testing"]))
        await registry.register(PromptTemplate(name="b", content="c", tags=["python"]))
        results = await registry.search(tags=["python", "testing"])
        assert len(results) == 1
        assert results[0].name == "a"

    async def test_search_by_category(self, registry):
        await registry.register(PromptTemplate(name="a", content="c", category="sw"))
        await registry.register(PromptTemplate(name="b", content="c", category="fin"))
        results = await registry.search(category="sw")
        assert len(results) == 1
        assert results[0].name == "a"

    async def test_search_combined_tags_and_category(self, registry):
        await registry.register(PromptTemplate(name="a", content="c", tags=["x"], category="sw"))
        await registry.register(PromptTemplate(name="b", content="c", tags=["x"], category="fin"))
        results = await registry.search(tags=["x"], category="sw")
        assert len(results) == 1
        assert results[0].name == "a"

    async def test_list_all_returns_everything(self, registry):
        for i in range(5):
            await registry.register(PromptTemplate(name=f"t{i}", content="c"))
        results = await registry.list_all(limit=10)
        assert len(results) == 5

    async def test_limit_applied(self, registry):
        for i in range(5):
            await registry.register(PromptTemplate(name=f"t{i}", content="c"))
        results = await registry.list_all(limit=3)
        assert len(results) == 3


class TestMetrics:
    async def test_hit_and_miss_counters(self, registry_with_metrics, metrics_registry):
        await registry_with_metrics.register(PromptTemplate(name="hit", content="c"))
        await registry_with_metrics.get("hit")
        await registry_with_metrics.get("miss")

        lookups = metrics_registry.counter("prompt_registry_lookups_total", "").get()
        hits = metrics_registry.counter("prompt_registry_hits_total", "").get()
        misses = metrics_registry.counter("prompt_registry_misses_total", "").get()

        assert lookups == 2
        assert hits == 1
        assert misses == 1

    async def test_search_records_lookup(self, registry_with_metrics, metrics_registry):
        await registry_with_metrics.register(PromptTemplate(name="x", content="c", tags=["a"]))
        await registry_with_metrics.search(tags=["a"])
        await registry_with_metrics.search(tags=["unknown"])

        lookups = metrics_registry.counter("prompt_registry_lookups_total", "").get()
        assert lookups == 2

    async def test_latency_histogram_records(self, registry_with_metrics, metrics_registry):
        await registry_with_metrics.register(PromptTemplate(name="x", content="c"))
        await registry_with_metrics.get("x")
        hist = metrics_registry.histogram("prompt_registry_lookup_duration_seconds", "")
        assert hist.get_count() == 1


class TestNamespaceIsolation:
    async def test_writes_happen_under_prompt_namespace(self, store):
        reg = PromptRegistry(store)
        await reg.register(PromptTemplate(name="n", content="c"))
        # The underlying store should have the item under PROMPT_NAMESPACE
        item = await store.aget(PROMPT_NAMESPACE, "n")
        assert item is not None
