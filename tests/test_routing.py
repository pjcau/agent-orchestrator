"""Tests for v0.5.0 — Smart Routing & Cost Optimization."""

import pytest
from agent_orchestrator.core.health import HealthMonitor
from agent_orchestrator.core.router import (
    RouterConfig,
    RoutingStrategy,
    TaskComplexityClassifier,
    TaskRouter,
)
from agent_orchestrator.core.usage import (
    BudgetConfig,
    UsageRecord,
    UsageTracker,
)
from agent_orchestrator.core.benchmark import BenchmarkSuite
from agent_orchestrator.core.orchestrator import TaskComplexity
from agent_orchestrator.core.provider import (
    Completion,
    ModelCapabilities,
    Provider,
    StreamChunk,
    Usage,
)


# --- Mock Provider ---


class MockProvider(Provider):
    def __init__(
        self,
        model: str = "mock",
        cost_in: float = 0.0,
        cost_out: float = 0.0,
        context: int = 4096,
        coding: float = 0.5,
        reasoning: float = 0.5,
        supports_tools: bool = True,
    ):
        self._model = model
        self._cost_in = cost_in
        self._cost_out = cost_out
        self._context = context
        self._coding = coding
        self._reasoning = reasoning
        self._supports_tools = supports_tools

    async def complete(self, messages, tools=None, system=None, max_tokens=4096, temperature=0.0):
        return Completion(
            content="mock response",
            usage=Usage(input_tokens=100, output_tokens=50, cost_usd=0.001),
        )

    async def stream(self, messages, tools=None, system=None, max_tokens=4096):
        yield StreamChunk(content="mock", is_final=True)

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            max_context=self._context,
            supports_tools=self._supports_tools,
            coding_quality=self._coding,
            reasoning_quality=self._reasoning,
        )

    @property
    def input_cost_per_million(self) -> float:
        return self._cost_in

    @property
    def output_cost_per_million(self) -> float:
        return self._cost_out


# --- TaskComplexityClassifier ---


class TestTaskComplexityClassifier:
    def setup_method(self):
        self.classifier = TaskComplexityClassifier()

    def test_low_complexity_short_task(self):
        result = self.classifier.classify("summarize this text")
        assert result.level == "low"

    def test_high_complexity_keywords(self):
        result = self.classifier.classify(
            "architect a distributed system with security audit and optimize performance"
        )
        assert result.level == "high"
        assert result.requires_reasoning is True

    def test_medium_complexity_default(self):
        # >30 words to avoid "low", no high keywords to avoid "high"
        result = self.classifier.classify(
            "implement a REST API endpoint that handles user authentication "
            "and returns JSON tokens with proper validation of the request body "
            "including error messages for invalid inputs and also add logging "
            "for each request that comes in to the server application"
        )
        assert result.level == "medium"

    def test_requires_tools_detection(self):
        result = self.classifier.classify("write code to deploy the test suite")
        assert result.requires_tools is True

    def test_no_tools_needed(self):
        result = self.classifier.classify("summarize the project history")
        assert result.requires_tools is False

    def test_estimated_tokens(self):
        short = self.classifier.classify("hello")
        long_text = self.classifier.classify("word " * 500)
        assert long_text.estimated_tokens > short.estimated_tokens


# --- HealthMonitor ---


