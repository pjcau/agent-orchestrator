"""PostgreSQL-backed persistent store for cross-thread memory.

Same interface as InMemoryStore but with durable storage.

Table schema:
    store_items(
        namespace  TEXT,           -- dot-joined namespace tuple, e.g. "agent.backend"
        key        TEXT,
        value      JSONB,
        expires_at TIMESTAMPTZ,    -- NULL means no expiry
        created_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ,
        PRIMARY KEY (namespace, key)
    )
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from .store import BaseStore, Item, SearchItem, _match_filter

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import]

# Namespace tuple is stored as a dot-separated string in the DB.
_SEP = "."


def _ns_to_str(namespace: tuple[str, ...]) -> str:
    """Encode a namespace tuple to a dot-separated string.

    Example: ("agent", "backend") -> "agent.backend"
    """
    return _SEP.join(namespace)


def _str_to_ns(ns_str: str) -> tuple[str, ...]:
    """Decode a dot-separated string to a namespace tuple.

    Example: "agent.backend" -> ("agent", "backend")
    """
    if not ns_str:
        return ()
    return tuple(ns_str.split(_SEP))


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS store_items (
    namespace  TEXT        NOT NULL,
    key        TEXT        NOT NULL,
    value      JSONB       NOT NULL DEFAULT '{}',
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (namespace, key)
);
"""


