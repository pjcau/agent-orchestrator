"""Tests for the per-skill circuit breaker middleware (scout finding #28)."""

from __future__ import annotations

import pytest

from agent_orchestrator.core.skill import (
    Skill,
    SkillRegistry,
    SkillResult,
    circuit_breaker_middleware,
)


class FlakySkill(Skill):
    """Skill that fails until told to recover; counts real executions."""

    def __init__(self, name: str = "flaky") -> None:
        self._name = name
        self.failing = True
        self.executions = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "flaky external API"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, params: dict) -> SkillResult:
        self.executions += 1
        if self.failing:
            return SkillResult(success=False, output=None, error="upstream 500")
        return SkillResult(success=True, output="ok")


def _registry(skill: Skill, **breaker_kwargs) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(skill)
    registry.use(circuit_breaker_middleware(**breaker_kwargs))
    return registry


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_opens_after_threshold_and_fails_fast(self):
        skill = FlakySkill()
        registry = _registry(skill, failure_threshold=3, cooldown_seconds=60.0)
        for _ in range(3):
            await registry.execute("flaky", {})
        assert skill.executions == 3
        # Breaker is now open: the skill must NOT run again.
        result = await registry.execute("flaky", {})
        assert result.success is False
        assert "Circuit breaker open" in (result.error or "")
        assert result.metadata.get("circuit_open") is True
        assert skill.executions == 3

    @pytest.mark.asyncio
    async def test_half_open_probe_recovers(self, monkeypatch):
        skill = FlakySkill()
        registry = _registry(skill, failure_threshold=2, cooldown_seconds=0.0)
        for _ in range(2):
            await registry.execute("flaky", {})
        # cooldown_seconds=0 -> immediately half-open; a successful probe closes.
        skill.failing = False
        result = await registry.execute("flaky", {})
        assert result.success is True
        # Fully closed again: next call passes straight through.
        result = await registry.execute("flaky", {})
        assert result.success is True
        assert skill.executions == 4

    @pytest.mark.asyncio
    async def test_failed_probe_reopens(self):
        skill = FlakySkill()
        registry = _registry(skill, failure_threshold=2, cooldown_seconds=0.0)
        for _ in range(2):
            await registry.execute("flaky", {})
        executions_before_probe = skill.executions
        await registry.execute("flaky", {})  # probe — still failing
        assert skill.executions == executions_before_probe + 1

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self):
        skill = FlakySkill()
        registry = _registry(skill, failure_threshold=3, cooldown_seconds=60.0)
        await registry.execute("flaky", {})
        await registry.execute("flaky", {})
        skill.failing = False
        await registry.execute("flaky", {})  # success resets streak
        skill.failing = True
        await registry.execute("flaky", {})
        await registry.execute("flaky", {})
        # Only 2 consecutive failures since the reset — breaker still closed.
        result = await registry.execute("flaky", {})
        assert "Circuit breaker" not in (result.error or "")

    @pytest.mark.asyncio
    async def test_scoped_to_named_skills_only(self):
        guarded = FlakySkill("guarded")
        unguarded = FlakySkill("unguarded")
        registry = SkillRegistry()
        registry.register(guarded)
        registry.register(unguarded)
        registry.use(
            circuit_breaker_middleware(
                failure_threshold=1, cooldown_seconds=60.0, skills={"guarded"}
            )
        )
        await registry.execute("guarded", {})
        await registry.execute("unguarded", {})
        # guarded is now open; unguarded keeps executing.
        await registry.execute("guarded", {})
        await registry.execute("unguarded", {})
        assert guarded.executions == 1
        assert unguarded.executions == 2

    @pytest.mark.asyncio
    async def test_breakers_are_independent_per_skill(self):
        a, b = FlakySkill("a"), FlakySkill("b")
        registry = SkillRegistry()
        registry.register(a)
        registry.register(b)
        registry.use(circuit_breaker_middleware(failure_threshold=1, cooldown_seconds=60.0))
        await registry.execute("a", {})
        result_b = await registry.execute("b", {})
        # a's open breaker must not affect b's first call.
        assert "Circuit breaker" not in (result_b.error or "")

    def test_invalid_threshold_rejected(self):
        with pytest.raises(ValueError):
            circuit_breaker_middleware(failure_threshold=0)
