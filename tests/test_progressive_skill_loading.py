"""Tests for Progressive Skill Loading feature.

Covers:
- SkillSummary dataclass and get_summaries()
- get_full_instructions() for valid and unknown skills
- SkillLoaderSkill execute (success + error paths)
- skill_loads_total metric increments on each load
"""

from __future__ import annotations

import pytest

from agent_orchestrator.core.skill import (
    Skill,
    SkillRegistry,
    SkillResult,
    SkillSummary,
)
from agent_orchestrator.core.metrics import MetricsRegistry, default_metrics
from agent_orchestrator.skills.skill_loader import SkillLoaderSkill


# ─── Fixture skills ──────────────────────────────────────────────────


class DummySkill(Skill):
    """Skill with full_instructions for testing."""

    @property
    def name(self) -> str:
        return "dummy"

    @property
    def description(self) -> str:
        return "A dummy skill"

    @property
    def category(self) -> str:
        return "testing"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def full_instructions(self) -> str | None:
        return "These are the full instructions for the dummy skill."

    async def execute(self, params: dict) -> SkillResult:
        return SkillResult(success=True, output="dummy executed")


class MinimalSkill(Skill):
    """Skill without full_instructions (uses default None)."""

    @property
    def name(self) -> str:
        return "minimal"

    @property
    def description(self) -> str:
        return "A minimal skill"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, params: dict) -> SkillResult:
        return SkillResult(success=True, output="minimal executed")


# ─── Tests: SkillSummary ─────────────────────────────────────────────


class TestSkillSummary:
    def test_defaults(self):
        s = SkillSummary(name="foo", description="bar")
        assert s.name == "foo"
        assert s.description == "bar"
        assert s.category == "general"

    def test_custom_category(self):
        s = SkillSummary(name="x", description="y", category="finance")
        assert s.category == "finance"


# ─── Tests: SkillRegistry.get_summaries ──────────────────────────────


class TestGetSummaries:
    def test_returns_compact_list(self):
        registry = SkillRegistry()
        registry.register(DummySkill())
        registry.register(MinimalSkill())

        summaries = registry.get_summaries()
        assert len(summaries) == 2
        assert all(isinstance(s, SkillSummary) for s in summaries)

        names = {s.name for s in summaries}
        assert names == {"dummy", "minimal"}

    def test_summary_contains_correct_fields(self):
        registry = SkillRegistry()
        registry.register(DummySkill())

        summaries = registry.get_summaries()
        s = summaries[0]
        assert s.name == "dummy"
        assert s.description == "A dummy skill"
        assert s.category == "testing"

    def test_empty_registry(self):
        registry = SkillRegistry()
        assert registry.get_summaries() == []

    def test_default_category_for_minimal_skill(self):
        registry = SkillRegistry()
        registry.register(MinimalSkill())

        summaries = registry.get_summaries()
        assert summaries[0].category == "general"


# ─── Tests: SkillRegistry.get_full_instructions ─────────────────────


class TestGetFullInstructions:
    def test_returns_full_content_for_valid_skill(self):
        registry = SkillRegistry()
        registry.register(DummySkill())

        result = registry.get_full_instructions("dummy")
        assert result == "These are the full instructions for the dummy skill."

    def test_returns_none_for_unknown_skill(self):
        registry = SkillRegistry()
        result = registry.get_full_instructions("nonexistent")
        assert result is None

    def test_returns_none_for_skill_without_instructions(self):
        registry = SkillRegistry()
        registry.register(MinimalSkill())

        result = registry.get_full_instructions("minimal")
        assert result is None


# ─── Tests: SkillLoaderSkill ─────────────────────────────────────────