class TestHealthMonitor:
    def test_initial_health_is_available(self):
        monitor = HealthMonitor()
        assert monitor.is_available("test-provider") is True

    def test_record_success_updates_latency(self):
        monitor = HealthMonitor()
        monitor.record_success("p1", 100.0)
        monitor.record_success("p1", 200.0)
        health = monitor.get_health("p1")
        assert health.avg_latency_ms == 150.0
        assert health.total_requests == 2
        assert health.error_rate == 0.0

    def test_record_error_increments_counts(self):
        monitor = HealthMonitor()
        monitor.record_error("p1", "timeout")
        health = monitor.get_health("p1")
        assert health.total_errors == 1
        assert health.consecutive_errors == 1
        assert health.error_rate == 1.0

    def test_consecutive_errors_marks_unavailable(self):
        monitor = HealthMonitor(max_consecutive_errors=3)
        for _ in range(3):
            monitor.record_error("p1", "error")
        assert monitor.is_available("p1") is False

    def test_success_resets_consecutive_errors(self):
        monitor = HealthMonitor(max_consecutive_errors=3, error_rate_threshold=0.8)
        monitor.record_error("p1", "error")
        monitor.record_error("p1", "error")
        monitor.record_success("p1", 50.0)
        assert monitor.get_health("p1").consecutive_errors == 0
        # error_rate is 2/3 = 0.66, below 0.8 threshold
        assert monitor.is_available("p1") is True

    def test_high_error_rate_marks_unavailable(self):
        monitor = HealthMonitor(error_rate_threshold=0.5)
        # 3 errors, 1 success = 75% error rate
        monitor.record_error("p1", "e1")
        monitor.record_error("p1", "e2")
        monitor.record_error("p1", "e3")
        monitor.record_success("p1", 50.0)
        assert monitor.is_available("p1") is False

    def test_get_best_provider(self):
        monitor = HealthMonitor()
        monitor.record_success("fast", 50.0)
        monitor.record_success("slow", 500.0)
        best = monitor.get_best_provider(["fast", "slow"])
        assert best == "fast"

    def test_get_best_provider_skips_unavailable(self):
        monitor = HealthMonitor(max_consecutive_errors=1)
        monitor.record_error("bad", "error")
        monitor.record_success("good", 100.0)
        best = monitor.get_best_provider(["bad", "good"])
        assert best == "good"

    def test_get_best_provider_returns_none_if_all_down(self):
        monitor = HealthMonitor(max_consecutive_errors=1)
        monitor.record_error("p1", "error")
        monitor.record_error("p2", "error")
        assert monitor.get_best_provider(["p1", "p2"]) is None

    def test_get_all_health(self):
        monitor = HealthMonitor()
        monitor.record_success("a", 10.0)
        monitor.record_success("b", 20.0)
        all_health = monitor.get_all_health()
        assert "a" in all_health
        assert "b" in all_health


# --- TaskRouter ---


class TestTaskRouter:
    def _make_providers(self):
        return {
            "local-ollama": MockProvider("ollama-model", cost_out=0.0, coding=0.7, reasoning=0.6),
            "openrouter": MockProvider("cloud-model", cost_out=1.0, coding=0.9, reasoning=0.9),
            "anthropic": MockProvider("claude", cost_out=15.0, coding=0.95, reasoning=0.95),
        }

    def test_local_first_picks_local_for_low(self):
        providers = self._make_providers()
        router = TaskRouter(providers, config=RouterConfig(strategy=RoutingStrategy.LOCAL_FIRST))
        result = router.route("summarize this text")
        assert result is not None
        assert result.model_id == "ollama-model"

    def test_local_first_picks_cloud_for_high(self):
        providers = self._make_providers()
        router = TaskRouter(providers, config=RouterConfig(strategy=RoutingStrategy.LOCAL_FIRST))
        result = router.route("architect a distributed system with complex security audit")
        assert result is not None
        assert result.model_id != "ollama-model"

    def test_cost_optimized_picks_cheapest_for_low(self):
        providers = self._make_providers()
        router = TaskRouter(providers, config=RouterConfig(strategy=RoutingStrategy.COST_OPTIMIZED))
        result = router.route("list files", complexity=TaskComplexity(level="low"))
        assert result is not None
        assert result.output_cost_per_million == 0.0

    def test_cost_optimized_picks_expensive_for_high(self):
        providers = self._make_providers()
        router = TaskRouter(providers, config=RouterConfig(strategy=RoutingStrategy.COST_OPTIMIZED))
        result = router.route("complex task", complexity=TaskComplexity(level="high"))
        assert result is not None
        assert result.output_cost_per_million == 15.0

    def test_capability_based_routing(self):
        providers = self._make_providers()
        router = TaskRouter(
            providers,
            config=RouterConfig(
                strategy=RoutingStrategy.CAPABILITY_BASED,
                min_reasoning_quality=0.9,
            ),
        )
        result = router.route(
            "reasoning task",
            complexity=TaskComplexity(level="high", requires_reasoning=True),
        )
        assert result is not None
        # Should pick anthropic (highest combined quality)
        assert result.capabilities.reasoning_quality >= 0.9

    def test_fallback_chain(self):
        providers = self._make_providers()
        health = HealthMonitor(max_consecutive_errors=1)
        health.record_error("local-ollama", "down")  # local is down
        router = TaskRouter(
            providers,
            health_monitor=health,
            config=RouterConfig(
                strategy=RoutingStrategy.FALLBACK_CHAIN,
                fallback_chain=["local-ollama", "openrouter", "anthropic"],
            ),
        )
        result = router.route("any task")
        assert result is not None
        assert result.model_id == "cloud-model"  # skipped local, fell to openrouter

    def test_complexity_based_low_prefers_local(self):
        providers = self._make_providers()
        router = TaskRouter(
            providers, config=RouterConfig(strategy=RoutingStrategy.COMPLEXITY_BASED)
        )
        result = router.route("simple ping", complexity=TaskComplexity(level="low"))
        assert result is not None
        assert result.model_id == "ollama-model"

    def test_complexity_based_high_prefers_best_cloud(self):
        providers = self._make_providers()
        router = TaskRouter(
            providers, config=RouterConfig(strategy=RoutingStrategy.COMPLEXITY_BASED)
        )
        result = router.route("complex task", complexity=TaskComplexity(level="high"))
        assert result is not None
        assert result.model_id == "claude"

    def test_route_returns_none_when_all_unavailable(self):
        providers = {"p1": MockProvider("m1")}
        health = HealthMonitor(max_consecutive_errors=1)
        health.record_error("p1", "down")
        router = TaskRouter(
            providers,
            health_monitor=health,
            config=RouterConfig(strategy=RoutingStrategy.LOCAL_FIRST),
        )
        result = router.route("task")
        assert result is None

    def test_split_execution_falls_back_to_complexity(self):
        providers = self._make_providers()
        router = TaskRouter(
            providers, config=RouterConfig(strategy=RoutingStrategy.SPLIT_EXECUTION)
        )
        result = router.route("simple task", complexity=TaskComplexity(level="low"))
        assert result is not None

    def test_get_classifier(self):
        router = TaskRouter({})
        assert isinstance(router.get_classifier(), TaskComplexityClassifier)


