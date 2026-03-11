"""Persistent usage tracking and conversation storage in PostgreSQL.

Stores cumulative token usage, cost, per-model/per-agent stats,
and conversation history (messages per conversation_id).
Falls back gracefully if DB is unavailable.
"""

from __future__ import annotations

import os
import time
from typing import Any


class UsageDB:
    """Async usage stats persistence with PostgreSQL."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or os.environ.get("DATABASE_URL", "")
        self._pool = None
        self._available = False
        # In-memory accumulator (always works, synced to DB)
        self._totals = {
            "total_tokens": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cost_usd": 0.0,
            "total_requests": 0,
        }
        self._per_model: dict[str, dict[str, Any]] = {}
        self._per_agent: dict[str, dict[str, Any]] = {}

    async def setup(self) -> None:
        """Initialize DB connection and create tables."""
        if not self._dsn:
            return
        try:
            import asyncpg

            self._pool = await asyncpg.create_pool(
                self._dsn,
                min_size=1,
                max_size=3,
                command_timeout=10,
            )
            async with self._pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS usage_stats (
                        id SERIAL PRIMARY KEY,
                        ts DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW()),
                        model TEXT NOT NULL DEFAULT '',
                        agent TEXT NOT NULL DEFAULT '',
                        provider TEXT NOT NULL DEFAULT '',
                        input_tokens INTEGER NOT NULL DEFAULT 0,
                        output_tokens INTEGER NOT NULL DEFAULT 0,
                        cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                        elapsed_s DOUBLE PRECISION NOT NULL DEFAULT 0.0,
                        session_id TEXT NOT NULL DEFAULT ''
                    )
                """)
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_stats (ts)
                """)
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_stats (model)
                """)
                # Agent error tracking table
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS agent_errors (
                        id SERIAL PRIMARY KEY,
                        ts DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW()),
                        session_id TEXT NOT NULL DEFAULT '',
                        agent TEXT NOT NULL DEFAULT '',
                        tool_name TEXT NOT NULL DEFAULT '',
                        error_type TEXT NOT NULL DEFAULT '',
                        error_message TEXT NOT NULL DEFAULT '',
                        step_number INTEGER NOT NULL DEFAULT 0,
                        model TEXT NOT NULL DEFAULT '',
                        provider TEXT NOT NULL DEFAULT ''
                    )
                """)
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_errors_ts ON agent_errors (ts)
                """)
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_errors_agent ON agent_errors (agent)
                """)
                # Conversation persistence table
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS conversations (
                        id SERIAL PRIMARY KEY,
                        conv_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        ts DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
                    )
                """)
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_conv_id ON conversations (conv_id)
                """)
            self._available = True
            # Load cumulative totals from DB
            await self._load_totals()
        except Exception:
            self._available = False

    async def _load_totals(self) -> None:
        """Load cumulative totals from DB on startup."""
        if not self._available or not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT
                        COALESCE(SUM(input_tokens), 0) AS total_input,
                        COALESCE(SUM(output_tokens), 0) AS total_output,
                        COALESCE(SUM(input_tokens + output_tokens), 0) AS total_tokens,
                        COALESCE(SUM(cost_usd), 0.0) AS total_cost,
                        COUNT(*) AS total_requests
                    FROM usage_stats
                """)
                if row:
                    self._totals["total_input_tokens"] = row["total_input"]
                    self._totals["total_output_tokens"] = row["total_output"]
                    self._totals["total_tokens"] = row["total_tokens"]
                    self._totals["total_cost_usd"] = float(row["total_cost"])
                    self._totals["total_requests"] = row["total_requests"]

                # Load per-model stats
                rows = await conn.fetch("""
                    SELECT model,
                        SUM(input_tokens + output_tokens) AS tokens,
                        SUM(cost_usd) AS cost,
                        COUNT(*) AS requests,
                        AVG(CASE WHEN elapsed_s > 0 THEN output_tokens / elapsed_s ELSE 0 END) AS avg_speed
                    FROM usage_stats
                    WHERE model != ''
                    GROUP BY model
                    ORDER BY SUM(cost_usd) DESC
                """)
                for r in rows:
                    self._per_model[r["model"]] = {
                        "tokens": r["tokens"],
                        "cost_usd": float(r["cost"]),
                        "requests": r["requests"],
                        "avg_speed": round(float(r["avg_speed"]), 1),
                    }

                # Load per-agent stats
                rows = await conn.fetch("""
                    SELECT agent,
                        SUM(input_tokens + output_tokens) AS tokens,
                        SUM(cost_usd) AS cost,
                        COUNT(*) AS requests
                    FROM usage_stats
                    WHERE agent != ''
                    GROUP BY agent
                    ORDER BY SUM(cost_usd) DESC
                """)
                for r in rows:
                    self._per_agent[r["agent"]] = {
                        "tokens": r["tokens"],
                        "cost_usd": float(r["cost"]),
                        "requests": r["requests"],
                    }
        except Exception:
            pass

    async def record(
        self,
        *,
        model: str = "",
        agent: str = "",
        provider: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
        elapsed_s: float = 0.0,
        session_id: str = "",
    ) -> None:
        """Record a usage entry."""
        total = input_tokens + output_tokens
        self._totals["total_tokens"] += total
        self._totals["total_input_tokens"] += input_tokens
        self._totals["total_output_tokens"] += output_tokens
        self._totals["total_cost_usd"] += cost_usd
        self._totals["total_requests"] += 1

        # Update per-model
        if model:
            if model not in self._per_model:
                self._per_model[model] = {
                    "tokens": 0,
                    "cost_usd": 0.0,
                    "requests": 0,
                    "avg_speed": 0.0,
                }
            m = self._per_model[model]
            m["tokens"] += total
            m["cost_usd"] += cost_usd
            m["requests"] += 1
            if elapsed_s > 0:
                speed = output_tokens / elapsed_s
                m["avg_speed"] = round(
                    (m["avg_speed"] * (m["requests"] - 1) + speed) / m["requests"], 1
                )

        # Update per-agent
        if agent:
            if agent not in self._per_agent:
                self._per_agent[agent] = {"tokens": 0, "cost_usd": 0.0, "requests": 0}
            a = self._per_agent[agent]
            a["tokens"] += total
            a["cost_usd"] += cost_usd
            a["requests"] += 1

        # Persist to DB
        if self._available and self._pool:
            try:
                async with self._pool.acquire() as conn:
                    await conn.execute(
                        """INSERT INTO usage_stats
                           (ts, model, agent, provider, input_tokens, output_tokens, cost_usd, elapsed_s, session_id)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                        time.time(),
                        model,
                        agent,
                        provider,
                        input_tokens,
                        output_tokens,
                        cost_usd,
                        elapsed_s,
                        session_id,
                    )
            except Exception:
                pass

    def get_totals(self) -> dict[str, Any]:
        """Return cumulative totals."""
        return dict(self._totals)

    def get_per_model(self) -> dict[str, dict[str, Any]]:
        """Return per-model breakdown."""
        return dict(self._per_model)

    def get_per_agent(self) -> dict[str, dict[str, Any]]:
        """Return per-agent breakdown."""
        return dict(self._per_agent)

    def get_summary(self) -> dict[str, Any]:
        """Full summary for the dashboard header."""
        return {
            **self._totals,
            "per_model": self.get_per_model(),
            "per_agent": self.get_per_agent(),
            "db_connected": self._available,
        }

    # --- Error tracking ---

    async def record_error(
        self,
        *,
        session_id: str = "",
        agent: str = "",
        tool_name: str = "",
        error_type: str = "",
        error_message: str = "",
        step_number: int = 0,
        model: str = "",
        provider: str = "",
    ) -> None:
        """Record an agent/tool error for tracking and analysis."""
        if not self._available or not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO agent_errors
                       (ts, session_id, agent, tool_name, error_type, error_message,
                        step_number, model, provider)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                    time.time(),
                    session_id,
                    agent,
                    tool_name,
                    error_type,
                    error_message[:2000],
                    step_number,
                    model,
                    provider,
                )
        except Exception:
            pass

    async def get_recent_errors(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent errors for the dashboard."""
        if not self._available or not self._pool:
            return []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id, ts, session_id, agent, tool_name, error_type,
                              error_message, step_number, model, provider
                       FROM agent_errors ORDER BY id DESC LIMIT $1""",
                    limit,
                )
                return [dict(r) for r in rows]
        except Exception:
            return []

    async def get_error_summary(self) -> dict[str, Any]:
        """Return error counts grouped by agent and error_type."""
        if not self._available or not self._pool:
            return {}
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT agent, error_type, COUNT(*) AS count
                    FROM agent_errors
                    GROUP BY agent, error_type
                    ORDER BY count DESC
                """)
                return {
                    "by_agent": [
                        {"agent": r["agent"], "error_type": r["error_type"], "count": r["count"]}
                        for r in rows
                    ],
                    "total": sum(r["count"] for r in rows),
                }
        except Exception:
            return {}

    # --- Conversation persistence ---

    async def create_conversation(self, conv_id: str) -> None:
        """Create a conversation (no-op if DB unavailable — conv just won't persist)."""
        # Nothing to insert; the conv_id is implicit from messages.
        pass

    async def append_message(self, conv_id: str, role: str, content: str) -> None:
        """Append a message to a conversation."""
        if not self._available or not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO conversations (conv_id, role, content, ts) VALUES ($1, $2, $3, $4)",
                    conv_id,
                    role,
                    content,
                    time.time(),
                )
        except Exception:
            pass

    async def get_conversation(self, conv_id: str) -> list[dict[str, str]]:
        """Load all messages for a conversation, ordered chronologically."""
        if not self._available or not self._pool:
            return []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT role, content FROM conversations WHERE conv_id = $1 ORDER BY id",
                    conv_id,
                )
                return [{"role": r["role"], "content": r["content"]} for r in rows]
        except Exception:
            return []

    async def get_recent_messages(self, conv_id: str, limit: int = 6) -> list[dict[str, str]]:
        """Load the most recent N messages for context injection."""
        if not self._available or not self._pool:
            return []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT role, content FROM (
                        SELECT role, content, id FROM conversations
                        WHERE conv_id = $1 ORDER BY id DESC LIMIT $2
                    ) sub ORDER BY id""",
                    conv_id,
                    limit,
                )
                return [{"role": r["role"], "content": r["content"]} for r in rows]
        except Exception:
            return []
