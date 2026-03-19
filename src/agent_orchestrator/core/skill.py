"""Skill — provider-independent capabilities that agents can invoke.

Includes a middleware pattern (SkillMiddleware) for composable interceptors:
retry, caching, logging, authorization, rate limiting.

Supports an optional ``_description`` parameter on every tool call.  When the
LLM (or caller) includes ``_description`` in the params dict, it is extracted
before execution, logged, and propagated through the middleware chain via
``SkillRequest.metadata["tool_description"]``.

Inspired by LangGraph's ToolCallWrapper (analysis/langgraph/18-tool-node.md).
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class SkillSummary:
    """Compact skill summary for inclusion in system prompts.

    Contains only name, description, and category to minimize token usage.
    Full instructions are loaded on demand via SkillLoaderSkill.
    """

    name: str
    description: str
    category: str = "general"


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

    @property
    def category(self) -> str:
        """Optional category for grouping skills. Defaults to 'general'."""
        return "general"

    @property
    def full_instructions(self) -> str | None:
        """Optional detailed instructions loaded on demand.

        Return None if the skill has no extended instructions beyond its
        description.  Subclasses override this to provide rich documentation
        that is only loaded when an agent invokes ``load_skill``.
        """
        return None

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

        # Extract optional _description before forwarding params to the skill
        clean_params = dict(params)
        tool_description = clean_params.pop("_description", None)

        metadata: dict[str, Any] = {}
        if tool_description:
            metadata["tool_description"] = tool_description
            safe_desc = str(tool_description).replace("\n", " ").replace("\r", " ")
            safe_name = str(name).replace("\n", " ").replace("\r", " ")
            logger.info("Tool %s: %s", safe_name, safe_desc)

        request = SkillRequest(skill_name=name, params=clean_params, metadata=metadata)

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

    def get_summaries(self) -> list[SkillSummary]:
        """Return compact summaries of all registered skills.

        Intended for embedding in system prompts to minimise token usage.
        Agents can then call ``load_skill`` for full instructions on demand.
        """
        return [
            SkillSummary(
                name=s.name,
                description=s.description,
                category=s.category,
            )
            for s in self._skills.values()
        ]

    def get_full_instructions(self, skill_name: str) -> str | None:
        """Return full instructions for a skill, or None if not found.

        This is the on-demand counterpart to ``get_summaries()``.
        """
        skill = self._skills.get(skill_name)
        if skill is None:
            return None
        return skill.full_instructions

    def list_skills(self) -> list[str]:
        return list(self._skills.keys())

    def to_tool_definitions(self) -> list[dict]:
        """Export all skills as tool definitions (for LLM APIs).

        Every tool schema includes an optional ``_description`` parameter so the
        LLM can explain *why* it is invoking the tool.  The description is
        extracted before execution and never forwarded to the skill itself.
        """
        defs: list[dict] = []
        for s in self._skills.values():
            params = dict(s.parameters)
            # Inject _description into the properties if it looks like a JSON Schema object
            props = params.get("properties")
            if isinstance(props, dict):
                props = dict(props)
                props["_description"] = {
                    "type": "string",
                    "description": ("Optional short description of why this tool is being called."),
                }
                params = dict(params, properties=props)
            defs.append(
                {
                    "name": s.name,
                    "description": s.description,
                    "parameters": params,
                }
            )
        return defs


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