# --- UsageTracker ---


class TestUsageTracker:
    def test_record_and_session_cost(self):
        tracker = UsageTracker()
        tracker.record(
            UsageRecord(
                provider="openrouter",
                model="qwen",
                input_tokens=1000,
                output_tokens=500,
                cost_usd=0.01,
            )
        )
        tracker.record(
            UsageRecord(
                provider="openrouter",
                model="qwen",
                input_tokens=500,
                output_tokens=200,
                cost_usd=0.005,
            )
        )
        assert tracker.get_session_cost() == pytest.approx(0.015)

    def test_cost_by_provider(self):
        tracker = UsageTracker()
        tracker.record(
            UsageRecord(
                provider="openrouter",
                model="m1",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.01,
            )
        )
        tracker.record(
            UsageRecord(
                provider="local-ollama",
                model="m2",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.0,
            )
        )
        by_provider = tracker.get_cost_by_provider()
        assert by_provider["openrouter"] == pytest.approx(0.01)
        assert by_provider["local-ollama"] == pytest.approx(0.0)

    def test_cost_by_agent(self):
        tracker = UsageTracker()
        tracker.record(
            UsageRecord(
                provider="p1",
                model="m1",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.01,
                agent_name="backend",
            )
        )
        tracker.record(
            UsageRecord(
                provider="p1",
                model="m1",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.02,
                agent_name="frontend",
            )
        )
        by_agent = tracker.get_cost_by_agent()
        assert by_agent["backend"] == pytest.approx(0.01)
        assert by_agent["frontend"] == pytest.approx(0.02)

    def test_budget_within_limits(self):
        tracker = UsageTracker()
        tracker.record(
            UsageRecord(
                provider="p1",
                model="m1",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.01,
                task_id="t1",
            )
        )
        status = tracker.check_budget(
            BudgetConfig(max_per_task=0.05, max_per_session=1.0),
            task_id="t1",
        )
        assert status.within_budget is True
        assert status.remaining_usd is not None
        assert status.remaining_usd > 0

    def test_budget_task_exceeded(self):
        tracker = UsageTracker()
        tracker.record(
            UsageRecord(
                provider="p1",
                model="m1",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.10,
                task_id="t1",
            )
        )
        status = tracker.check_budget(
            BudgetConfig(max_per_task=0.05),
            task_id="t1",
        )
        assert status.within_budget is False
        assert status.limit_type == "task"

    def test_budget_session_exceeded(self):
        tracker = UsageTracker()
        for i in range(20):
            tracker.record(
                UsageRecord(
                    provider="p1",
                    model="m1",
                    input_tokens=100,
                    output_tokens=50,
                    cost_usd=0.1,
                )
            )
        status = tracker.check_budget(BudgetConfig(max_per_session=1.0))
        assert status.within_budget is False
        assert status.limit_type == "session"

    def test_cost_breakdown_local_vs_cloud(self):
        tracker = UsageTracker()
        tracker.record(
            UsageRecord(
                provider="local-ollama",
                model="llama",
                input_tokens=1000,
                output_tokens=500,
                cost_usd=0.0,
            )
        )
        tracker.record(
            UsageRecord(
                provider="openrouter",
                model="qwen",
                input_tokens=1000,
                output_tokens=500,
                cost_usd=0.01,
            )
        )
        breakdown = tracker.get_cost_breakdown()
        assert breakdown.local_cost == 0.0
        assert breakdown.cloud_cost == pytest.approx(0.01)
        assert breakdown.local_tokens == 1500
        assert breakdown.cloud_tokens == 1500
        assert "local-ollama" in breakdown.by_provider
        assert "openrouter" in breakdown.by_provider

    def test_get_records_with_since_filter(self):
        import time

        tracker = UsageTracker()
        early = time.time() - 100
        tracker.record(
            UsageRecord(
                provider="p1",
                model="m1",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.01,
                timestamp=early,
            )
        )
        tracker.record(
            UsageRecord(
                provider="p1",
                model="m1",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.02,
            )
        )
        recent = tracker.get_records(since=early + 50)
        assert len(recent) == 1
        assert recent[0].cost_usd == pytest.approx(0.02)

    def test_daily_cost(self):
        tracker = UsageTracker()
        tracker.record(
            UsageRecord(
                provider="p1",
                model="m1",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.05,
            )
        )
        assert tracker.get_daily_cost() == pytest.approx(0.05)


