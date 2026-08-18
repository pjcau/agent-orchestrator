"""Tests for the second batch of research-scout findings (score 8).

Covers: per-agent budget caps (#74), skill lifecycle hooks (#61), skill
manifests (#71), provider quota gauge (#64), unified notification dispatcher
(#56/#70), semantic cache (#68/#69), ensemble provider (#58), and parallel
branch exploration (#60).
"""

from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.core.cache import SemanticCache
from agent_orchestrator.core.checkpoint import Checkpoint, InMemoryCheckpointer
from agent_orchestrator.core.ensemble import EnsembleProvider, EnsembleStrategy
from agent_orchestrator.core.exploration import explore
from agent_orchestrator.core.health import HealthMonitor, QuotaConfig
from agent_orchestrator.core.notifications import (
    CallableBackend,
    LogBackend,
    Notification,
    NotificationDispatcher,
    NotificationLevel,
)
from agent_orchestrator.core.provider import Completion, Usage
from agent_orchestrator.core.skill import Skill, SkillManifest, SkillRegistry, SkillResult
from agent_orchestrator.core.usage import BudgetConfig, UsageRecord, UsageTracker
from agent_orchestrator.providers import MockProvider


def _rec(agent: str, cost: float) -> UsageRecord:
    return UsageRecord(
        provider="p", model="m", input_tokens=1, output_tokens=1, cost_usd=cost, agent_name=agent
    )


# ─── #74 Per-agent budget ───────────────────────────────────────────


class TestPerAgentBudget:
    def test_agent_over_cap_breaches(self):
        tracker = UsageTracker()
        tracker.record(_rec("backend", 3.0))
        status = tracker.check_budget(BudgetConfig(max_per_agent=2.0), agent_name="backend")
        assert status.within_budget is False
        assert status.limit_type == "agent"

    def test_other_agents_unaffected(self):
        tracker = UsageTracker()
        tracker.record(_rec("backend", 3.0))
        status = tracker.check_budget(BudgetConfig(max_per_agent=2.0), agent_name="frontend")
        assert status.within_budget is True

    def test_agent_override_takes_precedence(self):
        tracker = UsageTracker()
        tracker.record(_rec("backend", 3.0))
        budget = BudgetConfig(max_per_agent=2.0, agent_overrides={"backend": 5.0})
        assert tracker.check_budget(budget, agent_name="backend").within_budget is True

    def test_tightest_remaining_includes_agent(self):
        tracker = UsageTracker()
        tracker.record(_rec("backend", 1.5))
        budget = BudgetConfig(max_per_session=10.0, max_per_agent=2.0)
        status = tracker.check_budget(budget, agent_name="backend")
        assert status.within_budget is True
        assert status.remaining_usd == pytest.approx(0.5)

    def test_get_agent_cost(self):
        tracker = UsageTracker()
        tracker.record(_rec("a", 1.0))
        tracker.record(_rec("a", 0.5))
        tracker.record(_rec("b", 9.0))
        assert tracker.get_agent_cost("a") == pytest.approx(1.5)


# ─── #61 / #71 Skill lifecycle + manifest ───────────────────────────


class LifecycleSkill(Skill):
    def __init__(self, name: str = "life", fail_setup: bool = False) -> None:
        self._name = name
        self._fail_setup = fail_setup
        self.events: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "stateful skill"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"q": {"type": "string"}}}

    async def setup(self) -> None:
        if self._fail_setup:
            raise RuntimeError("cannot connect")
        self.events.append("setup")

    async def teardown(self) -> None:
        self.events.append("teardown")

    async def execute(self, params: dict) -> SkillResult:
        return SkillResult(success=True, output="ok")


