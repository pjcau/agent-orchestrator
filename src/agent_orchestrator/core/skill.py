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
    metadata: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        if self.success:
            return str(self.output)
        return f"Error: {self.error}"


@dataclass
class SkillManifest:
    """Standardized, serializable skill descriptor (scout finding #71).

    A ``skill.json``-style manifest for discovery, marketplace listings, and
    cross-process capability exchange. Metadata only — manifests never carry
    executable code; importing one registers *knowledge* of a skill, not an
    implementation.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    category: str = "general"
    version: str = "1.0"
    instructions: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "category": self.category,
            "version": self.version,
            "instructions": self.instructions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillManifest":
        """Validate and build a manifest from untrusted JSON data."""
        if not isinstance(data, dict):
            raise ValueError("Manifest must be a JSON object")
        for field_name in ("name", "description", "parameters"):
            if field_name not in data:
                raise ValueError(f"Manifest missing required field '{field_name}'")
        name = data["name"]
        if not isinstance(name, str) or not name or len(name) > 128:
            raise ValueError("Manifest 'name' must be a non-empty string (max 128 chars)")
        if not isinstance(data["description"], str):
            raise ValueError("Manifest 'description' must be a string")
        if not isinstance(data["parameters"], dict):
            raise ValueError("Manifest 'parameters' must be a JSON-Schema object")
        return cls(
            name=name,
            description=data["description"],
            parameters=data["parameters"],
            category=data.get("category", "general"),
            version=str(data.get("version", "1.0")),
            instructions=data.get("instructions"),
        )


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

    # --- Lifecycle hooks (scout finding #61) ---------------------------
    # Optional no-op hooks for stateful skills (browser sessions, DB
    # connections, warm caches). The registry drives them via
    # ``SkillRegistry.startup()`` / ``shutdown()``.

    async def setup(self) -> None:
        """Acquire long-lived resources before first use. Default: no-op."""

    async def teardown(self) -> None:
        """Release long-lived resources on shutdown. Default: no-op."""

    def to_manifest(self) -> SkillManifest:
        """Export this skill's metadata as a :class:`SkillManifest`."""
        return SkillManifest(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            category=self.category,
            instructions=self.full_instructions,
        )