class PostgresStore(BaseStore):
    """PostgreSQL-backed persistent store for cross-thread memory.

    Implements the same :class:`BaseStore` interface as :class:`InMemoryStore`
    but persists all data to a PostgreSQL ``store_items`` table with durable
    JSONB values and optional TTL via ``expires_at``.

    Args:
        pool: An ``asyncpg.Pool`` connection pool.  The caller is responsible
              for creating and closing the pool.
        memory_filter: Optional :class:`MemoryFilter` to sanitize string values
                       before persistence.  When set, every string field in the
                       value dict is filtered on :meth:`aput`.
    """

    def __init__(
        self,
        pool: "asyncpg.Pool",
        memory_filter: "MemoryFilter | None" = None,  # noqa: F821
    ) -> None:
        from .memory_filter import MemoryFilter as _MF

        self._pool = pool
        self._memory_filter: "_MF | None" = memory_filter

    # ------------------------------------------------------------------
    # Table bootstrap
    # ------------------------------------------------------------------

    async def ensure_table(self) -> None:
        """Create the store_items table if it does not already exist."""
        async with self._pool.acquire() as conn:
            await conn.execute(_CREATE_TABLE_SQL)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _filter_value(self, value: dict[str, Any]) -> dict[str, Any]:
        """Apply MemoryFilter to string fields in the value dict."""
        if self._memory_filter is None:
            return value
        filtered: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(v, str):
                filtered[k] = self._memory_filter.filter_message(v)
            else:
                filtered[k] = v
        return filtered

    @staticmethod
    def _unix_from_pg(ts: Any) -> float:
        """Convert a PostgreSQL datetime object to a Unix timestamp float."""
        if ts is None:
            return time.time()
        try:
            return ts.timestamp()
        except Exception:
            return time.time()

    # ------------------------------------------------------------------
    # BaseStore interface
    # ------------------------------------------------------------------

    async def aget(self, namespace: tuple[str, ...], key: str) -> Item | None:
        """Fetch a single item by namespace and key.

        Returns ``None`` if the item does not exist or has expired.
        Expired items are deleted lazily on read.
        """
        ns_str = _ns_to_str(namespace)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value, expires_at, created_at, updated_at "
                "FROM store_items WHERE namespace = $1 AND key = $2",
                ns_str,
                key,
            )

        if row is None:
            return None

        # Lazy TTL expiry
        if row["expires_at"] is not None:
            import datetime

            now = datetime.datetime.now(datetime.timezone.utc)
            if row["expires_at"] < now:
                # Delete the expired row asynchronously
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        "DELETE FROM store_items WHERE namespace = $1 AND key = $2",
                        ns_str,
                        key,
                    )
                return None

        value = json.loads(row["value"]) if isinstance(row["value"], str) else dict(row["value"])
        return Item(
            namespace=namespace,
            key=key,
            value=value,
            created_at=self._unix_from_pg(row["created_at"]),
            updated_at=self._unix_from_pg(row["updated_at"]),
        )

    async def aput(
        self,
        namespace: tuple[str, ...],
        key: str,
        value: dict[str, Any],
        *,
        ttl: float | None = None,
    ) -> None:
        """Upsert an item.  If ``ttl`` is given (seconds), the row expires after
        that many seconds from *now*.  Passing ``ttl=None`` clears any existing
        expiry on update.
        """
        ns_str = _ns_to_str(namespace)
        filtered_value = self._filter_value(value)
        value_json = json.dumps(filtered_value)

        expires_at_sql: str | None = None
        if ttl is not None:
            # Build an interval expression evaluated server-side
            expires_at_sql = f"NOW() + INTERVAL '{ttl} seconds'"

        async with self._pool.acquire() as conn:
            # Fetch existing created_at to preserve it on update
            existing = await conn.fetchrow(
                "SELECT created_at FROM store_items WHERE namespace = $1 AND key = $2",
                ns_str,
                key,
            )

            if existing is None:
                # INSERT new row
                if expires_at_sql:
                    await conn.execute(
                        f"INSERT INTO store_items (namespace, key, value, expires_at) "
                        f"VALUES ($1, $2, $3::jsonb, {expires_at_sql})",
                        ns_str,
                        key,
                        value_json,
                    )
                else:
                    await conn.execute(
                        "INSERT INTO store_items (namespace, key, value) "
                        "VALUES ($1, $2, $3::jsonb)",
                        ns_str,
                        key,
                        value_json,
                    )
            else:
                # UPDATE: preserve original created_at
                if expires_at_sql:
                    await conn.execute(
                        f"UPDATE store_items SET value = $3::jsonb, "
                        f"expires_at = {expires_at_sql}, updated_at = NOW() "
                        f"WHERE namespace = $1 AND key = $2",
                        ns_str,
                        key,
                        value_json,
                    )
                else:
                    await conn.execute(
                        "UPDATE store_items SET value = $3::jsonb, "
                        "expires_at = NULL, updated_at = NOW() "
                        "WHERE namespace = $1 AND key = $2",
                        ns_str,
                        key,
                        value_json,
                    )

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        """Delete an item.  No-op if the item does not exist."""
        ns_str = _ns_to_str(namespace)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM store_items WHERE namespace = $1 AND key = $2",
                ns_str,
                key,
            )

    async def asearch(
        self,
        namespace_prefix: tuple[str, ...],
        *,
        filter: dict[str, Any] | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[SearchItem]:
        """Search items whose namespace starts with *namespace_prefix*.

        Active TTL filtering happens server-side (rows with ``expires_at <
        NOW()`` are skipped).  Additional JSON-field filters are applied in
        Python using :func:`_match_filter` (same semantics as InMemoryStore).

        Results are sorted by ``updated_at`` descending.
        """
        ns_prefix_str = _ns_to_str(namespace_prefix)
        # Match the exact namespace or any deeper namespace that starts with
        # the prefix followed by a dot separator.
        like_pattern = ns_prefix_str + ".%"

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT namespace, key, value, created_at, updated_at "
                "FROM store_items "
                "WHERE (namespace = $1 OR namespace LIKE $2) "
                "  AND (expires_at IS NULL OR expires_at > NOW()) "
                "ORDER BY updated_at DESC",
                ns_prefix_str,
                like_pattern,
            )

        results: list[SearchItem] = []
        for row in rows:
            value = (
                json.loads(row["value"]) if isinstance(row["value"], str) else dict(row["value"])
            )

            if filter and not _match_filter(value, filter):
                continue

            ns = _str_to_ns(row["namespace"])
            results.append(
                SearchItem(
                    namespace=ns,
                    key=row["key"],
                    value=value,
                    created_at=self._unix_from_pg(row["created_at"]),
                    updated_at=self._unix_from_pg(row["updated_at"]),
                    score=None,
                )
            )

        return results[offset : offset + limit]

    async def alist_namespaces(
        self,
        *,
        prefix: tuple[str, ...] | None = None,
        max_depth: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[tuple[str, ...]]:
        """List distinct namespaces.

        When *prefix* is given, only namespaces that start with it are
        returned.  When *max_depth* is given, namespaces deeper than
        ``len(prefix) + max_depth`` components are truncated.
        """
        async with self._pool.acquire() as conn:
            if prefix:
                prefix_str = _ns_to_str(prefix)
                like_pattern = prefix_str + ".%"
                rows = await conn.fetch(
                    "SELECT DISTINCT namespace FROM store_items "
                    "WHERE (namespace = $1 OR namespace LIKE $2) "
                    "  AND (expires_at IS NULL OR expires_at > NOW()) "
                    "ORDER BY namespace",
                    prefix_str,
                    like_pattern,
                )
            else:
                rows = await conn.fetch(
                    "SELECT DISTINCT namespace FROM store_items "
                    "WHERE expires_at IS NULL OR expires_at > NOW() "
                    "ORDER BY namespace"
                )

        namespaces: set[tuple[str, ...]] = set()
        prefix_len = len(prefix) if prefix else 0

        for row in rows:
            ns = _str_to_ns(row["namespace"])
            if max_depth is not None:
                # Truncate to prefix_len + max_depth components
                ns = ns[: prefix_len + max_depth]
            namespaces.add(ns)

        sorted_ns = sorted(namespaces)
        return sorted_ns[offset : offset + limit]
