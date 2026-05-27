"""PostgreSQL-backed :class:`DeviceFlowStore`.

Selected by :func:`dashboard.app.create_dashboard_app` when ``DATABASE_URL``
is set. Lets the two anonymous CLI endpoints (``device-start`` and
``device-poll``) run on different workers without sticky-session affinity.

Schema is created lazily by :meth:`PostgresDeviceFlowStore.setup`. Old rows
are reaped by :meth:`cleanup`, which is also called opportunistically from
``device-poll`` to keep the table bounded without a separate worker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from .cli_device_flow import (
    DEFAULT_EXPIRES_IN,
    DEFAULT_INTERVAL,
    STATUS_APPROVED,
    STATUS_DENIED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    DeviceFlow,
    DeviceFlowStore,
    _gen_device_code,
    _gen_user_code,
)

logger = logging.getLogger(__name__)


class PostgresDeviceFlowStore(DeviceFlowStore):
    """Async Postgres-backed store for CLI device-flow OAuth.

    All operations are short and idempotent; the table is a key-value record
    keyed by ``device_code``. The ``user_code`` column is uniqued so the
    browser-facing approval lookup is O(log n).
    """

    def __init__(self, dsn: str, table_name: str = "cli_device_flows") -> None:
        self._dsn = dsn
        self._table = table_name
        self._pool: Any | None = None
        self._setup_done = False
        self._setup_lock = asyncio.Lock()

    async def _get_pool(self) -> Any:
        if self._pool is None:
            try:
                import asyncpg
            except ImportError as exc:  # pragma: no cover — declared in pyproject
                raise ImportError(
                    "asyncpg is required for PostgresDeviceFlowStore. "
                    "Install with: pip install asyncpg"
                ) from exc
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        return self._pool

    async def setup(self) -> None:
        """Create the table + indices if they do not already exist.

        Idempotent and concurrency-safe — multiple workers can call this on
        startup. The first one wins; the rest see ``CREATE ... IF NOT EXISTS``
        and no-op.
        """
        async with self._setup_lock:
            if self._setup_done:
                return
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute(f"""
                    CREATE TABLE IF NOT EXISTS {self._table} (
                        device_code TEXT PRIMARY KEY,
                        user_code TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL,
                        created_at DOUBLE PRECISION NOT NULL,
                        expires_at DOUBLE PRECISION NOT NULL,
                        interval_s INTEGER NOT NULL,
                        user_info JSONB,
                        last_poll_at DOUBLE PRECISION NOT NULL DEFAULT 0
                    )
                """)
                await conn.execute(f"""
                    CREATE INDEX IF NOT EXISTS idx_{self._table}_expires_at
                    ON {self._table}(expires_at)
                """)
            self._setup_done = True

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _row_to_flow(row: Any) -> DeviceFlow:
        user_info_raw = row["user_info"]
        if isinstance(user_info_raw, str):
            user_info: dict[str, Any] | None = json.loads(user_info_raw)
        else:
            user_info = dict(user_info_raw) if user_info_raw else None
        return DeviceFlow(
            device_code=row["device_code"],
            user_code=row["user_code"],
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
            interval=int(row["interval_s"]),
            status=row["status"],
            user_info=user_info,
            last_poll_at=float(row["last_poll_at"] or 0.0),
        )

    # ------------------------------------------------------------------ API

    async def create(
        self,
        *,
        expires_in: int = DEFAULT_EXPIRES_IN,
        interval: int = DEFAULT_INTERVAL,
    ) -> DeviceFlow:
        await self.setup()
        now = time.time()
        # Retry on user_code collision (unique constraint).
        last_err: Exception | None = None
        for _ in range(10):
            flow = DeviceFlow(
                device_code=_gen_device_code(),
                user_code=_gen_user_code(),
                created_at=now,
                expires_at=now + expires_in,
                interval=interval,
            )
            pool = await self._get_pool()
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        f"""
                        INSERT INTO {self._table}
                        (device_code, user_code, status, created_at, expires_at, interval_s)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        """,
                        flow.device_code,
                        flow.user_code,
                        flow.status,
                        flow.created_at,
                        flow.expires_at,
                        flow.interval,
                    )
                return flow
            except Exception as exc:  # pragma: no cover — race
                last_err = exc
                continue
        raise RuntimeError(f"could not insert device-flow row: {last_err}")

    async def lookup_by_user_code(self, user_code: str) -> DeviceFlow | None:
        await self.setup()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {self._table} WHERE user_code = $1",
                user_code.upper(),
            )
        return self._row_to_flow(row) if row else None

    async def lookup_by_device_code(self, device_code: str) -> DeviceFlow | None:
        await self.setup()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {self._table} WHERE device_code = $1",
                device_code,
            )
        return self._row_to_flow(row) if row else None

    async def approve(self, user_code: str, user_info: dict[str, Any]) -> DeviceFlow:
        await self.setup()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {self._table} WHERE user_code = $1",
                user_code.upper(),
            )
            if row is None:
                raise KeyError("user_code not found")
            now = time.time()
            if now >= float(row["expires_at"]) and row["status"] != STATUS_APPROVED:
                await conn.execute(
                    f"UPDATE {self._table} SET status = $1 WHERE device_code = $2",
                    STATUS_EXPIRED,
                    row["device_code"],
                )
                raise KeyError("user_code expired")
            if row["status"] != STATUS_PENDING:
                raise KeyError(f"user_code already {row['status']}")
            await conn.execute(
                f"""
                UPDATE {self._table}
                SET status = $1, user_info = $2::jsonb
                WHERE device_code = $3
                """,
                STATUS_APPROVED,
                json.dumps(user_info),
                row["device_code"],
            )
            row = await conn.fetchrow(
                f"SELECT * FROM {self._table} WHERE device_code = $1",
                row["device_code"],
            )
        return self._row_to_flow(row)

    async def deny(self, user_code: str) -> DeviceFlow:
        await self.setup()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE {self._table}
                SET status = $1
                WHERE user_code = $2 AND status = $3
                RETURNING *
                """,
                STATUS_DENIED,
                user_code.upper(),
                STATUS_PENDING,
            )
            if row is None:
                # Either not present or already non-pending — return current row.
                row = await conn.fetchrow(
                    f"SELECT * FROM {self._table} WHERE user_code = $1",
                    user_code.upper(),
                )
                if row is None:
                    raise KeyError("user_code not found")
        return self._row_to_flow(row)

    async def consume(self, device_code: str) -> DeviceFlow | None:
        await self.setup()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            # Use DELETE ... RETURNING for atomic read+remove when approved;
            # if not approved we just SELECT so the row stays as-is.
            row = await conn.fetchrow(
                f"""
                DELETE FROM {self._table}
                WHERE device_code = $1 AND status = $2
                RETURNING *
                """,
                device_code,
                STATUS_APPROVED,
            )
            if row is not None:
                return self._row_to_flow(row)
            row = await conn.fetchrow(
                f"SELECT * FROM {self._table} WHERE device_code = $1",
                device_code,
            )
        return self._row_to_flow(row) if row else None

    async def record_poll(self, device_code: str, now: float) -> None:
        await self.setup()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE {self._table} SET last_poll_at = $1 WHERE device_code = $2",
                now,
                device_code,
            )

    async def cleanup(self) -> int:
        await self.setup()
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM {self._table} WHERE expires_at < $1",
                time.time(),
            )
        # asyncpg returns "DELETE n"; surface n for callers that care.
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0