class SkillRegistry:
    """Central registry of all available skills with middleware support."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        self._middlewares: list[SkillMiddleware] = []
        self._started = False

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    async def startup(self) -> None:
        """Run ``setup()`` on every registered skill (idempotent).

        A skill whose setup raises is unregistered and reported, so one
        broken integration cannot take the whole registry down.
        """
        if self._started:
            return
        self._started = True
        for name, skill in list(self._skills.items()):
            try:
                await skill.setup()
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Skill %s setup failed — unregistering: %s", name, exc
                )
                self._skills.pop(name, None)

    async def shutdown(self) -> None:
        """Run ``teardown()`` on every registered skill, best-effort."""
        if not self._started:
            return
        self._started = False
        for name, skill in self._skills.items():
            try:
                await skill.teardown()
            except Exception as exc:
                logging.getLogger(__name__).warning("Skill %s teardown failed: %s", name, exc)

    def export_manifests(self) -> list[dict]:
        """Export every registered skill as a manifest dict (``skill.json`` style)."""
        return [skill.to_manifest().to_dict() for skill in self._skills.values()]

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

    def register_mcp_tools(self, client_manager: "Any") -> int:
        """Register external MCP tools as skills.

        Creates a wrapper skill for each tool returned by
        ``client_manager.get_all_tools()``.  Each wrapper delegates execution
        to ``client_manager.call_tool(server_name, tool_name, arguments)``.

        Returns the number of skills registered.
        """
        from .mcp_client import MCPClientManager

        if not isinstance(client_manager, MCPClientManager):
            raise TypeError("client_manager must be an MCPClientManager instance")

        tools = client_manager.get_all_tools()
        registered = 0
        for tool in tools:
            # tool.name is already prefixed: "{server}/{tool_name}"
            parts = tool.name.split("/", 1)
            server_name = parts[0]
            remote_tool_name = parts[1] if len(parts) > 1 else tool.name

            skill = _MCPToolSkill(
                tool=tool,
                server_name=server_name,
                remote_tool_name=remote_tool_name,
                client_manager=client_manager,
            )
            self.register(skill)
            registered += 1

        return registered

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


def circuit_breaker_middleware(
    failure_threshold: int = 5,
    cooldown_seconds: float = 30.0,
    skills: set[str] | None = None,
) -> SkillMiddleware:
    """Per-skill circuit breaker — stop hammering a skill that keeps failing.

    Classic three-state breaker, tracked independently per skill name:

    - **closed** — calls pass through; consecutive failures are counted.
    - **open** — after ``failure_threshold`` consecutive failures the breaker
      opens and calls fail fast (no execution) until ``cooldown_seconds``
      elapse. This is what retry_middleware alone cannot do: without it a
      skill backed by a dead external API is retried in full on every task.
    - **half-open** — after the cooldown, exactly one probe call is let
      through; success closes the breaker, failure reopens it for another
      cooldown.

    ``skills`` limits the breaker to the named skills (e.g. only external
    integrations); None guards every skill. Place it *before*
    retry_middleware in the chain so an open breaker also skips the retries.
    """
    if failure_threshold < 1:
        raise ValueError("failure_threshold must be >= 1")

    # skill_name -> [consecutive_failures, opened_at_monotonic | None, probing]
    state: dict[str, list] = {}

    async def middleware(
        request: SkillRequest,
        next_fn: Callable[[SkillRequest], Awaitable[SkillResult]],
    ) -> SkillResult:
        if skills is not None and request.skill_name not in skills:
            return await next_fn(request)

        st = state.setdefault(request.skill_name, [0, None, False])

        if st[1] is not None:  # open
            elapsed = time.monotonic() - st[1]
            if elapsed < cooldown_seconds:
                return SkillResult(
                    success=False,
                    output=None,
                    error=(
                        f"Circuit breaker open for skill '{request.skill_name}' "
                        f"({st[0]} consecutive failures); retrying in "
                        f"{cooldown_seconds - elapsed:.1f}s"
                    ),
                    metadata={"circuit_open": True},
                )
            if st[2]:  # a probe is already in flight — fail fast, don't stampede
                return SkillResult(
                    success=False,
                    output=None,
                    error=(
                        f"Circuit breaker half-open for skill '{request.skill_name}': "
                        "probe in flight"
                    ),
                    metadata={"circuit_open": True},
                )
            st[2] = True  # half-open: this call is the probe

        try:
            result = await next_fn(request)
        except Exception:
            st[0] += 1
            st[2] = False
            if st[0] >= failure_threshold:
                st[1] = time.monotonic()
            raise

        if result.success:
            state[request.skill_name] = [0, None, False]
        else:
            st[0] += 1
            st[2] = False
            if st[0] >= failure_threshold:
                st[1] = time.monotonic()
        return result

    return middleware


def verification_middleware(
    validators: dict[str, Callable[[SkillResult], bool | tuple[bool, str]]],
    metrics: Any = None,
) -> SkillMiddleware:
    """Quality gate middleware — reject skill results that fail validators (PR #59).

    For each skill name mapped to a validator, after the skill runs the
    result is passed to the validator. Validators return either:
    - ``bool`` — ``True`` accepts, ``False`` rejects with a generic reason.
    - ``(bool, str)`` — explicit pass/fail with a reason string.

    A rejected result is converted into an error ``SkillResult`` so callers
    see a clear failure. Metrics (``verification_total``,
    ``verification_pass_total``, ``verification_fail_total``,
    ``verification_duration_seconds``) are recorded when a
    ``MetricsRegistry`` is supplied.
    """

    async def middleware(
        request: SkillRequest,
        next_fn: Callable[[SkillRequest], Awaitable[SkillResult]],
    ) -> SkillResult:
        result = await next_fn(request)
        validator = validators.get(request.skill_name)
        if validator is None:
            return result

        start = time.monotonic()
        verdict = validator(result)
        duration = time.monotonic() - start

        if isinstance(verdict, tuple):
            passed, reason = verdict
        else:
            passed = bool(verdict)
            reason = "verification gate failed"

        if metrics is not None:
            metrics.counter(
                "verification_total",
                "Total skill results passed through verification",
                labels={"skill": request.skill_name},
            ).inc()
            if passed:
                metrics.counter(
                    "verification_pass_total",
                    "Skill results that passed the verification gate",
                    labels={"skill": request.skill_name},
                ).inc()
            else:
                metrics.counter(
                    "verification_fail_total",
                    "Skill results that failed the verification gate",
                    labels={"skill": request.skill_name},
                ).inc()
            metrics.histogram(
                "verification_duration_seconds",
                "Latency of verification validators in seconds",
                labels={"skill": request.skill_name},
            ).observe(duration)

        if passed:
            return result
        # Reject — convert into a failed SkillResult so downstream sees it.
        return SkillResult(
            success=False,
            output=result.output,
            error=f"verification: {reason}",
        )

    return middleware


def context_loader_middleware(
    context_dir: Any,
    target_skills: set[str] | None = None,
    metadata_key: str = "context",
    max_bytes: int = 50_000,
    metrics: Any = None,
) -> SkillMiddleware:
    """Inject reference context files into ``request.metadata`` (PR #61).

    Reads every ``.md`` file under ``context_dir`` once at middleware
    creation time (cached in-memory) and injects the concatenated content
    under ``request.metadata[metadata_key]`` so skills that know to look
    for it can prepend the reference material to their prompts. Skills
    that don't reference this key are unaffected.

    Safer than modifying ``params`` directly, since unknown params break
    LLM tool schemas. Content is capped at ``max_bytes`` to avoid blowing
    up downstream prompts.

    Metrics: ``context_files_loaded_total`` and ``context_bytes_injected``
    are recorded if a ``MetricsRegistry`` is provided.
    """
    from pathlib import Path as _Path

    context_path = _Path(str(context_dir))
    cached_content: str = ""
    cached_file_count: int = 0
    if context_path.is_dir():
        parts: list[str] = []
        total_bytes = 0
        for md in sorted(context_path.glob("*.md")):
            try:
                text = md.read_text(encoding="utf-8")
            except OSError:
                continue
            block = f"# {md.name}\n{text}"
            if total_bytes + len(block) > max_bytes:
                break
            parts.append(block)
            total_bytes += len(block)
            cached_file_count += 1
        cached_content = "\n\n".join(parts)

    async def middleware(
        request: SkillRequest,
        next_fn: Callable[[SkillRequest], Awaitable[SkillResult]],
    ) -> SkillResult:
        if not cached_content:
            return await next_fn(request)
        if target_skills is not None and request.skill_name not in target_skills:
            return await next_fn(request)

        new_metadata = dict(request.metadata)
        new_metadata[metadata_key] = cached_content
        injected = request.override(metadata=new_metadata)

        if metrics is not None:
            metrics.counter(
                "context_files_loaded_total",
                "Total skill invocations that received context injection",
                labels={"skill": request.skill_name},
            ).inc(cached_file_count)
            metrics.counter(
                "context_bytes_injected",
                "Total bytes of reference context injected into skill metadata",
                labels={"skill": request.skill_name},
            ).inc(len(cached_content))

        return await next_fn(injected)

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


# ---------------------------------------------------------------------------
# MCP tool skill wrapper
# ---------------------------------------------------------------------------


class _MCPToolSkill(Skill):
    """Internal wrapper that exposes an external MCP tool as a local Skill.

    Instantiated by ``SkillRegistry.register_mcp_tools``; not part of the
    public API.  The skill name uses the full ``{server}/{tool}`` prefix so
    that tools from different servers never collide.
    """

    def __init__(
        self,
        tool: Any,  # MCPTool
        server_name: str,
        remote_tool_name: str,
        client_manager: Any,  # MCPClientManager
    ) -> None:
        self._tool = tool
        self._server_name = server_name
        self._remote_tool_name = remote_tool_name
        self._client_manager = client_manager

    @property
    def name(self) -> str:
        return self._tool.name

    @property
    def description(self) -> str:
        return self._tool.description

    @property
    def parameters(self) -> dict:
        return self._tool.input_schema

    @property
    def category(self) -> str:
        return "mcp"

    async def execute(self, params: dict) -> SkillResult:
        try:
            output = await self._client_manager.call_tool(
                self._server_name, self._remote_tool_name, params
            )
            return SkillResult(success=True, output=output)
        except Exception as exc:
            return SkillResult(success=False, output=None, error=str(exc))
