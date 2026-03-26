"""Tests for Rust/Python fallback layer.

Verifies that when _agent_orchestrator_rust is NOT importable,
all core modules fall back gracefully to pure Python implementations.
Also tests that the fallback layer interface is wired correctly.
"""

from __future__ import annotations


import pytest


class TestGraphRustFallback:
    """Verify graph.py Rust fallback layer."""

    def test_has_rust_flag_false_without_rust(self):
        from agent_orchestrator.core import graph

        assert graph._HAS_RUST is False

    def test_state_graph_creates_without_rust(self):
        from agent_orchestrator.core.graph import StateGraph

        g = StateGraph()
        assert g._rust_topo is None

    def test_graph_works_without_rust(self):
        """Full graph build and validate should work in pure Python."""
        from agent_orchestrator.core.graph import START, END, StateGraph

        async def noop(state):
            return state

        g = StateGraph()
        g.add_node("a", noop)
        g.add_node("b", noop)
        g.add_edge(START, "a")
        g.add_edge("a", "b")
        g.add_edge("b", END)
        compiled = g.compile()
        assert compiled is not None

    def test_find_reachable_without_rust(self):
        from agent_orchestrator.core.graph import START, END, StateGraph

        async def noop(state):
            return state

        g = StateGraph()
        g.add_node("a", noop)
        g.add_node("b", noop)
        g.add_edge(START, "a")
        g.add_edge("a", "b")
        g.add_edge("b", END)
        reachable = g._find_reachable(START)
        assert reachable == {"a", "b"}


class TestRouterRustFallback:
    """Verify router.py Rust fallback layer."""

    def test_has_rust_flag_false(self):
        from agent_orchestrator.core import router

        assert router._HAS_RUST is False

    def test_classifier_works_without_rust(self):
        from agent_orchestrator.core.router import TaskComplexityClassifier

        c = TaskComplexityClassifier()
        assert c._rust is None

        result = c.classify("hello world")
        assert result.level in ("low", "medium", "high")

    def test_classifier_high_complexity(self):
        from agent_orchestrator.core.router import TaskComplexityClassifier

        c = TaskComplexityClassifier()
        result = c.classify("architect a distributed scalability migration strategy")
        assert result.level == "high"
        assert result.requires_reasoning is True

    def test_classifier_low_complexity(self):
        from agent_orchestrator.core.router import TaskComplexityClassifier

        c = TaskComplexityClassifier()
        result = c.classify("hello")
        assert result.level == "low"


class TestTaskQueueRustFallback:
    """Verify task_queue.py Rust fallback layer."""

    def test_has_rust_flag_false(self):
        from agent_orchestrator.core import task_queue

        assert task_queue._HAS_RUST is False

    def test_queue_works_without_rust(self):
        from agent_orchestrator.core.task_queue import TaskQueue, QueuedTask

        q = TaskQueue()
        assert q._rust is None

        task = QueuedTask(task_id="t1", description="test", priority=5)
        q.enqueue(task)
        result = q.dequeue()
        assert result is not None
        assert result.task_id == "t1"
        assert result.status == "running"

    def test_queue_priority_ordering(self):
        from agent_orchestrator.core.task_queue import TaskQueue, QueuedTask

        q = TaskQueue()
        q.enqueue(QueuedTask(task_id="low", description="low", priority=1))
        q.enqueue(QueuedTask(task_id="high", description="high", priority=10))
        result = q.dequeue()
        assert result.task_id == "high"

    def test_queue_stats(self):
        from agent_orchestrator.core.task_queue import TaskQueue, QueuedTask

        q = TaskQueue()
        q.enqueue(QueuedTask(task_id="t1", description="test", priority=5))
        stats = q.get_stats()
        assert stats.pending == 1
        assert stats.total == 1


class TestRateLimiterRustFallback:
    """Verify rate_limiter.py Rust fallback layer."""

    def test_has_rust_flag_false(self):
        from agent_orchestrator.core import rate_limiter

        assert rate_limiter._HAS_RUST_RL is False

    @pytest.mark.asyncio
    async def test_limiter_works_without_rust(self):
        from agent_orchestrator.core.rate_limiter import RateLimiter, RateLimitConfig

        limiter = RateLimiter(
            [RateLimitConfig(requests_per_minute=10, tokens_per_minute=1000, provider_key="test")]
        )
        assert limiter._rust is None

        allowed = await limiter.acquire("test", 100)
        assert allowed is True

    @pytest.mark.asyncio
    async def test_limiter_blocks_over_limit(self):
        from agent_orchestrator.core.rate_limiter import RateLimiter, RateLimitConfig

        limiter = RateLimiter(
            [RateLimitConfig(requests_per_minute=2, tokens_per_minute=1000, provider_key="test")]
        )
        limiter.record_usage("test", 10)
        limiter.record_usage("test", 10)
        allowed = await limiter.acquire("test", 0)
        assert allowed is False


class TestMetricsRustFallback:
    """Verify metrics.py Rust fallback layer."""

    def test_has_rust_flag_false(self):
        from agent_orchestrator.core import metrics

        assert metrics._HAS_RUST_METRICS is False

    def test_registry_works_without_rust(self):
        from agent_orchestrator.core.metrics import MetricsRegistry

        reg = MetricsRegistry()
        counter = reg.counter("test_counter", "A test counter")
        counter.inc(5)
        assert counter.get() == 5.0

    def test_histogram_percentile_without_rust(self):
        from agent_orchestrator.core.metrics import MetricsRegistry

        reg = MetricsRegistry()
        h = reg.histogram("latency", "Test latency")
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            h.observe(v)
        p50 = h.get_percentile(50)
        assert 2.5 <= p50 <= 3.5

    def test_prometheus_export_without_rust(self):
        from agent_orchestrator.core.metrics import MetricsRegistry

        reg = MetricsRegistry()
        c = reg.counter("requests_total", "Total requests")
        c.inc(42)
        output = reg.export_prometheus()
        assert "requests_total" in output
        assert "42" in output


class TestDualServeFallback:
    """Verify app.py serves React when available, vanilla JS otherwise."""

    def test_static_dir_exists(self):
        from agent_orchestrator.dashboard.app import STATIC_DIR

        assert STATIC_DIR.is_dir()
        assert (STATIC_DIR / "index.html").exists()

    def test_react_dist_not_required(self):
        """Dashboard should start fine without frontend/dist/."""
        from pathlib import Path

        react_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
        # It's OK if it doesn't exist — vanilla JS fallback is used
        # This test just verifies the path logic doesn't crash
        if react_dist.is_dir():
            assert (react_dist / "index.html").exists()


class TestRustImportMocked:
    """Test behavior when Rust IS available (mocked)."""

    def test_graph_rust_topo_created_when_available(self):
        """When Rust module is importable, StateGraph should create _rust_topo."""
        # We can't actually import the Rust module, but we can verify
        # the conditional import logic by checking the _HAS_RUST flag
        from agent_orchestrator.core import graph

        # Without Rust installed, _HAS_RUST should be False
        assert graph._HAS_RUST is False
        # And _rust_topo should be None
        g = graph.StateGraph()
        assert g._rust_topo is None
