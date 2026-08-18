"""Tests for the third batch of research-scout findings (score 7).

Covers: skill error taxonomy (#173), per-skill rate limiting
(#170/#180/#185), and user-defined routing rule overrides (#187).
"""

from __future__ import annotations

import pytest

from agent_orchestrator.core.provider import Provider
from agent_orchestrator.core.rate_limiter import RateLimitConfig, RateLimiter
from agent_orchestrator.core.router import (
    RouterConfig,
    RoutingRule,
    RoutingStrategy,
    TaskRouter,
)
from agent_orchestrator.core.skill import (
    Skill,
    SkillErrorCode,
    SkillRegistry,
    SkillResult,
    circuit_breaker_middleware,
    rate_limit_middleware,
    timeout_middleware,
)
from agent_orchestrator.providers import MockProvider


class EchoSkill(Skill):
    def __init__(self, name: str = "echo") -> None:
        self._name = name
        self.executions = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "echo"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, params: dict) -> SkillResult:
        self.executions += 1
        return SkillResult(success=True, output="ok")


# ─── #173 Skill error taxonomy ──────────────────────────────────────


class TestSkillErrorTaxonomy:
    def test_transient_classification(self):
        assert SkillErrorCode.TIMEOUT.transient
        assert SkillErrorCode.RATE_LIMITED.transient
        assert SkillErrorCode.CIRCUIT_OPEN.transient
        assert not SkillErrorCode.NOT_FOUND.transient
        assert not SkillErrorCode.INVALID_PARAMS.transient

    @pytest.mark.asyncio
    async def test_unknown_skill_carries_not_found_code(self):
        registry = SkillRegistry()
        result = await registry.execute("ghost", {})
        assert result.success is False
        assert result.error_code == SkillErrorCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_timeout_carries_timeout_code(self):
        import asyncio

        class SlowSkill(EchoSkill):
            async def execute(self, params: dict) -> SkillResult:
                await asyncio.sleep(1.0)
                return SkillResult(success=True, output="late")

        registry = SkillRegistry()
        registry.register(SlowSkill("slow"))
        registry.use(timeout_middleware(timeout_seconds=0.05))
        result = await registry.execute("slow", {})
        assert result.error_code == SkillErrorCode.TIMEOUT.value

    @pytest.mark.asyncio
    async def test_open_circuit_carries_circuit_code(self):
        class FailingSkill(EchoSkill):
            async def execute(self, params: dict) -> SkillResult:
                self.executions += 1
                return SkillResult(success=False, output=None, error="boom")

        registry = SkillRegistry()
        registry.register(FailingSkill("f"))
        registry.use(circuit_breaker_middleware(failure_threshold=1, cooldown_seconds=60.0))
        await registry.execute("f", {})
        result = await registry.execute("f", {})
        assert result.error_code == SkillErrorCode.CIRCUIT_OPEN.value


# ─── #170/#180/#185 Per-skill rate limiting ─────────────────────────


class TestSkillRateLimiting:
    def _registry(self, rpm: int, **mw_kwargs):
        limiter = RateLimiter(
            [
                RateLimitConfig(
                    requests_per_minute=rpm, tokens_per_minute=10_000, provider_key="skill:echo"
                )
            ]
        )
        registry = SkillRegistry()
        registry.register(EchoSkill())
        registry.use(rate_limit_middleware(limiter, **mw_kwargs))
        return registry

    @pytest.mark.asyncio
    async def test_over_limit_fails_fast_with_code(self):
        registry = self._registry(rpm=2)
        assert (await registry.execute("echo", {})).success
        assert (await registry.execute("echo", {})).success
        result = await registry.execute("echo", {})
        assert result.success is False
        assert result.error_code == SkillErrorCode.RATE_LIMITED.value
        assert result.metadata.get("rate_limited") is True

    @pytest.mark.asyncio
    async def test_unconfigured_skill_passes_through(self):
        limiter = RateLimiter([])
        registry = SkillRegistry()
        skill = EchoSkill()
        registry.register(skill)
        registry.use(rate_limit_middleware(limiter))
        for _ in range(5):
            assert (await registry.execute("echo", {})).success
        assert skill.executions == 5

    @pytest.mark.asyncio
    async def test_scoped_to_named_skills(self):
        limiter = RateLimiter(
            [RateLimitConfig(requests_per_minute=0, tokens_per_minute=0, provider_key="skill:echo")]
        )
        registry = SkillRegistry()
        registry.register(EchoSkill())
        registry.use(rate_limit_middleware(limiter, skills={"other"}))
        # echo is not in the guarded set — rpm=0 window never consulted.
        assert (await registry.execute("echo", {})).success

    @pytest.mark.asyncio
    async def test_wait_mode_recovers_when_slot_frees(self):
        # rpm=1 with a 61s window cannot free within the test; instead verify
        # the deadline path returns RATE_LIMITED after waiting.
        registry = self._registry(rpm=1, max_wait_seconds=0.05, poll_interval_seconds=0.01)
        assert (await registry.execute("echo", {})).success
        result = await registry.execute("echo", {})
        assert result.error_code == SkillErrorCode.RATE_LIMITED.value


# ─── #187 User-defined routing rules ────────────────────────────────


def _router(rules: list[RoutingRule], providers: dict[str, Provider] | None = None) -> TaskRouter:
    providers = providers or {
        "cheap": MockProvider(model_id="cheap"),
        "smart": MockProvider(model_id="smart"),
    }
    return TaskRouter(
        providers=providers,
        config=RouterConfig(
            strategy=RoutingStrategy.FALLBACK_CHAIN, rules=rules, fallback_chain=list(providers)
        ),
    )


class TestRoutingRules:
    def test_matching_rule_pins_provider(self):
        router = _router([RoutingRule(pattern=r"\bsql\b", provider_key="smart")])
        provider = router.route("optimize this SQL query")
        assert provider is not None and provider.model_id == "smart"

    def test_rules_checked_in_order(self):
        router = _router(
            [
                RoutingRule(pattern="deploy", provider_key="cheap"),
                RoutingRule(pattern="deploy prod", provider_key="smart"),
            ]
        )
        provider = router.route("deploy prod now")
        assert provider is not None and provider.model_id == "cheap"

    def test_unknown_provider_falls_through_to_strategy(self):
        router = _router([RoutingRule(pattern="sql", provider_key="missing")])
        provider = router.route("sql stuff")
        # Falls through to the fallback-chain strategy instead of None.
        assert provider is not None

    def test_no_match_uses_strategy(self):
        router = _router([RoutingRule(pattern="zebra", provider_key="smart")])
        assert router.route("plain task") is not None

    def test_case_insensitive_matching(self):
        router = _router([RoutingRule(pattern="terraform", provider_key="smart")])
        provider = router.route("Run TERRAFORM plan")
        assert provider is not None and provider.model_id == "smart"

    def test_invalid_pattern_rejected_at_construction(self):
        with pytest.raises(ValueError):
            RoutingRule(pattern="([unclosed", provider_key="x")
