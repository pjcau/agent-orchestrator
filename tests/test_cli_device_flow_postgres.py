"""Tests for :class:`PostgresDeviceFlowStore`.

Runs only when ``TEST_POSTGRES_URL`` is set — otherwise the whole module is
skipped. Local devs without postgres see no failures; CI with postgres
configures the env var and the same suite that exercises the in-memory
store now exercises the persistent one too.

Note: each test uses a unique ``table_name`` so concurrent runs do not stomp
on each other, and the table is dropped at teardown.
"""

from __future__ import annotations

import os
import secrets
import time

import pytest

POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL, reason="TEST_POSTGRES_URL not set — skipping Postgres-backed tests"
)


@pytest.fixture
async def store():
    from agent_orchestrator.dashboard.cli_device_flow_postgres import PostgresDeviceFlowStore

    table = f"cli_device_flows_test_{secrets.token_hex(4)}"
    s = PostgresDeviceFlowStore(POSTGRES_URL, table_name=table)
    await s.setup()
    yield s
    pool = await s._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"DROP TABLE IF EXISTS {table}")
    await pool.close()


@pytest.mark.asyncio
async def test_pg_create_and_lookup(store):
    flow = await store.create()
    by_dc = await store.lookup_by_device_code(flow.device_code)
    assert by_dc is not None
    assert by_dc.device_code == flow.device_code
    assert by_dc.status == "authorization_pending"
    by_uc = await store.lookup_by_user_code(flow.user_code)
    assert by_uc is not None
    assert by_uc.user_code == flow.user_code


@pytest.mark.asyncio
async def test_pg_approve_then_consume(store):
    flow = await store.create()
    approved = await store.approve(flow.user_code, {"name": "alice", "role": "admin"})
    assert approved.status == "approved"
    assert approved.user_info == {"name": "alice", "role": "admin"}
    consumed = await store.consume(flow.device_code)
    assert consumed is not None
    assert consumed.status == "approved"
    assert consumed.user_info == {"name": "alice", "role": "admin"}
    # Single-use: the row is gone after consume.
    assert await store.lookup_by_device_code(flow.device_code) is None


@pytest.mark.asyncio
async def test_pg_deny_blocks_approval(store):
    flow = await store.create()
    denied = await store.deny(flow.user_code)
    assert denied.status == "access_denied"
    with pytest.raises(KeyError):
        await store.approve(flow.user_code, {"name": "x"})


@pytest.mark.asyncio
async def test_pg_cleanup_removes_expired(store):
    await store.create(expires_in=0)
    time.sleep(0.05)
    removed = await store.cleanup()
    assert removed >= 1


@pytest.mark.asyncio
async def test_pg_consume_pending_returns_unchanged(store):
    """Consuming a still-pending flow must NOT delete the row.

    The token endpoint relies on this so a polling client does not lose
    its pairing if it polls a fraction too early.
    """
    flow = await store.create()
    consumed = await store.consume(flow.device_code)
    assert consumed is not None
    assert consumed.status == "authorization_pending"
    # Row still present.
    assert await store.lookup_by_device_code(flow.device_code) is not None