# --- BenchmarkSuite ---


class TestBenchmarkSuite:
    @pytest.mark.asyncio
    async def test_run_benchmark(self):
        suite = BenchmarkSuite()
        provider = MockProvider("test-model")
        result = await suite.run_benchmark(provider, "Explain recursion.", "reasoning")
        assert result.model_id == "test-model"
        assert result.task_type == "reasoning"
        assert result.latency_ms > 0
        assert result.tokens_per_second > 0
        assert result.cost_usd == pytest.approx(0.001)

    @pytest.mark.asyncio
    async def test_compare_models(self):
        suite = BenchmarkSuite()
        providers = {
            "fast": MockProvider("fast-model"),
            "slow": MockProvider("slow-model"),
        }
        results = await suite.compare_models(providers, "test task")
        assert len(results) == 2
        # Results should be sorted by latency
        assert results[0].latency_ms <= results[1].latency_ms

    @pytest.mark.asyncio
    async def test_get_results_accumulates(self):
        suite = BenchmarkSuite()
        p1 = MockProvider("m1")
        p2 = MockProvider("m2")
        await suite.run_benchmark(p1, "task1", "coding")
        await suite.run_benchmark(p2, "task2", "reasoning")
        assert len(suite.get_results()) == 2

    @pytest.mark.asyncio
    async def test_get_best_for_task(self):
        suite = BenchmarkSuite()
        await suite.run_benchmark(MockProvider("m1"), "task", "coding")
        await suite.run_benchmark(MockProvider("m2"), "task", "coding")
        best = suite.get_best_for_task("coding")
        assert best is not None
        assert best.task_type == "coding"

    def test_get_best_for_task_empty(self):
        suite = BenchmarkSuite()
        assert suite.get_best_for_task("nonexistent") is None

    @pytest.mark.asyncio
    async def test_provider_key_override(self):
        suite = BenchmarkSuite()
        result = await suite.run_benchmark(
            MockProvider("model"), "task", "test", provider_key="custom-key"
        )
        assert result.provider_key == "custom-key"
