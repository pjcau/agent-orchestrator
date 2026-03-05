"""PostgreSQL checkpointer for distributed graph execution.

Uses asyncpg for async Postgres access. Designed to run in Docker/OrbStack.

Usage:
    cp = PostgresCheckpointer("postgresql://user:pass@localhost:5432/orchestrator")
    await cp.setup()  # Creates table if not exists
    compiled = graph.compile(checkpointer=cp)
"""

from __future__ import annotations

import json
from typing import Any

from .checkpoint import Checkpoint, Checkpointer


class PostgresCheckpointer(Checkpointer):
    """Async Postgres checkpointer using asyncpg."""

    def __init__(self, dsn: str, table_name: str = "checkpoints") -> None:
        self._dsn = dsn
        self._table = table_name
        self._pool = None

    async def _get_pool(self):
        if self._pool is None:
            try:
                import asyncpg
            except ImportError:
                raise ImportError(
                    "asyncpg is required for PostgresCheckpointer. "
                    "Install with: pip install asyncpg"
                )
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        return self._pool

    async def setup(self) -> None:
        """Create the checkpoints table if it doesn't exist."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._table} (
                    checkpoint_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    state JSONB NOT NULL,
                    next_nodes JSONB NOT NULL,
                    step_index INTEGER NOT NULL,
                    metadata JSONB DEFAULT '{{}}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{self._table}_thread
                ON {self._table}(thread_id, step_index)
            """)

    async def save(self, checkpoint: Checkpoint) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self._table}
                (checkpoint_id, thread_id, state, next_nodes, step_index, metadata)
                VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6::jsonb)
                ON CONFLICT (checkpoint_id)
                DO UPDATE SET state = $3::jsonb, next_nodes = $4::jsonb,
                             step_index = $5, metadata = $6::jsonb
                """,
                checkpoint.checkpoint_id,
                checkpoint.thread_id,
                json.dumps(checkpoint.state),
                json.dumps(checkpoint.next_nodes),
                checkpoint.step_index,
                json.dumps(checkpoint.metadata),
            )

    async def get(self, checkpoint_id: str) -> Checkpoint | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {self._table} WHERE checkpoint_id = $1",
                checkpoint_id,
            )
            return self._row_to_checkpoint(row) if row else None

    async def get_latest(self, thread_id: str) -> Checkpoint | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""SELECT * FROM {self._table}
                WHERE thread_id = $1 ORDER BY step_index DESC LIMIT 1""",
                thread_id,
            )
            return self._row_to_checkpoint(row) if row else None

    async def list_thread(self, thread_id: str) -> list[Checkpoint]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""SELECT * FROM {self._table}
                WHERE thread_id = $1 ORDER BY step_index""",
                thread_id,
            )
            return [self._row_to_checkpoint(r) for r in rows]

    async def delete_thread(self, thread_id: str) -> int:
        """Delete all checkpoints for a thread. Returns count deleted."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                f"DELETE FROM {self._table} WHERE thread_id = $1",
                thread_id,
            )
            return int(result.split()[-1])

    def _row_to_checkpoint(self, row: Any) -> Checkpoint:
        state = row["state"] if isinstance(row["state"], dict) else json.loads(row["state"])
        next_nodes = (
            row["next_nodes"]
            if isinstance(row["next_nodes"], list)
            else json.loads(row["next_nodes"])
        )
        metadata = (
            row["metadata"]
            if isinstance(row["metadata"], dict)
            else json.loads(row.get("metadata", "{}"))
        )
        return Checkpoint(
            checkpoint_id=row["checkpoint_id"],
            thread_id=row["thread_id"],
            state=state,
            next_nodes=next_nodes,
            step_index=row["step_index"],
            metadata=metadata,
        )

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
