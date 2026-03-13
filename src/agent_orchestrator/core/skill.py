"""Skill — provider-independent capabilities that agents can invoke.

Includes a middleware pattern (SkillMiddleware) for composable interceptors:
retry, caching, logging, authorization, rate limiting.

Inspired by LangGraph's ToolCallWrapper (analysis/langgraph/18-tool-node.md).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


@dataclass
class SkillResult:
    success: bool
    output: Any
    error: str | None = None

    def __str__(self) -> str:
        if self.success:
            return str(self.output)
        return f"Error: {self.error}"


@dataclass(frozen=True)
class SkillRequest:
    """Immutable request object passed through the middleware chain.

    Use override() to create a modified copy without mutating the original.
    """

    skill_name: str
    params: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)

    def override(self, **kwargs: Any) -> SkillRequest:
        """Create a new request with overridden fields."""
        return SkillRequest(
            skill_name=kwargs.get("skill_name", self.skill_name),
            params=kwargs.get("params", self.params),
            metadata=kwargs.get("metadata", self.metadata),
        )


# Type for middleware: takes request + next_fn, returns result
SkillMiddleware = Callable[
    [SkillRequest, Callable[[SkillRequest], Awaitable[SkillResult]]],
    Awaitable[SkillResult],
]


class Skill(ABC):
    """A tool/capability that agents can use. Provider-independent."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> dict:
        """JSON Schema for the skill's parameters."""
        ...

    @abstractmethod
    async def execute(self, params: dict) -> SkillResult: ...


class SkillRegistry:
    """Central registry of all available skills with middleware support."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._middlewares: list[SkillMiddleware] = []

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def use(self, middleware: SkillMiddleware) -> None:
        """Add a middleware to the execution chain.

        Middlewares execute in registration order (first registered = outermost).
        Each middleware calls next_fn(request) to continue the chain.
        """
        self._middlewares.append(middleware)

    async def execute(self, name: str, params: dict) -> SkillResult:
        skill = self._skills.get(name)
        if skill is None:
            return SkillResult(success=False, output=None, error=f"Unknown skill: {name}")

        request = SkillRequest(skill_name=name, params=params)

        # Build the middleware chain (innermost = actual skill execution)
        async def core_executor(req: SkillRequest) -> SkillResult:
            s = self._skills.get(req.skill_name)
            if s is None:
                return SkillResult(
                    success=False, output=None, error=f"Unknown skill: {req.skill_name}"
                )
            try:
                return await s.execute(req.params)
            except Exception as e:
                return SkillResult(success=False, output=None, error=str(e))

        # Wrap middlewares from inside out
        chain = core_executor
        for mw in reversed(self._middlewares):
            chain = _wrap_middleware(mw, chain)

        return await chain(request)

    def list_skills(self) -> list[str]:
        return list(self._skills.keys())

    def to_tool_definitions(self) -> list[dict]:
        """Export all skills as tool definitions (for LLM APIs)."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "parameters": s.parameters,
            }
            for s in self._skills.values()
        ]


def _wrap_middleware(
    mw: SkillMiddleware,
    next_fn: Callable[[SkillRequest], Awaitable[SkillResult]],
) -> Callable[[SkillRequest], Awaitable[SkillResult]]:
    """Wrap a middleware around a next function."""

    async def wrapped(request: SkillRequest) -> SkillResult:
        return await mw(request, next_fn)

    return wrapped


# ─── Built-in Middlewares ─────────────────────────────────────────────


def logging_middleware(
    logger: Callable[[str], None] | None = None,
) -> SkillMiddleware:
    """Log skill execution: name, params, duration, success/error."""

    log = logger or (lambda msg: None)

    async def middleware(
        request: SkillRequest,
        next_fn: Callable[[SkillRequest], Awaitable[SkillResult]],
    ) -> SkillResult:
        start = time.monotonic()
        log(f"Skill '{request.skill_name}' starting with params={request.params}")
        result = await next_fn(request)
        duration_ms = (time.monotonic() - start) * 1000
        if result.success:
            log(f"Skill '{request.skill_name}' completed in {duration_ms:.1f}ms")
        else:
            log(f"Skill '{request.skill_name}' failed in {duration_ms:.1f}ms: {result.error}")
        return result

    return middleware


def retry_middleware(max_retries: int = 2) -> SkillMiddleware:
    """Retry failed skill executions up to max_retries times."""

    async def middleware(
        request: SkillRequest,
        next_fn: Callable[[SkillRequest], Awaitable[SkillResult]],
    ) -> SkillResult:
        last_result: SkillResult | None = None
        for attempt in range(1 + max_retries):
            result = await next_fn(request)
            if result.success:
                return result
            last_result = result
        return last_result  # type: ignore[return-value]

    return middleware


def timeout_middleware(timeout_seconds: float = 30.0) -> SkillMiddleware:
    """Enforce a timeout on skill execution."""

    import asyncio

    async def middleware(
        request: SkillRequest,
        next_fn: Callable[[SkillRequest], Awaitable[SkillResult]],
    ) -> SkillResult:
        try:
            return await asyncio.wait_for(next_fn(request), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            return SkillResult(
                success=False,
                output=None,
                error=f"Skill '{request.skill_name}' timed out after {timeout_seconds}s",
            )

    return middleware


def cache_middleware(
    cache: Any,
    cacheable_skills: set[str] | None = None,
    ttl_seconds: int = 120,
    invalidate_on: dict[str, str] | None = None,
) -> SkillMiddleware:
    """Cache results of idempotent skills.

    Args:
        cache: A BaseCache instance (e.g. InMemoryCache).
        cacheable_skills: Set of skill names to cache. If None, caches all skills.
        ttl_seconds: Time-to-live for cached entries.
        invalidate_on: Map of {skill_name: param_key} — when this skill runs
            successfully, invalidate the cache for the param value as a file_read key.
            Example: {"file_write": "file_path"} invalidates file_read cache for that path.
    """
    from .cache import make_cache_key

    _invalidate_on = invalidate_on or {}

    async def middleware(
        request: SkillRequest,
        next_fn: Callable[[SkillRequest], Awaitable[SkillResult]],
    ) -> SkillResult:
        # Check if this skill triggers cache invalidation
        if request.skill_name in _invalidate_on:
            result = await next_fn(request)
            if result.success:
                param_key = _invalidate_on[request.skill_name]
                param_val = request.params.get(param_key, "")
                if param_val:
                    inv_key = make_cache_key("file_read", {"file_path": param_val})
                    cache.invalidate(inv_key)
            return result

        # Only cache specified skills
        if cacheable_skills and request.skill_name not in cacheable_skills:
            return await next_fn(request)

        key = make_cache_key(request.skill_name, request.params)
        entry = cache.get(key)
        if entry is not None:
            return entry.value

        result = await next_fn(request)
        if result.success:
            cache.put(key, result, ttl_seconds=ttl_seconds, node_name=request.skill_name)
        return result

    return middleware
