"""Cacheable-prefix propagation via contextvars.

The CLI sends a ``cache_context`` body field on /api/prompt,
/api/cli/v1/run, /api/agent/run and /api/team/run when its expanded
``@file`` / ``@dir`` references should be marked as cacheable by the
underlying LLM provider. Threading that string through every layer
(endpoint → run_graph / run_team → run_agent → agent loop → provider)
would be invasive, so we instead use a :class:`contextvars.ContextVar`
that:

  * is set at the FastAPI endpoint boundary and cleared in a ``finally``
    block;
  * is per-task — concurrent requests on the same worker do not leak
    into each other because asyncio creates a fresh copy for each task;
  * is read lazily by providers that know how to use it (currently
    :class:`OpenRouterProvider` — Anthropic native support lands later).

A provider that does not know about caching simply never calls
``current_cache_context()`` and behaves exactly as before.
"""

from __future__ import annotations

from contextvars import ContextVar

# Default ``None`` means "no cacheable prefix on this request".
_cache_context: ContextVar[str | None] = ContextVar("ago_cache_context", default=None)


def current_cache_context() -> str | None:
    """Return the active cacheable prefix for the current task, if any."""
    return _cache_context.get()


def set_cache_context(value: str | None):
    """Set the cacheable prefix for the current task.

    Returns the token returned by ``ContextVar.set`` so callers can pass it
    to :func:`reset_cache_context` in a ``finally`` block. This pattern
    ensures the value is scoped to the current request even when the same
    asyncio worker handles other requests immediately after.
    """
    return _cache_context.set(value or None)


def reset_cache_context(token) -> None:
    """Reset the cacheable prefix to its previous value (None by default)."""
    _cache_context.reset(token)
