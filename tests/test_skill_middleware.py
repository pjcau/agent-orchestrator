"""Tests for Skill middleware pattern."""

from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.core.skill import (
    Skill,
    SkillRegistry,
    SkillResult,
    SkillRequest,
    logging_middleware,
    retry_middleware,
    timeout_middleware,
)


# ─── Test Skill implementations ──────────────────────────────────────


class EchoSkill(Skill):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echoes input"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, params: dict) -> SkillResult:
        return SkillResult(success=True, output=params.get("text", ""))


class FailingSkill(Skill):
    """Skill that fails N times then succeeds."""

    def __init__(self, fail_count: int = 1):
        self._fail_count = fail_count
        self._calls = 0

    @property
    def name(self) -> str:
        return "failing"

    @property
    def description(self) -> str:
        return "Fails then succeeds"

    @property
    def parameters(self) -> dict:
        return {"type": "object"}

    async def execute(self, params: dict) -> SkillResult:
        self._calls += 1
        if self._calls <= self._fail_count:
            return SkillResult(success=False, output=None, error="intentional failure")
        return SkillResult(success=True, output="recovered")


class SlowSkill(Skill):
    @property
    def name(self) -> str:
        return "slow"

    @property
    def description(self) -> str:
        return "Takes a long time"

    @property
    def parameters(self) -> dict:
        return {"type": "object"}

    async def execute(self, params: dict) -> SkillResult:
        await asyncio.sleep(5)
        return SkillResult(success=True, output="done")


# ─── SkillRequest ─────────────────────────────────────────────────────


class TestSkillRequest:
    def test_immutable(self):
        req = SkillRequest(skill_name="echo", params={"text": "hello"})
        assert req.skill_name == "echo"
        assert req.params == {"text": "hello"}

    def test_override(self):
        req = SkillRequest(skill_name="echo", params={"text": "hello"})
        req2 = req.override(params={"text": "world"})
        assert req.params == {"text": "hello"}  # original unchanged
        assert req2.params == {"text": "world"}
        assert req2.skill_name == "echo"

    def test_override_skill_name(self):
        req = SkillRequest(skill_name="a", params={})
        req2 = req.override(skill_name="b")
        assert req2.skill_name == "b"

    def test_metadata(self):
        req = SkillRequest(skill_name="x", params={}, metadata={"agent": "backend"})
        assert req.metadata["agent"] == "backend"


# ─── SkillRegistry with middleware ────────────────────────────────────


class TestSkillRegistryMiddleware:
    @pytest.fixture
    def registry(self):
        reg = SkillRegistry()
        reg.register(EchoSkill())
        return reg

    @pytest.mark.asyncio
    async def test_execute_without_middleware(self, registry):
        result = await registry.execute("echo", {"text": "hi"})
        assert result.success
        assert result.output == "hi"

    @pytest.mark.asyncio
    async def test_execute_unknown_skill(self, registry):
        result = await registry.execute("nonexistent", {})
        assert not result.success
        assert "Unknown skill" in result.error

    @pytest.mark.asyncio
    async def test_middleware_chain(self, registry):
        call_order = []

        async def mw_a(request, next_fn):
            call_order.append("a_before")
            result = await next_fn(request)
            call_order.append("a_after")
            return result

        async def mw_b(request, next_fn):
            call_order.append("b_before")
            result = await next_fn(request)
            call_order.append("b_after")
            return result

        registry.use(mw_a)
        registry.use(mw_b)

        result = await registry.execute("echo", {"text": "test"})
        assert result.success
        # a is outermost, b is inner
        assert call_order == ["a_before", "b_before", "b_after", "a_after"]

    @pytest.mark.asyncio
    async def test_middleware_can_modify_request(self, registry):
        async def add_prefix(request, next_fn):
            new_params = dict(request.params)
            new_params["text"] = "prefix:" + new_params.get("text", "")
            return await next_fn(request.override(params=new_params))

        registry.use(add_prefix)
        result = await registry.execute("echo", {"text": "hello"})
        assert result.success
        assert result.output == "prefix:hello"

    @pytest.mark.asyncio
    async def test_middleware_can_short_circuit(self, registry):
        async def block_all(request, next_fn):
            return SkillResult(success=False, output=None, error="blocked")

        registry.use(block_all)
        result = await registry.execute("echo", {"text": "test"})
        assert not result.success
        assert result.error == "blocked"


# ─── Built-in middlewares ─────────────────────────────────────────────


class TestLoggingMiddleware:
    @pytest.mark.asyncio
    async def test_logs_success(self):
        logs = []
        reg = SkillRegistry()
        reg.register(EchoSkill())
        reg.use(logging_middleware(logger=logs.append))
        result = await reg.execute("echo", {"text": "hi"})
        assert result.success
        assert len(logs) == 2
        assert "starting" in logs[0]
        assert "completed" in logs[1]

    @pytest.mark.asyncio
    async def test_logs_failure(self):
        logs = []
        reg = SkillRegistry()
        reg.register(FailingSkill(fail_count=999))
        reg.use(logging_middleware(logger=logs.append))
        result = await reg.execute("failing", {})
        assert not result.success
        assert "failed" in logs[1]


class TestRetryMiddleware:
    @pytest.mark.asyncio
    async def test_retry_recovers(self):
        reg = SkillRegistry()
        skill = FailingSkill(fail_count=1)
        reg.register(skill)
        reg.use(retry_middleware(max_retries=2))
        result = await reg.execute("failing", {})
        assert result.success
        assert result.output == "recovered"

    @pytest.mark.asyncio
    async def test_retry_exhausted(self):
        reg = SkillRegistry()
        skill = FailingSkill(fail_count=10)
        reg.register(skill)
        reg.use(retry_middleware(max_retries=2))
        result = await reg.execute("failing", {})
        assert not result.success

    @pytest.mark.asyncio
    async def test_no_retry_on_success(self):
        reg = SkillRegistry()
        reg.register(EchoSkill())
        reg.use(retry_middleware(max_retries=3))
        result = await reg.execute("echo", {"text": "ok"})
        assert result.success
        assert result.output == "ok"


class TestTimeoutMiddleware:
    @pytest.mark.asyncio
    async def test_timeout_triggers(self):
        reg = SkillRegistry()
        reg.register(SlowSkill())
        reg.use(timeout_middleware(timeout_seconds=0.05))
        result = await reg.execute("slow", {})
        assert not result.success
        assert "timed out" in result.error

    @pytest.mark.asyncio
    async def test_no_timeout(self):
        reg = SkillRegistry()
        reg.register(EchoSkill())
        reg.use(timeout_middleware(timeout_seconds=5.0))
        result = await reg.execute("echo", {"text": "fast"})
        assert result.success


# ─── Combined middlewares ─────────────────────────────────────────────


class TestCombinedMiddlewares:
    @pytest.mark.asyncio
    async def test_logging_plus_retry(self):
        logs = []
        reg = SkillRegistry()
        skill = FailingSkill(fail_count=1)
        reg.register(skill)
        reg.use(logging_middleware(logger=logs.append))
        reg.use(retry_middleware(max_retries=2))
        result = await reg.execute("failing", {})
        assert result.success
        # Logging wraps the whole retry chain
        assert len(logs) == 2
        assert "starting" in logs[0]
        assert "completed" in logs[1]
