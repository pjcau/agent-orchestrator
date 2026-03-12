"""Tests for database connection pool resilience (auto-reconnect on stale connections).

Verifies that UsageDB._acquire() and user_store._acquire() recover
from ConnectionDoesNotExistError by reconnecting the pool transparently.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_orchestrator.dashboard.usage_db import UsageDB


class FakeConnectionDoesNotExistError(Exception):
    """Simulate asyncpg.exceptions.ConnectionDoesNotExistError."""

    pass


# Rename to match the pattern checked in _acquire
FakeConnectionDoesNotExistError.__name__ = "ConnectionDoesNotExistError"


class TestUsageDBReconnect:
    """Test UsageDB._acquire() auto-reconnect behavior."""

    @pytest.fixture
    def db(self):
        udb = UsageDB(dsn="postgresql://test:test@localhost/test")
        udb._available = True
        return udb

    @pytest.mark.asyncio
    async def test_acquire_success_no_reconnect(self, db):
        """Normal acquire should work without triggering reconnect."""
        mock_conn = AsyncMock()
        mock_pool = MagicMock()

        @asynccontextmanager
        async def fake_acquire():
            yield mock_conn

        mock_pool.acquire = fake_acquire
        db._pool = mock_pool

        async with db._acquire() as conn:
            assert conn is mock_conn

    @pytest.mark.asyncio
    async def test_acquire_reconnects_on_connection_lost(self, db):
        """Should reconnect pool when ConnectionDoesNotExistError occurs."""
        mock_conn = AsyncMock()
        call_count = 0

        @asynccontextmanager
        async def failing_then_ok_acquire():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise FakeConnectionDoesNotExistError("connection was closed")
            yield mock_conn

        mock_pool = MagicMock()
        mock_pool.acquire = failing_then_ok_acquire
        mock_pool.close = AsyncMock()
        db._pool = mock_pool

        # Patch _reconnect_pool to just reset the pool (simulating reconnection)
        async def fake_reconnect():
            db._pool = mock_pool  # Same mock but call_count now > 1

        with patch.object(db, "_reconnect_pool", side_effect=fake_reconnect):
            async with db._acquire() as conn:
                assert conn is mock_conn
            assert call_count == 2  # First failed, second succeeded

    @pytest.mark.asyncio
    async def test_acquire_raises_non_connection_errors(self, db):
        """Non-connection errors should propagate, not trigger reconnect."""

        @asynccontextmanager
        async def raising_acquire():
            raise ValueError("some other error")
            yield  # pragma: no cover

        mock_pool = MagicMock()
        mock_pool.acquire = raising_acquire
        db._pool = mock_pool

        with pytest.raises(ValueError, match="some other error"):
            async with db._acquire() as _conn:
                pass  # pragma: no cover

    @pytest.mark.asyncio
    async def test_reconnect_pool_creates_new_pool(self, db):
        """_reconnect_pool should close old pool and create a new one."""
        old_pool = MagicMock()
        old_pool.close = AsyncMock()
        db._pool = old_pool

        new_pool = MagicMock()
        mock_asyncpg = MagicMock()
        mock_asyncpg.create_pool = AsyncMock(return_value=new_pool)

        with patch.dict("sys.modules", {"asyncpg": mock_asyncpg}):
            await db._reconnect_pool()

        old_pool.close.assert_awaited_once()
        assert db._pool is new_pool
        assert db._available is True

    @pytest.mark.asyncio
    async def test_reconnect_pool_handles_failure(self, db):
        """If reconnection fails, _available should be set to False."""
        old_pool = MagicMock()
        old_pool.close = AsyncMock()
        db._pool = old_pool

        mock_asyncpg = MagicMock()
        mock_asyncpg.create_pool = AsyncMock(side_effect=OSError("connection refused"))

        with patch.dict("sys.modules", {"asyncpg": mock_asyncpg}):
            await db._reconnect_pool()

        assert db._available is False

    @pytest.mark.asyncio
    async def test_record_graceful_on_db_failure(self, db):
        """record() should update in-memory totals even if DB write fails."""
        db._available = False
        db._pool = None

        await db.record(model="test", input_tokens=100, output_tokens=50, cost_usd=0.01)

        assert db._totals["total_tokens"] == 150
        assert db._totals["total_cost_usd"] == 0.01


class TestUserStoreReconnect:
    """Test user_store._acquire() auto-reconnect behavior."""

    @pytest.mark.asyncio
    async def test_acquire_reconnects_on_connection_lost(self):
        """user_store._acquire() should reconnect on stale connections."""
        from agent_orchestrator.dashboard import user_store

        mock_conn = AsyncMock()
        call_count = 0

        @asynccontextmanager
        async def failing_then_ok_acquire():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise FakeConnectionDoesNotExistError("connection closed")
            yield mock_conn

        mock_pool = MagicMock()
        mock_pool.acquire = failing_then_ok_acquire
        mock_pool.close = AsyncMock()

        original_pool = user_store._pool
        original_available = user_store._db_available
        try:
            user_store._pool = mock_pool
            user_store._db_available = True

            async def fake_reconnect():
                user_store._pool = mock_pool

            with patch.object(user_store, "_reconnect_pool", side_effect=fake_reconnect):
                async with user_store._acquire() as conn:
                    assert conn is mock_conn
                assert call_count == 2
        finally:
            user_store._pool = original_pool
            user_store._db_available = original_available

    @pytest.mark.asyncio
    async def test_acquire_propagates_other_errors(self):
        """Non-connection errors should not trigger reconnect."""
        from agent_orchestrator.dashboard import user_store

        @asynccontextmanager
        async def raising_acquire():
            raise RuntimeError("unrelated error")
            yield  # pragma: no cover

        mock_pool = MagicMock()
        mock_pool.acquire = raising_acquire

        original_pool = user_store._pool
        original_available = user_store._db_available
        try:
            user_store._pool = mock_pool
            user_store._db_available = True

            with pytest.raises(RuntimeError, match="unrelated error"):
                async with user_store._acquire() as _conn:
                    pass  # pragma: no cover
        finally:
            user_store._pool = original_pool
            user_store._db_available = original_available
