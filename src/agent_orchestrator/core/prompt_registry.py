"""Prompt registry — tagged, metadata-indexed prompt template store.

Wraps ``BaseStore`` with a domain-specific API for registering, retrieving
and searching reusable prompt templates by name, tag set, and category.
Backed by whatever ``BaseStore`` implementation the caller passes in
(``InMemoryStore`` for dev, ``PostgresStore`` for production durability).

Inspired by PR #56 (f/prompts.chat). Operates on namespace ``("prompt",)``.

Usage::

    registry = PromptRegistry(store=InMemoryStore())
    await registry.register(PromptTemplate(
        name="code_review",
        content="Review this code: {code}",
        tags=["code", "review"],
        category="software",
    ))
    tpl = await registry.get("code_review")
    results = await registry.search(tags=["code"], category="software")
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .metrics import MetricsRegistry
from .store import BaseStore, Namespace

PROMPT_NAMESPACE: Namespace = ("prompt",)


@dataclass
class PromptTemplate:
    """A reusable prompt template with metadata for tag-based retrieval.

    Attributes:
        name: Unique identifier (used as the store key).
        content: The prompt text, possibly with ``{placeholders}``.
        tags: Free-form labels used for AND-intersection search.
        category: Coarse-grained category (e.g. ``software``, ``finance``).
        version: Semver-like version string; caller-defined semantics.
        description: Human-readable description of what the prompt does.
        metadata: Arbitrary extra metadata.
        created_at: Unix timestamp (seconds) of first registration.
        updated_at: Unix timestamp of most recent update.
    """

    name: str
    content: str
    tags: list[str] = field(default_factory=list)
    category: str | None = None
    version: str = "1"
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def format(self, **kwargs: Any) -> str:
        """Render the template with keyword arguments via ``str.format``."""
        return self.content.format(**kwargs)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PromptTemplate:
        return cls(
            name=data["name"],
            content=data["content"],
            tags=list(data.get("tags", [])),
            category=data.get("category"),
            version=data.get("version", "1"),
            description=data.get("description"),
            metadata=dict(data.get("metadata", {})),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
        )


class PromptRegistry:
    """Tag-aware prompt template store backed by a ``BaseStore``.

    Lookups record metrics (``prompt_registry_lookups_total``,
    ``prompt_registry_hits_total`` / ``prompt_registry_misses_total``,
    ``prompt_registry_lookup_duration_seconds``) when a ``MetricsRegistry``
    is provided so they can be visualised in the dashboard and exported via
    Prometheus.

    Thread-safety: delegates to the underlying store. Writes are last-write-
    wins by ``name``; we do not attempt optimistic concurrency.
    """

    def __init__(
        self,
        store: BaseStore,
        *,
        metrics: MetricsRegistry | None = None,
        namespace: Namespace = PROMPT_NAMESPACE,
    ) -> None:
        self._store = store
        self._metrics = metrics
        self._namespace = namespace

    async def register(self, template: PromptTemplate) -> None:
        """Insert or update a template by name (last write wins)."""
        template.updated_at = time.time()
        await self._store.aput(self._namespace, template.name, template.to_dict())

    async def get(self, name: str) -> PromptTemplate | None:
        """Return the template with this name, or None."""
        start = time.monotonic()
        item = await self._store.aget(self._namespace, name)
        self._record_lookup(start, hit=item is not None)
        if item is None:
            return None
        return PromptTemplate.from_dict(item.value)

    async def delete(self, name: str) -> None:
        """Remove the named template (no-op if absent)."""
        await self._store.adelete(self._namespace, name)

    async def list_all(self, limit: int = 100, offset: int = 0) -> list[PromptTemplate]:
        """List every registered template (up to ``limit``)."""
        results = await self._store.asearch(self._namespace, limit=limit, offset=offset)
        return [PromptTemplate.from_dict(r.value) for r in results]

    async def search(
        self,
        *,
        tags: list[str] | None = None,
        category: str | None = None,
        limit: int = 10,
    ) -> list[PromptTemplate]:
        """Search by AND-intersection of tags and an optional category.

        ``tags`` is AND-semantics: a template matches only if it contains
        every requested tag. ``category`` is exact-match. Results are
        ordered by ``updated_at`` descending.
        """
        start = time.monotonic()
        filter_spec: dict[str, Any] | None = None
        if category is not None:
            filter_spec = {"category": {"$eq": category}}

        # Over-fetch when tag filtering will happen client-side; 10x the
        # request limit gives us headroom without unbounded scans.
        fetch_limit = limit * 10 if tags else limit
        raw = await self._store.asearch(
            self._namespace,
            filter=filter_spec,
            limit=max(fetch_limit, limit),
        )
        templates = [PromptTemplate.from_dict(r.value) for r in raw]

        if tags:
            tag_set = set(tags)
            templates = [t for t in templates if tag_set.issubset(set(t.tags))]

        templates.sort(key=lambda t: t.updated_at, reverse=True)
        templates = templates[:limit]
        self._record_lookup(start, hit=bool(templates))
        return templates

    # ─── Internal ─────────────────────────────────────────────────────

    def _record_lookup(self, start_monotonic: float, *, hit: bool) -> None:
        if self._metrics is None:
            return
        duration = time.monotonic() - start_monotonic
        self._metrics.counter(
            "prompt_registry_lookups_total",
            "Total prompt registry lookups (hit + miss)",
        ).inc()
        if hit:
            self._metrics.counter(
                "prompt_registry_hits_total",
                "Total prompt registry lookups that returned at least one result",
            ).inc()
        else:
            self._metrics.counter(
                "prompt_registry_misses_total",
                "Total prompt registry lookups that returned zero results",
            ).inc()
        self._metrics.histogram(
            "prompt_registry_lookup_duration_seconds",
            "Latency of prompt registry lookups in seconds",
        ).observe(duration)