class TestSkillLifecycle:
    @pytest.mark.asyncio
    async def test_startup_and_shutdown_drive_hooks(self):
        skill = LifecycleSkill()
        registry = SkillRegistry()
        registry.register(skill)
        await registry.startup()
        await registry.startup()  # idempotent
        await registry.shutdown()
        assert skill.events == ["setup", "teardown"]

    @pytest.mark.asyncio
    async def test_failed_setup_unregisters_skill(self):
        good, bad = LifecycleSkill("good"), LifecycleSkill("bad", fail_setup=True)
        registry = SkillRegistry()
        registry.register(good)
        registry.register(bad)
        await registry.startup()
        assert registry.get("bad") is None
        assert registry.get("good") is not None


class TestSkillManifest:
    def test_roundtrip(self):
        manifest = LifecycleSkill().to_manifest()
        restored = SkillManifest.from_dict(manifest.to_dict())
        assert restored.name == "life"
        assert restored.parameters["type"] == "object"

    def test_registry_export(self):
        registry = SkillRegistry()
        registry.register(LifecycleSkill())
        manifests = registry.export_manifests()
        assert len(manifests) == 1
        assert manifests[0]["name"] == "life"

    @pytest.mark.parametrize(
        "bad",
        [
            "not a dict",
            {},
            {"name": "", "description": "d", "parameters": {}},
            {"name": "x" * 200, "description": "d", "parameters": {}},
            {"name": "ok", "description": "d", "parameters": "nope"},
        ],
    )
    def test_invalid_manifests_rejected(self, bad):
        with pytest.raises(ValueError):
            SkillManifest.from_dict(bad)  # type: ignore[arg-type]


# ─── #64 Provider quota gauge ───────────────────────────────────────


class TestQuotaGauge:
    def test_full_tank_without_quota(self):
        monitor = HealthMonitor()
        status = monitor.get_quota_status("openai")
        assert status.remaining_fraction == 1.0
        assert status.exhausted is False

    def test_gauge_tracks_calls_and_tokens(self):
        monitor = HealthMonitor()
        monitor.set_quota("openai", QuotaConfig(max_calls=4, max_tokens=1000))
        monitor.record_success("openai", 100.0, tokens=250)
        monitor.record_success("openai", 100.0, tokens=250)
        status = monitor.get_quota_status("openai")
        assert status.calls_used == 2
        assert status.tokens_used == 500
        assert status.calls_remaining == 2
        assert status.tokens_remaining == 500
        assert status.remaining_fraction == pytest.approx(0.5)

    def test_exhausted_quota_marks_unavailable(self):
        monitor = HealthMonitor()
        monitor.set_quota("openai", QuotaConfig(max_calls=1))
        assert monitor.is_available("openai") is True
        monitor.record_success("openai", 50.0)
        assert monitor.get_quota_status("openai").exhausted is True
        assert monitor.is_available("openai") is False

    def test_window_expiry_refills_the_tank(self):
        monitor = HealthMonitor()
        monitor.set_quota("openai", QuotaConfig(max_calls=1, window_seconds=0.0))
        monitor.record_success("openai", 50.0)
        # window 0 -> the event immediately falls out of the sliding window
        assert monitor.get_quota_status("openai").exhausted is False


# ─── #56/#70 Notification dispatcher ────────────────────────────────


class TestNotificationDispatcher:
    @pytest.mark.asyncio
    async def test_fan_out_and_results(self):
        received: list[str] = []

        async def capture(n: Notification) -> bool:
            received.append(n.title)
            return True

        dispatcher = NotificationDispatcher()
        dispatcher.register("log", LogBackend())
        dispatcher.register("cb", CallableBackend(capture))
        results = await dispatcher.notify(Notification("deploy", "done"))
        assert results == {"log": True, "cb": True}
        assert received == ["deploy"]

    @pytest.mark.asyncio
    async def test_min_level_filtering(self):
        calls: list[str] = []

        async def capture(n: Notification) -> bool:
            calls.append(n.title)
            return True

        dispatcher = NotificationDispatcher()
        dispatcher.register(
            "critical-only", CallableBackend(capture), min_level=NotificationLevel.CRITICAL
        )
        assert await dispatcher.notify(Notification("info", "x")) == {}
        results = await dispatcher.notify(
            Notification("boom", "x", level=NotificationLevel.CRITICAL)
        )
        assert results == {"critical-only": True}

    @pytest.mark.asyncio
    async def test_failing_backend_is_isolated(self):
        async def broken(_: Notification) -> bool:
            raise RuntimeError("channel down")

        async def working(_: Notification) -> bool:
            return True

        dispatcher = NotificationDispatcher()
        dispatcher.register("broken", CallableBackend(broken))
        dispatcher.register("working", CallableBackend(working))
        results = await dispatcher.notify(Notification("t", "b"))
        assert results == {"broken": False, "working": True}

    @pytest.mark.asyncio
    async def test_backend_subset_selection(self):
        async def yes(_: Notification) -> bool:
            return True

        dispatcher = NotificationDispatcher()
        dispatcher.register("a", CallableBackend(yes))
        dispatcher.register("b", CallableBackend(yes))
        results = await dispatcher.notify(Notification("t", "b"), backends=["b"])
        assert results == {"b": True}


