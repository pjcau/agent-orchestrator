"""Checkpoint — state persistence for graph execution.

Supports:
- InMemoryCheckpointer: fast, for dev/testing
- SQLiteCheckpointer: durable, for local production
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Checkpoint:
    """One snapshot of graph state.

    ``raw_log`` (PR #81) optionally stores the verbatim sequence of
    messages that led to this state. When set, callers can re-render a
    faithful transcript even after summarisation has compacted the
    conversation. It's opt-in — default ``None`` to avoid storage bloat.
    """

    checkpoint_id: str
    thread_id: str
    state: dict[str, Any]
    next_nodes: list[str]
    step_index: int
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_log: str | None = None


class Checkpointer(ABC):
    """Abstract checkpointer. Save and restore graph state.

    Besides the four storage primitives, every backend inherits
    :meth:`get_at_step` and :meth:`fork` (scout findings #31/#40/#43):
    ``fork`` copies a thread's history up to a chosen step into a fresh
    thread, so alternative branches can be replayed (via the graph's
    ``resume_from=``) without overwriting the original thread's history.
    """

    @abstractmethod
    async def save(self, checkpoint: Checkpoint) -> None: ...

    @abstractmethod
    async def get(self, checkpoint_id: str) -> Checkpoint | None: ...

    @abstractmethod
    async def get_latest(self, thread_id: str) -> Checkpoint | None: ...

    @abstractmethod
    async def list_thread(self, thread_id: str) -> list[Checkpoint]: ...

    async def get_at_step(self, thread_id: str, step_index: int) -> Checkpoint | None:
        """Return the checkpoint of *thread_id* at exactly *step_index*."""
        for checkpoint in await self.list_thread(thread_id):
            if checkpoint.step_index == step_index:
                return checkpoint
        return None

    async def fork(
        self,
        thread_id: str,
        new_thread_id: str,
        at_step: int | None = None,
    ) -> Checkpoint | None:
        """Copy *thread_id*'s history up to *at_step* into *new_thread_id*.

        Every copied checkpoint gets a fresh id, a deep-copied state (the
        branch must never alias the original's mutable state), and
        ``forked_from`` / ``forked_at_step`` metadata. Returns the new
        branch's head checkpoint — pass its ``checkpoint_id`` as the graph's
        ``resume_from=`` to replay from the fork point. Returns None when the
        source thread is empty or *at_step* does not exist; raises
        ValueError when *new_thread_id* already has history (a fork must
        never silently splice into an existing thread).
        """
        import copy
        import uuid

        if await self.get_latest(new_thread_id) is not None:
            raise ValueError(f"Thread '{new_thread_id}' already has checkpoints")

        history = await self.list_thread(thread_id)
        if not history:
            return None
        if at_step is not None:
            history = [c for c in history if c.step_index <= at_step]
            if not history or history[-1].step_index != at_step:
                return None

        head: Checkpoint | None = None
        for source in history:
            head = Checkpoint(
                checkpoint_id=str(uuid.uuid4()),
                thread_id=new_thread_id,
                state=copy.deepcopy(source.state),
                next_nodes=list(source.next_nodes),
                step_index=source.step_index,
                metadata={
                    **copy.deepcopy(source.metadata),
                    "forked_from": thread_id,
                    "forked_at_step": history[-1].step_index,
                },
                raw_log=source.raw_log,
            )
            await self.save(head)
        return head


class InMemoryCheckpointer(Checkpointer):
    """In-memory checkpointer for development and testing."""

    def __init__(self) -> None:
        self._store: dict[str, Checkpoint] = {}
        self._threads: dict[str, list[str]] = {}

    async def save(self, checkpoint: Checkpoint) -> None:
        self._store[checkpoint.checkpoint_id] = checkpoint
        thread_list = self._threads.setdefault(checkpoint.thread_id, [])
        thread_list.append(checkpoint.checkpoint_id)

    async def get(self, checkpoint_id: str) -> Checkpoint | None:
        return self._store.get(checkpoint_id)

    async def get_latest(self, thread_id: str) -> Checkpoint | None:
        thread_list = self._threads.get(thread_id, [])
        if not thread_list:
            return None
        return self._store.get(thread_list[-1])

    async def list_thread(self, thread_id: str) -> list[Checkpoint]:
        thread_list = self._threads.get(thread_id, [])
        return [self._store[cid] for cid in thread_list if cid in self._store]


class SQLiteCheckpointer(Checkpointer):
    """SQLite-backed checkpointer for durable local persistence."""

    def __init__(self, db_path: str | Path = ".checkpoints.db") -> None:
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    next_nodes TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    raw_log TEXT
                )
            """)
            # Backfill column on pre-existing tables from older schema.
            try:
                self._conn.execute("ALTER TABLE checkpoints ADD COLUMN raw_log TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_thread
                ON checkpoints(thread_id, step_index)
            """)
            self._conn.commit()
        return self._conn

    async def save(self, checkpoint: Checkpoint) -> None:
        conn = self._get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO checkpoints
            (checkpoint_id, thread_id, state, next_nodes, step_index, metadata, raw_log)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                checkpoint.checkpoint_id,
                checkpoint.thread_id,
                json.dumps(checkpoint.state),
                json.dumps(checkpoint.next_nodes),
                checkpoint.step_index,
                json.dumps(checkpoint.metadata),
                checkpoint.raw_log,
            ),
        )
        conn.commit()

    async def get(self, checkpoint_id: str) -> Checkpoint | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM checkpoints WHERE checkpoint_id = ?",
            (checkpoint_id,),
        ).fetchone()
        return self._row_to_checkpoint(row) if row else None

    async def get_latest(self, thread_id: str) -> Checkpoint | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM checkpoints WHERE thread_id = ? ORDER BY step_index DESC LIMIT 1",
            (thread_id,),
        ).fetchone()
        return self._row_to_checkpoint(row) if row else None

    async def list_thread(self, thread_id: str) -> list[Checkpoint]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM checkpoints WHERE thread_id = ? ORDER BY step_index",
            (thread_id,),
        ).fetchall()
        return [self._row_to_checkpoint(r) for r in rows]

    def _row_to_checkpoint(self, row: tuple) -> Checkpoint:
        return Checkpoint(
            checkpoint_id=row[0],
            thread_id=row[1],
            state=json.loads(row[2]),
            next_nodes=json.loads(row[3]),
            step_index=row[4],
            metadata=json.loads(row[5]) if row[5] else {},
            raw_log=row[6] if len(row) > 6 else None,
        )

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