class TestSkillLoaderSkill:
    def test_properties(self):
        registry = SkillRegistry()
        loader = SkillLoaderSkill(registry)

        assert loader.name == "load_skill"
        assert loader.category == "meta"
        assert "skill_name" in str(loader.parameters)
        assert loader.full_instructions is not None

    @pytest.mark.asyncio
    async def test_returns_instructions_for_valid_skill(self):
        registry = SkillRegistry()
        registry.register(DummySkill())
        loader = SkillLoaderSkill(registry)

        result = await loader.execute({"skill_name": "dummy"})
        assert result.success is True
        assert result.output == "These are the full instructions for the dummy skill."

    @pytest.mark.asyncio
    async def test_returns_error_for_unknown_skill(self):
        registry = SkillRegistry()
        loader = SkillLoaderSkill(registry)

        result = await loader.execute({"skill_name": "nonexistent"})
        assert result.success is False
        assert "Unknown skill: nonexistent" in str(result.output)

    @pytest.mark.asyncio
    async def test_returns_error_for_missing_param(self):
        registry = SkillRegistry()
        loader = SkillLoaderSkill(registry)

        result = await loader.execute({})
        assert result.success is False
        assert "skill_name" in str(result.error)

    @pytest.mark.asyncio
    async def test_session_counter_increments(self):
        registry = SkillRegistry()
        registry.register(DummySkill())
        loader = SkillLoaderSkill(registry)

        assert loader.loads_per_session == 0
        await loader.execute({"skill_name": "dummy"})
        assert loader.loads_per_session == 1
        await loader.execute({"skill_name": "dummy"})
        assert loader.loads_per_session == 2

    @pytest.mark.asyncio
    async def test_session_counter_reset(self):
        registry = SkillRegistry()
        registry.register(DummySkill())
        loader = SkillLoaderSkill(registry)

        await loader.execute({"skill_name": "dummy"})
        assert loader.loads_per_session == 1
        loader.reset_session()
        assert loader.loads_per_session == 0


# ─── Tests: Metric integration ──────────────────────────────────────


class TestSkillLoadMetrics:
    @pytest.mark.asyncio
    async def test_metric_increments_on_each_load(self):
        registry = SkillRegistry()
        registry.register(DummySkill())

        metrics_reg = MetricsRegistry()
        counter = metrics_reg.counter(
            "skill_loads_total",
            "Total on-demand skill loads",
        )

        loader = SkillLoaderSkill(registry, loads_counter=counter)

        assert counter.get() == 0.0

        await loader.execute({"skill_name": "dummy"})
        assert counter.get() == 1.0

        await loader.execute({"skill_name": "dummy"})
        assert counter.get() == 2.0

    @pytest.mark.asyncio
    async def test_metric_not_incremented_on_failure(self):
        registry = SkillRegistry()

        metrics_reg = MetricsRegistry()
        counter = metrics_reg.counter("skill_loads_total", "")

        loader = SkillLoaderSkill(registry, loads_counter=counter)

        await loader.execute({"skill_name": "nonexistent"})
        assert counter.get() == 0.0

    def test_default_metrics_includes_skill_loads_total(self):
        reg = default_metrics()
        all_metrics = reg.get_all()
        assert "skill_loads_total" in all_metrics
        assert all_metrics["skill_loads_total"]["type"] == "counter"
        assert all_metrics["skill_loads_total"]["value"] == 0.0


# ─── Tests: SkillLoaderSkill registered in SkillRegistry ────────────


class TestSkillLoaderRegistration:
    @pytest.mark.asyncio
    async def test_loader_works_through_registry_execute(self):
        """Verify load_skill works when invoked via SkillRegistry.execute()."""
        registry = SkillRegistry()
        registry.register(DummySkill())

        loader = SkillLoaderSkill(registry)
        registry.register(loader)

        result = await registry.execute("load_skill", {"skill_name": "dummy"})
        assert result.success is True
        assert "full instructions" in result.output

    @pytest.mark.asyncio
    async def test_loader_in_summaries(self):
        """load_skill should appear in summaries with category 'meta'."""
        registry = SkillRegistry()
        loader = SkillLoaderSkill(registry)
        registry.register(loader)

        summaries = registry.get_summaries()
        names = {s.name: s for s in summaries}
        assert "load_skill" in names
        assert names["load_skill"].category == "meta"