# ─── #68/#69 Semantic cache ─────────────────────────────────────────


def _toy_embedder(text: str) -> list[float]:
    """Deterministic bag-of-chars embedding — enough to test the mechanics."""
    vec = [0.0] * 26
    for ch in text.lower():
        if "a" <= ch <= "z":
            vec[ord(ch) - ord("a")] += 1.0
    return vec


class TestSemanticCache:
    def test_exact_and_near_match_hit(self):
        cache = SemanticCache(_toy_embedder, threshold=0.95)
        cache.put("summarize the report", "cached-answer")
        entry = cache.get("summarize the report")
        assert entry is not None and entry.value == "cached-answer"
        # Same letters, different order — cosine 1.0 with bag-of-chars.
        assert cache.get("the report summarize") is not None

    def test_dissimilar_text_misses(self):
        cache = SemanticCache(_toy_embedder, threshold=0.95)
        cache.put("summarize the report", "cached-answer")
        assert cache.get("zzzzzz qqqq") is None
        assert cache.get_stats().misses == 1

    def test_threshold_is_conservative(self):
        loose = SemanticCache(_toy_embedder, threshold=0.5)
        strict = SemanticCache(_toy_embedder, threshold=0.999)
        for cache in (loose, strict):
            cache.put("hello world", 1)
        assert loose.get("hello word") is not None
        assert strict.get("completely different phrase") is None

    def test_eviction_and_size(self):
        cache = SemanticCache(_toy_embedder, threshold=0.99, max_entries=2)
        cache.put("aaa", 1)
        cache.put("bbb", 2)
        cache.put("ccc", 3)
        assert cache.size() == 2
        assert cache.get_stats().evictions == 1

    def test_invalid_threshold_rejected(self):
        with pytest.raises(ValueError):
            SemanticCache(_toy_embedder, threshold=0.0)


# ─── #58 Ensemble provider ──────────────────────────────────────────


def _completion(content: str, cost: float = 0.01) -> Completion:
    return Completion(content=content, usage=Usage(10, 10, cost))


