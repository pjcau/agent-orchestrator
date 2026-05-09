"""Retrieval skill — agents call this to retrieve from the knowledge store.

DIP: this skill depends only on the ``Retriever`` abstraction, never on a
concrete embedder or vector store. The orchestrator injects the wired
``Retriever`` at construction time.
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.knowledge import Retriever
from ..core.knowledge.store import (
    SHARED_NAMESPACE,
    agent_namespace,
    user_namespace,
)
from ..core.skill import Skill, SkillResult

logger = logging.getLogger(__name__)


class RetrievalSkill(Skill):
    """Lets an agent search the knowledge store for relevant context.

    Namespaces follow the conventions in ``core/knowledge/store.py``:
    - ``shared``        → ``("shared",)``
    - ``agent:<name>``  → ``("agent", "<name>")``
    - ``user:<id>``     → ``("user", "<id>")``
    """

    def __init__(
        self,
        retriever: Retriever,
        emit_event: Any | None = None,
    ) -> None:
        self._retriever = retriever
        # Optional async callback ``await emit_event(event_type, data)`` so
        # the dashboard can highlight retrievals in real time. The skill
        # works fine without it (tests use it as None).
        self._emit_event = emit_event

    @property
    def name(self) -> str:
        return "knowledge_retrieve"

    @property
    def category(self) -> str:
        return "knowledge"

    @property
    def description(self) -> str:
        return (
            "Search the project's knowledge base for relevant snippets. "
            "Pass `query` and the `namespace` to search "
            "(e.g. 'shared', 'agent:backend', 'user:u-1'). "
            "Returns the top-k matching chunks as a Markdown context block."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query.",
                },
                "namespace": {
                    "type": "string",
                    "description": (
                        "Knowledge namespace. Use 'shared', 'agent:<name>' "
                        "or 'user:<id>'. Defaults to 'shared'."
                    ),
                    "default": "shared",
                },
                "k": {
                    "type": "integer",
                    "description": "Number of chunks to return (1..20). Default 5.",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    async def execute(self, params: dict) -> SkillResult:
        query = str(params.get("query", "")).strip()
        if not query:
            return SkillResult(success=False, output=None, error="query is required")

        ns_str = str(params.get("namespace", "shared")).strip() or "shared"
        try:
            namespace = parse_namespace(ns_str)
        except ValueError as exc:
            return SkillResult(success=False, output=None, error=str(exc))

        k_raw = params.get("k", 5)
        try:
            k = max(1, min(20, int(k_raw)))
        except (TypeError, ValueError):
            k = 5

        result = await self._retriever.retrieve(query, namespace, k=k)

        # Best-effort event emission so the dashboard can highlight
        # retrievals in the event log. Missing emit_event is fine.
        if self._emit_event is not None:
            try:
                await self._emit_event(
                    "knowledge.retrieved",
                    {
                        "namespace": list(namespace),
                        "query": query,
                        "k": k,
                        "hits": len(result.hits),
                        "embedding_model": result.embedding_model,
                    },
                )
            except Exception:  # pragma: no cover — defensive
                logger.warning("emit_event failed for knowledge.retrieved", exc_info=True)

        return SkillResult(
            success=True,
            output=result.as_context_block(),
            metadata={
                "namespace": list(namespace),
                "hits": len(result.hits),
                "embedding_model": result.embedding_model,
                "scores": [h.score for h in result.hits],
                "locations": [h.chunk.metadata.get("location", "") for h in result.hits],
            },
        )


def parse_namespace(s: str) -> tuple[str, ...]:
    """Parse the user-facing namespace string.

    Accepts:
    - ``"shared"``
    - ``"agent:<name>"``
    - ``"user:<id>"``

    Anything else is rejected. Centralised here so the API and the skill
    apply the exact same rules.
    """
    s = (s or "").strip()
    if s == "shared" or s == "":
        return SHARED_NAMESPACE
    if s.startswith("agent:") and len(s) > len("agent:"):
        return agent_namespace(s[len("agent:") :])
    if s.startswith("user:") and len(s) > len("user:"):
        return user_namespace(s[len("user:") :])
    raise ValueError(f"Unknown namespace '{s}'. Use 'shared', 'agent:<name>' or 'user:<id>'.")


def render_namespace(ns: tuple[str, ...]) -> str:
    """Inverse of ``parse_namespace`` for API responses."""
    if ns == SHARED_NAMESPACE:
        return "shared"
    if len(ns) == 2 and ns[0] in ("agent", "user"):
        return f"{ns[0]}:{ns[1]}"
    return ":".join(ns)
