"""Personalized Memory — per-user namespace facade over BaseStore.

Provides user-scoped key-value helpers on top of the existing BaseStore
abstraction. All writes pass through the optional MemoryFilter so that
ephemeral session artefacts are never persisted.

Namespace convention: ``("user", "<user_id>")``.

Usage::

    store = InMemoryStore()
    pm = PersonalizedMemory(store)

    await pm.put("alice", "profile", {"preferences": ["dark-mode"]})
    entry = await pm.get("alice", "profile")
    entries = await pm.list("alice")
    deleted = await pm.delete("alice", "profile")
    count = await pm.wipe("alice")
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .memory_filter import MemoryFilter
    from .store import BaseStore

logger = logging.getLogger(__name__)

# Namespace root component for per-user storage.
_USER_NS_ROOT = "user"


def _user_namespace(user_id: str) -> tuple[str, ...]:
    """Return the store namespace for a given user."""
    return (_USER_NS_ROOT, user_id)


def _filter_value(value: dict[str, Any], memory_filter: "MemoryFilter | None") -> dict[str, Any]:
    """Apply MemoryFilter to every string field in *value*, if filter is set."""
    if memory_filter is None:
        return value
    filtered: dict[str, Any] = {}
    for k, v in value.items():
        if isinstance(v, str):
            filtered[k] = memory_filter.filter_message(v)
        elif isinstance(v, list):
            filtered[k] = [
                memory_filter.filter_message(item) if isinstance(item, str) else item for item in v
            ]
        else:
            filtered[k] = v
    return filtered


class PersonalizedMemory:
    """User-scoped memory facade over :class:`BaseStore`.

    Wraps a ``BaseStore`` and scopes every operation under the
    ``("user", user_id)`` namespace.  An optional ``MemoryFilter`` sanitises
    string values (including list elements) before writes, keeping ephemeral
    session artefacts out of long-term memory.

    Args:
        store: Backing store (``InMemoryStore``, ``PostgresStore``, etc.).
        memory_filter: Optional filter applied on every ``put`` call.  When
            set, string fields in the value dict (and string items in list
            fields) are sanitised before persistence.
    """

    def __init__(
        self,
        store: "BaseStore",
        memory_filter: "MemoryFilter | None" = None,
    ) -> None:
        self._store = store
        self._memory_filter = memory_filter

    # ── Write ──────────────────────────────────────────────────────────

    async def put(
        self,
        user_id: str,
        key: str,
        value: dict[str, Any],
        ttl_seconds: int | None = None,
    ) -> None:
        """Persist *value* under ``("user", user_id, key)``.

        Applies the MemoryFilter before writing.  ``ttl_seconds`` is
        forwarded to the store as a float TTL.
        """
        ns = _user_namespace(user_id)
        safe_value = _filter_value(value, self._memory_filter)
        await self._store.aput(ns, key, safe_value, ttl=float(ttl_seconds) if ttl_seconds else None)
        logger.debug("PersonalizedMemory.put user=%s key=%s", user_id, key)

    # ── Read ───────────────────────────────────────────────────────────

    async def get(self, user_id: str, key: str) -> dict[str, Any] | None:
        """Return the value for *key* in the user's namespace, or ``None``."""
        ns = _user_namespace(user_id)
        item = await self._store.aget(ns, key)
        if item is None:
            return None
        return item.value

    async def list(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Return up to *limit* entries for the user, newest first.

        Each entry is a dict with ``key``, ``value``, ``created_at``, and
        ``updated_at`` fields so callers have full metadata.
        """
        ns = _user_namespace(user_id)
        items = await self._store.asearch(ns, limit=limit)
        return [
            {
                "key": item.key,
                "value": item.value,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in items
        ]

    # ── Delete ─────────────────────────────────────────────────────────

    async def delete(self, user_id: str, key: str) -> bool:
        """Remove *key* from the user's namespace.

        Returns ``True`` when the key existed, ``False`` when it was absent.
        """
        ns = _user_namespace(user_id)
        existing = await self._store.aget(ns, key)
        if existing is None:
            return False
        await self._store.adelete(ns, key)
        logger.debug("PersonalizedMemory.delete user=%s key=%s", user_id, key)
        return True

    async def wipe(self, user_id: str) -> int:
        """Delete **all** entries for *user_id* (GDPR-style erasure).

        Returns the number of entries removed.
        """
        ns = _user_namespace(user_id)
        # Fetch all entries — large limit to capture everything.
        items = await self._store.asearch(ns, limit=10_000)
        count = 0
        for item in items:
            await self._store.adelete(item.namespace, item.key)
            count += 1
        logger.info("PersonalizedMemory.wipe user=%s removed=%d", user_id, count)
        return count