class TestEnsembleProvider:
    @pytest.mark.asyncio
    async def test_first_success_returns_fastest(self):
        slow = MockProvider([_completion("slow")], model_id="slow", latency_seconds=0.2)
        fast = MockProvider([_completion("fast")], model_id="fast", latency_seconds=0.0)
        ensemble = EnsembleProvider([slow, fast], EnsembleStrategy.FIRST_SUCCESS)
        result = await ensemble.complete([])
        assert result.content == "fast"

    @pytest.mark.asyncio
    async def test_consensus_majority_wins(self):
        members = [
            MockProvider([_completion("Paris ")]),
            MockProvider([_completion("paris")]),  # normalised match
            MockProvider([_completion("London")]),
        ]
        ensemble = EnsembleProvider(members, EnsembleStrategy.CONSENSUS)
        result = await ensemble.complete([])
        assert result.content.strip().lower() == "paris"

    @pytest.mark.asyncio
    async def test_consensus_aggregates_cost_of_all_members(self):
        members = [MockProvider([_completion("x", cost=0.01)]) for _ in range(3)]
        ensemble = EnsembleProvider(members, EnsembleStrategy.CONSENSUS)
        result = await ensemble.complete([])
        assert result.usage.cost_usd == pytest.approx(0.03)

    @pytest.mark.asyncio
    async def test_best_of_uses_scorer(self):
        members = [
            MockProvider([_completion("short")]),
            MockProvider([_completion("a much longer answer")]),
        ]
        ensemble = EnsembleProvider(
            members, EnsembleStrategy.BEST_OF, scorer=lambda c: len(c.content)
        )
        result = await ensemble.complete([])
        assert result.content == "a much longer answer"

    def test_validation(self):
        single = [MockProvider()]
        with pytest.raises(ValueError):
            EnsembleProvider(single)
        with pytest.raises(ValueError):
            EnsembleProvider([MockProvider(), MockProvider()], EnsembleStrategy.BEST_OF)

    def test_metadata_reflects_members(self):
        ensemble = EnsembleProvider(
            [MockProvider(model_id="a"), MockProvider(model_id="b")],
        )
        assert "a+b" in ensemble.model_id
        assert ensemble.input_cost_per_million == 0.0


# ─── #60 Parallel branch exploration ────────────────────────────────


class TestExploration:
    @pytest.mark.asyncio
    async def test_branches_run_on_private_forks(self):
        checkpointer = InMemoryCheckpointer()
        await checkpointer.save(Checkpoint("c0", "base", {"v": 1}, next_nodes=["n"], step_index=0))

        async def branch(ctx):
            return f"{ctx.branch_id}:{ctx.head.state['v']}"

        outcome = await explore(checkpointer, "base", {"a": branch, "b": branch})
        assert outcome.winner is not None
        assert {r.output for r in outcome.results} == {"a:1", "b:1"}
        assert await checkpointer.get_latest("base:a") is not None
        assert await checkpointer.get_latest("base:b") is not None

    @pytest.mark.asyncio
    async def test_scorer_picks_winner_and_failures_isolated(self):
        checkpointer = InMemoryCheckpointer()
        await checkpointer.save(Checkpoint("c0", "base", {}, next_nodes=[], step_index=0))

        async def good(ctx):
            return "yy"

        async def better(ctx):
            return "yyyy"

        async def broken(ctx):
            raise RuntimeError("branch exploded")

        outcome = await explore(
            checkpointer,
            "base",
            {"good": good, "broken": broken, "better": better},
            scorer=lambda out: len(out),
        )
        assert outcome.winner is not None and outcome.winner.branch_id == "better"
        failed = next(r for r in outcome.results if r.branch_id == "broken")
        assert failed.success is False and "exploded" in (failed.error or "")

    @pytest.mark.asyncio
    async def test_empty_base_thread_yields_no_winner(self):
        checkpointer = InMemoryCheckpointer()

        async def branch(ctx):
            return "x"

        outcome = await explore(checkpointer, "ghost", {"a": branch})
        assert outcome.winner is None
        assert outcome.results[0].success is False

    @pytest.mark.asyncio
    async def test_max_parallel_respected(self):
        checkpointer = InMemoryCheckpointer()
        await checkpointer.save(Checkpoint("c0", "base", {}, next_nodes=[], step_index=0))
        concurrent = 0
        peak = 0

        async def branch(ctx):
            nonlocal concurrent, peak
            concurrent += 1
            peak = max(peak, concurrent)
            await asyncio.sleep(0.01)
            concurrent -= 1
            return "ok"

        await explore(
            checkpointer,
            "base",
            {f"b{i}": branch for i in range(6)},
            max_parallel=2,
        )
        assert peak <= 2

    @pytest.mark.asyncio
    async def test_validation(self):
        checkpointer = InMemoryCheckpointer()

        async def branch(ctx):
            return None

        with pytest.raises(ValueError):
            await explore(checkpointer, "b", {})
        with pytest.raises(ValueError):
            await explore(checkpointer, "b", {"a": branch}, max_parallel=0)
