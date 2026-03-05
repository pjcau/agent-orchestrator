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
    checkpoint_id: str
    thread_id: str
    state: dict[str, Any]
    next_nodes: list[str]
    step_index: int
    metadata: dict[str, Any] = field(default_factory=dict)


class Checkpointer(ABC):
    """Abstract checkpointer. Save and restore graph state."""

    @abstractmethod
    async def save(self, checkpoint: Checkpoint) -> None: ...

    @abstractmethod
    async def get(self, checkpoint_id: str) -> Checkpoint | None: ...

    @abstractmethod
    async def get_latest(self, thread_id: str) -> Checkpoint | None: ...

    @abstractmethod
    async def list_thread(self, thread_id: str) -> list[Checkpoint]: ...


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
                    metadata TEXT DEFAULT '{}'
                )
            """)
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
            (checkpoint_id, thread_id, state, next_nodes, step_index, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                checkpoint.checkpoint_id,
                checkpoint.thread_id,
                json.dumps(checkpoint.state),
                json.dumps(checkpoint.next_nodes),
                checkpoint.step_index,
                json.dumps(checkpoint.metadata),
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
        )

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
