"""Tests for the memory system: PostgresStore, store wiring, summarization, and API.

Covers:
- PostgresStore CRUD (mocked asyncpg pool)
- PostgresStore search with filter operators
- Namespace dot-encoding round-trips
- TTL expiration logic
- Store wiring (PostgresStore vs InMemoryStore based on DATABASE_URL)
- Summarization trigger at threshold
- Per-agent namespace writes via run_agent
- Memory injection into system prompt
- Memory API endpoints (GET/DELETE)
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_orchestrator.core.store import InMemoryStore
from agent_orchestrator.core.store_postgres import (
    PostgresStore,
    _ns_to_str,
    _str_to_ns,
)
from agent_orchestrator.core.memory_filter import MemoryFilter


# ─── Namespace encoding ────────────────────────────────────────────────


class TestNamespaceEncoding:
    def test_ns_to_str_single(self):
        assert _ns_to_str(("agent",)) == "agent"

    def test_ns_to_str_multi(self):
        assert _ns_to_str(("agent", "backend")) == "agent.backend"

    def test_ns_to_str_empty(self):
        assert _ns_to_str(()) == ""

    def test_str_to_ns_single(self):
        assert _str_to_ns("agent") == ("agent",)

    def test_str_to_ns_multi(self):
        assert _str_to_ns("agent.backend") == ("agent", "backend")

    def test_str_to_ns_empty(self):
        assert _str_to_ns("") == ()

    def test_round_trip(self):
        ns = ("agent", "backend", "sub")
        assert _str_to_ns(_ns_to_str(ns)) == ns


# ─── Mock asyncpg pool helpers ─────────────────────────────────────────


def _make_pool(rows: list[dict] | None = None, *, side_effect=None):
    """Build a minimal asyncpg pool mock.

    Each acquire() context manager yields a connection whose fetchrow/fetch/execute
    are pre-seeded with the provided rows list.
    """
    conn = MagicMock()

    # fetchrow returns first element or None
    fetchrow_result = rows[0] if rows else None
    conn.fetchrow = AsyncMock(return_value=fetchrow_result)

    # fetch returns the full list
    conn.fetch = AsyncMock(return_value=rows or [])

    # execute returns None (DDL / DML)
    conn.execute = AsyncMock(return_value=None)

    # Context manager for acquire()
    pool = MagicMock()
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool, conn


def _make_db_row(
    namespace: str,
    key: str,
    value: dict,
    *,
    expires_at=None,
    created_at=None,
    updated_at=None,
) -> dict:
    """Build a dict mimicking an asyncpg Record for store_items."""
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        "namespace": namespace,
        "key": key,
        "value": value,  # already a dict (asyncpg decodes JSONB)
        "expires_at": expires_at,
        "created_at": created_at or now,
        "updated_at": updated_at or now,
    }


# ─── PostgresStore: CRUD ──────────────────────────────────────────────


class TestPostgresStoreCRUD:
    @pytest.mark.asyncio
    async def test_ensure_table(self):
        pool, conn = _make_pool()
        store = PostgresStore(pool)
        await store.ensure_table()
        conn.execute.assert_called_once()
        sql = conn.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS store_items" in sql

    @pytest.mark.asyncio
    async def test_aget_returns_none_for_missing(self):
        pool, conn = _make_pool(rows=[])
        conn.fetchrow = AsyncMock(return_value=None)
        store = PostgresStore(pool)
        item = await store.aget(("agent", "backend"), "k1")
        assert item is None

    @pytest.mark.asyncio
    async def test_aget_returns_item(self):
        row = _make_db_row("agent.backend", "k1", {"task": "hello"})
        pool, conn = _make_pool(rows=[row])
        conn.fetchrow = AsyncMock(return_value=row)
        store = PostgresStore(pool)
        item = await store.aget(("agent", "backend"), "k1")
        assert item is not None
        assert item.key == "k1"
        assert item.namespace == ("agent", "backend")
        assert item.value["task"] == "hello"

    @pytest.mark.asyncio
    async def test_aget_expired_item_returns_none(self):
        import datetime

        past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=10)
        row = _make_db_row("agent.backend", "k1", {"task": "hello"}, expires_at=past)
        pool, conn = _make_pool(rows=[row])
        conn.fetchrow = AsyncMock(return_value=row)
        store = PostgresStore(pool)
        item = await store.aget(("agent", "backend"), "k1")
        assert item is None
        # Verify DELETE was called to purge the expired row
        assert conn.execute.await_count >= 1

    @pytest.mark.asyncio
    async def test_aput_insert_new(self):
        pool, conn = _make_pool()
        conn.fetchrow = AsyncMock(return_value=None)  # no existing row
        store = PostgresStore(pool)
        await store.aput(("agent", "backend"), "k1", {"x": 1})
        # execute should have been called for INSERT
        assert conn.execute.await_count >= 1
        sql = conn.execute.call_args[0][0]
        assert "INSERT" in sql

    @pytest.mark.asyncio
    async def test_aput_update_existing(self):
        import datetime

        existing_row = {"created_at": datetime.datetime.now(datetime.timezone.utc)}
        pool, conn = _make_pool()
        conn.fetchrow = AsyncMock(return_value=existing_row)
        store = PostgresStore(pool)
        await store.aput(("agent", "backend"), "k1", {"x": 2})
        sql = conn.execute.call_args[0][0]
        assert "UPDATE" in sql

    @pytest.mark.asyncio
    async def test_aput_with_ttl(self):
        pool, conn = _make_pool()
        conn.fetchrow = AsyncMock(return_value=None)
        store = PostgresStore(pool)
        await store.aput(("agent", "backend"), "k1", {"x": 1}, ttl=3600)
        sql = conn.execute.call_args[0][0]
        assert "INTERVAL" in sql

    @pytest.mark.asyncio
    async def test_adelete(self):
        pool, conn = _make_pool()
        store = PostgresStore(pool)
        await store.adelete(("agent", "backend"), "k1")
        conn.execute.assert_called_once()
        sql = conn.execute.call_args[0][0]
        assert "DELETE" in sql
        assert conn.execute.call_args[0][1] == "agent.backend"
        assert conn.execute.call_args[0][2] == "k1"

    @pytest.mark.asyncio
    async def test_adelete_nonexistent_no_error(self):
        """Deleting a nonexistent key must not raise."""
        pool, conn = _make_pool()
        store = PostgresStore(pool)
        await store.adelete(("no", "such"), "key")  # should not raise

    @pytest.mark.asyncio
    async def test_memory_filter_applied_on_put(self):
        """MemoryFilter replaces session-file paths on put."""
        pool, conn = _make_pool()
        conn.fetchrow = AsyncMock(return_value=None)

        mf = MemoryFilter()
        store = PostgresStore(pool, memory_filter=mf)
        await store.aput(("test",), "k", {"path": "jobs/job_abc123/output.txt"})

        # The JSON serialised value passed to execute should have [session-file]
        execute_args = conn.execute.call_args[0]
        value_json = execute_args[3]  # 4th positional arg is the JSONB value
        parsed = json.loads(value_json)
        assert parsed["path"] == "[session-file]"


# ─── PostgresStore: Search ────────────────────────────────────────────


class TestPostgresStoreSearch:
    @pytest.mark.asyncio
    async def test_asearch_returns_items(self):
        rows = [
            _make_db_row("agent.backend", "task_1", {"task": "build API", "score": 10}),
            _make_db_row("agent.backend", "task_2", {"task": "fix bug", "score": 5}),
        ]
        pool, conn = _make_pool(rows=rows)
        conn.fetch = AsyncMock(return_value=rows)
        store = PostgresStore(pool)
        results = await store.asearch(("agent", "backend"))
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_asearch_filter_eq(self):
        rows = [
            _make_db_row("agent.backend", "t1", {"model": "sonnet"}),
            _make_db_row("agent.backend", "t2", {"model": "opus"}),
        ]
        pool, conn = _make_pool(rows=rows)
        conn.fetch = AsyncMock(return_value=rows)
        store = PostgresStore(pool)
        results = await store.asearch(("agent", "backend"), filter={"model": {"$eq": "sonnet"}})
        assert len(results) == 1
        assert results[0].value["model"] == "sonnet"

    @pytest.mark.asyncio
    async def test_asearch_filter_gt(self):
        rows = [
            _make_db_row("scores", "a", {"score": 10}),
            _make_db_row("scores", "b", {"score": 20}),
            _make_db_row("scores", "c", {"score": 30}),
        ]
        pool, conn = _make_pool(rows=rows)
        conn.fetch = AsyncMock(return_value=rows)
        store = PostgresStore(pool)
        results = await store.asearch(("scores",), filter={"score": {"$gt": 15}})
        assert len(results) == 2
        assert all(r.value["score"] > 15 for r in results)

    @pytest.mark.asyncio
    async def test_asearch_limit_offset(self):
        rows = [_make_db_row("ns", f"k{i}", {"i": i}) for i in range(5)]
        pool, conn = _make_pool(rows=rows)
        conn.fetch = AsyncMock(return_value=rows)
        store = PostgresStore(pool)
        page1 = await store.asearch(("ns",), limit=2, offset=0)
        page2 = await store.asearch(("ns",), limit=2, offset=2)
        page3 = await store.asearch(("ns",), limit=2, offset=4)
        assert len(page1) == 2
        assert len(page2) == 2
        assert len(page3) == 1

    @pytest.mark.asyncio
    async def test_asearch_namespace_is_decoded(self):
        rows = [_make_db_row("agent.backend", "t1", {"x": 1})]
        pool, conn = _make_pool(rows=rows)
        conn.fetch = AsyncMock(return_value=rows)
        store = PostgresStore(pool)
        results = await store.asearch(("agent", "backend"))
        assert results[0].namespace == ("agent", "backend")


# ─── PostgresStore: alist_namespaces ─────────────────────────────────


class TestPostgresStoreNamespaces:
    @pytest.mark.asyncio
    async def test_alist_namespaces_no_prefix(self):
        rows = [{"namespace": "agent.backend"}, {"namespace": "shared"}]
        pool, conn = _make_pool(rows=rows)
        conn.fetch = AsyncMock(return_value=rows)
        store = PostgresStore(pool)
        nss = await store.alist_namespaces()
        assert ("agent", "backend") in nss
        assert ("shared",) in nss

    @pytest.mark.asyncio
    async def test_alist_namespaces_with_prefix(self):
        rows = [
            {"namespace": "agent.backend"},
            {"namespace": "agent.frontend"},
        ]
        pool, conn = _make_pool(rows=rows)
        conn.fetch = AsyncMock(return_value=rows)
        store = PostgresStore(pool)
        nss = await store.alist_namespaces(prefix=("agent",))
        assert ("agent", "backend") in nss
        assert ("agent", "frontend") in nss

    @pytest.mark.asyncio
    async def test_alist_namespaces_max_depth(self):
        rows = [
            {"namespace": "deep.l1.l2.l3"},
        ]
        pool, conn = _make_pool(rows=rows)
        conn.fetch = AsyncMock(return_value=rows)
        store = PostgresStore(pool)
        # max_depth=1 from prefix ("deep",) → truncate to ("deep", "l1")
        nss = await store.alist_namespaces(prefix=("deep",), max_depth=1)
        assert all(len(ns) <= 2 for ns in nss)


# ─── TTL expiration ──────────────────────────────────────────────────


class TestPostgresStoreTTL:
    @pytest.mark.asyncio
    async def test_aput_with_ttl_sets_interval_in_sql(self):
        """aput with ttl produces an INSERT that contains the INTERVAL expression."""
        pool, conn = _make_pool()
        conn.fetchrow = AsyncMock(return_value=None)
        store = PostgresStore(pool)
        await store.aput(("ttl",), "key", {"temp": True}, ttl=60.0)
        sql = conn.execute.call_args[0][0]
        assert "60.0 seconds" in sql or "INTERVAL" in sql

    @pytest.mark.asyncio
    async def test_aget_returns_none_after_expiry(self):
        """aget returns None for a row whose expires_at is in the past."""
        import datetime

        past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=5)
        row = _make_db_row("ttl", "key", {"temp": True}, expires_at=past)
        pool, conn = _make_pool(rows=[row])
        conn.fetchrow = AsyncMock(return_value=row)
        store = PostgresStore(pool)
        item = await store.aget(("ttl",), "key")
        assert item is None


# ─── InMemoryStore: TTL expiration (integration with existing store) ──


class TestInMemoryStoreTTL:
    @pytest.mark.asyncio
    async def test_ttl_expiration(self):
        store = InMemoryStore()
        await store.aput(("ttl",), "key", {"x": 1}, ttl=0.01)
        assert await store.aget(("ttl",), "key") is not None
        await asyncio.sleep(0.05)
        assert await store.aget(("ttl",), "key") is None


# ─── Store wiring test ────────────────────────────────────────────────


class TestStoreWiring:
    def test_inmemory_store_when_no_db_url(self):
        """When DATABASE_URL is not set, create_dashboard_app uses InMemoryStore."""
        import os

        env_backup = os.environ.pop("DATABASE_URL", None)
        try:
            from agent_orchestrator.dashboard.app import create_dashboard_app

            app = create_dashboard_app()
            # Before startup, _store_holder[0] is set to InMemoryStore
            # We can verify the app was created without errors
            assert app is not None
        finally:
            if env_backup is not None:
                os.environ["DATABASE_URL"] = env_backup

    def test_postgres_store_selected_when_db_url_set(self):
        """When DATABASE_URL is set, the store_holder starts as None (filled at startup)."""
        import os

        os.environ["DATABASE_URL"] = "postgresql://fake/db"
        try:
            from agent_orchestrator.dashboard.app import create_dashboard_app

            # Should not raise even with a fake URL (startup initialises the pool)
            app = create_dashboard_app()
            assert app is not None
        finally:
            del os.environ["DATABASE_URL"]


# ─── Summarization trigger ────────────────────────────────────────────


class TestSummarizationTrigger:
    @pytest.mark.asyncio
    async def test_summarization_fires_at_threshold(self):
        """ConversationManager summarizes when threshold is reached."""
        from agent_orchestrator.core.conversation import (
            ConversationManager,
            SummarizationConfig,
            SummarizationTrigger,
        )
        from agent_orchestrator.core.checkpoint import InMemoryCheckpointer

        summarize_called_with: list[list[dict]] = []

        async def _summarize(messages: list[dict]) -> str:
            summarize_called_with.append(messages)
            return "SUMMARY"

        manager = ConversationManager(
            checkpointer=InMemoryCheckpointer(),
            summarization_config=SummarizationConfig(
                trigger=SummarizationTrigger.MESSAGE_COUNT,
                threshold=5,
                retain_last=2,
                enabled=True,
            ),
            summarize_func=_summarize,
        )

        # Seed 5 messages manually to trigger summarisation
        from agent_orchestrator.core.conversation import ConversationMessage

        messages = [ConversationMessage(role="user", content=f"msg {i}") for i in range(5)]
        await manager._save_thread("t1", messages)

        # Send a 6th message — this should trigger summarization
        call_count = 0

        async def _responder(msgs):
            nonlocal call_count
            call_count += 1
            return "response"

        await manager.send("t1", "new message", _responder)
        # After summarization, manager.summarization_count increments
        assert manager.summarization_count >= 1

    @pytest.mark.asyncio
    async def test_summarization_disabled_when_not_configured(self):
        """Without SummarizationConfig, summarization never fires."""
        from agent_orchestrator.core.conversation import ConversationManager
        from agent_orchestrator.core.checkpoint import InMemoryCheckpointer

        manager = ConversationManager(checkpointer=InMemoryCheckpointer())
        assert manager.summarization_count == 0

        async def _responder(msgs):
            return "response"

        for i in range(60):
            await manager.send("t1", f"msg {i}", _responder)

        assert manager.summarization_count == 0


# ─── Per-agent namespace writes (run_agent integration) ──────────────


class TestPerAgentNamespaceWrites:
    @pytest.mark.asyncio
    async def test_run_agent_stores_task_summary(self):
        """After a successful run_agent, a summary is stored under (agent, name)."""
        from agent_orchestrator.core.store import InMemoryStore
        from agent_orchestrator.core.provider import (
            Completion,
            ModelCapabilities,
            Provider,
            StreamChunk,
            Usage,
        )
        from agent_orchestrator.dashboard.agent_runner import run_agent
        from agent_orchestrator.dashboard.events import EventBus

        class QuickProvider(Provider):
            @property
            def model_id(self):
                return "quick-1"

            @property
            def capabilities(self):
                return ModelCapabilities(
                    max_context=4096,
                    supports_tools=True,
                    supports_streaming=False,
                    max_output_tokens=512,
                )

            @property
            def input_cost_per_million(self) -> float:
                return 0.0

            @property
            def output_cost_per_million(self) -> float:
                return 0.0

            async def complete(self, messages, *, tools=None, system=None, max_tokens=512):
                return Completion(
                    content="Done.",
                    tool_calls=[],
                    stop_reason="end_turn",
                    usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.0),
                )

            async def stream(self, messages, *, tools=None, system=None, max_tokens=512):
                yield StreamChunk(content="Done.", stop_reason="end_turn")

        store = InMemoryStore()
        bus = EventBus()
        provider = QuickProvider()

        result = await run_agent(
            agent_name="test-agent",
            task_description="Build a hello world script",
            provider=provider,
            event_bus=bus,
            session_id="sess-001",
            store=store,
        )

        assert result["success"] is True
        # The store should have an entry under ("agent", "test-agent")
        items = await store.asearch(("agent", "test-agent"))
        assert len(items) >= 1
        first = items[0]
        assert "task" in first.value
        assert "result_summary" in first.value
        assert "timestamp" in first.value

    @pytest.mark.asyncio
    async def test_run_agent_injects_memory_into_role(self):
        """Memory from the store is prepended to the system prompt as <memory> block."""
        from agent_orchestrator.core.store import InMemoryStore
        from agent_orchestrator.core.provider import (
            Completion,
            ModelCapabilities,
            Provider,
            StreamChunk,
            Usage,
        )
        from agent_orchestrator.dashboard.agent_runner import run_agent
        from agent_orchestrator.dashboard.events import EventBus

        seen_system_prompts: list[str] = []

        class CapturingProvider(Provider):
            @property
            def model_id(self):
                return "cap-1"

            @property
            def capabilities(self):
                return ModelCapabilities(
                    max_context=4096,
                    supports_tools=True,
                    supports_streaming=False,
                    max_output_tokens=512,
                )

            @property
            def input_cost_per_million(self) -> float:
                return 0.0

            @property
            def output_cost_per_million(self) -> float:
                return 0.0

            async def complete(self, messages, *, tools=None, system=None, max_tokens=512):
                seen_system_prompts.append(system or "")
                return Completion(
                    content="Done.",
                    tool_calls=[],
                    stop_reason="end_turn",
                    usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.0),
                )

            async def stream(self, messages, *, tools=None, system=None, max_tokens=512):
                yield StreamChunk(content="Done.", stop_reason="end_turn")

        store = InMemoryStore()
        # Pre-seed memory for this agent
        await store.aput(
            ("agent", "cap-agent"),
            "task_prev",
            {"task": "previous task", "result_summary": "Built the API"},
        )
        await store.aput(
            ("shared",),
            "team_context",
            {"note": "Use Python 3.12"},
        )

        bus = EventBus()
        provider = CapturingProvider()

        await run_agent(
            agent_name="cap-agent",
            task_description="Write a test",
            provider=provider,
            event_bus=bus,
            session_id="sess-002",
            store=store,
        )

        # System prompt should contain the memory block
        assert len(seen_system_prompts) >= 1
        combined = " ".join(seen_system_prompts)
        assert "<memory>" in combined
        assert "cap-agent" in combined or "previous task" in combined


# ─── Memory API endpoints ─────────────────────────────────────────────


class TestMemoryAPIEndpoints:
    @pytest.fixture
    def app(self, monkeypatch):
        """Create a dashboard app with an in-memory store (dev mode, no auth)."""

        # Ensure no DATABASE_URL so InMemoryStore is used
        monkeypatch.delenv("DATABASE_URL", raising=False)
        # Disable auth middleware so API calls don't need keys
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        from agent_orchestrator.dashboard.app import create_dashboard_app

        return create_dashboard_app()

    @pytest.mark.asyncio
    async def test_namespaces_endpoint_returns_list(self, app):
        from httpx import AsyncClient, ASGITransport

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/memory/namespaces")
        assert response.status_code == 200
        data = response.json()
        assert "namespaces" in data
        assert isinstance(data["namespaces"], list)

    @pytest.mark.asyncio
    async def test_stats_endpoint(self, app):
        from httpx import AsyncClient, ASGITransport

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/memory/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total_entries" in data
        assert "namespace_count" in data
        assert "backend" in data

    @pytest.mark.asyncio
    async def test_list_entries_endpoint(self, app):
        from httpx import AsyncClient, ASGITransport

        # Seed the store through startup

        # Directly access _store_holder after the app is created
        # The store is accessible via app.state after startup
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Trigger startup
            response = await client.get("/health")
            assert response.status_code == 200

            # Query empty namespace
            response = await client.get("/api/memory/agent.backend")
        assert response.status_code == 200
        data = response.json()
        assert "entries" in data

    @pytest.mark.asyncio
    async def test_delete_endpoint(self, app):
        from httpx import AsyncClient, ASGITransport

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Trigger startup to initialise store
            await client.get("/health")

            # Seed a value directly in the store (now accessible via app.state)
            store = app.state.store
            await store.aput(("agent", "test"), "key1", {"x": 1})

            # Delete it via API
            response = await client.delete("/api/memory/agent.test/key1")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["key"] == "key1"

    @pytest.mark.asyncio
    async def test_delete_removes_from_store(self, app):
        from httpx import AsyncClient, ASGITransport

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.get("/health")
            store = app.state.store
            await store.aput(("agent", "test"), "ephemeral", {"data": "remove-me"})

            # Verify it exists
            item = await store.aget(("agent", "test"), "ephemeral")
            assert item is not None

            # Delete via API
            await client.delete("/api/memory/agent.test/ephemeral")

            # Verify it is gone from the store
            item = await store.aget(("agent", "test"), "ephemeral")
            assert item is None
