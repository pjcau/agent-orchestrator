"""HTTP tests for /api/knowledge/* and the rag_enabled flag in /api/prompt."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.setenv("ALLOW_DEV_MODE", "true")
    # Force the dependency-free embedder so tests don't hit external APIs
    # nor download large models.
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "hash")


@pytest.mark.asyncio
async def test_health_reports_enabled():
    from httpx import ASGITransport, AsyncClient

    from agent_orchestrator.dashboard.app import create_dashboard_app

    app = create_dashboard_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/knowledge/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["embedding_provider"] == "builtin"
    assert body["embedding_dim"] > 0


@pytest.mark.asyncio
async def test_ingest_then_search_round_trips():
    from httpx import ASGITransport, AsyncClient

    from agent_orchestrator.dashboard.app import create_dashboard_app

    app = create_dashboard_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        ingest = await c.post(
            "/api/knowledge/ingest",
            json={
                "text": "# Auth\n\nUse JWT tokens for stateless sessions.",
                "namespace": "agent:backend",
                "source_id": "auth-doc",
            },
        )
        assert ingest.status_code == 200
        body = ingest.json()
        assert body["success"] is True
        assert body["chunks_added"] >= 1
        assert body["namespace"] == "agent:backend"

        search = await c.post(
            "/api/knowledge/search",
            json={
                "query": "Use JWT tokens for stateless sessions.",
                "namespace": "agent:backend",
                "k": 3,
            },
        )
        assert search.status_code == 200
        sbody = search.json()
        assert sbody["embedding_model"] == "hash-md5"
        assert len(sbody["hits"]) >= 1
        assert "Use JWT" in sbody["hits"][0]["text"]


@pytest.mark.asyncio
async def test_search_rejects_unknown_namespace_format():
    from httpx import ASGITransport, AsyncClient

    from agent_orchestrator.dashboard.app import create_dashboard_app

    app = create_dashboard_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/knowledge/search",
            json={"query": "x", "namespace": "no-prefix-allowed"},
        )
    assert resp.status_code == 400
    assert "Unknown namespace" in resp.json()["error"]


@pytest.mark.asyncio
async def test_namespaces_lists_after_ingest():
    from httpx import ASGITransport, AsyncClient

    from agent_orchestrator.dashboard.app import create_dashboard_app

    app = create_dashboard_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/api/knowledge/ingest",
            json={"text": "shared note", "namespace": "shared", "source_id": "n1"},
        )
        await c.post(
            "/api/knowledge/ingest",
            json={"text": "front note", "namespace": "agent:frontend", "source_id": "n2"},
        )
        resp = await c.get("/api/knowledge/namespaces")
    assert resp.status_code == 200
    labels = [n["namespace"] for n in resp.json()["namespaces"]]
    assert "shared" in labels
    assert "agent:frontend" in labels


@pytest.mark.asyncio
async def test_delete_namespace_drops_chunks():
    from httpx import ASGITransport, AsyncClient

    from agent_orchestrator.dashboard.app import create_dashboard_app

    app = create_dashboard_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/api/knowledge/ingest",
            json={"text": "doomed", "namespace": "agent:tmp", "source_id": "x"},
        )
        resp = await c.delete("/api/knowledge/namespaces/agent:tmp")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_prompt_with_rag_enabled_emits_event_and_attaches_summary(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from agent_orchestrator.dashboard.app import create_dashboard_app
    from agent_orchestrator.dashboard.events import EventType

    # Stub run_graph so the test does NOT call any LLM.
    async def fake_run_graph(*args, **kwargs):
        return {
            "success": True,
            "output": "stub",
            "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
            "elapsed_s": 0.01,
        }

    import agent_orchestrator.dashboard.agent_runtime_router as runtime_mod

    monkeypatch.setattr(runtime_mod, "run_graph", fake_run_graph)

    app = create_dashboard_app()
    seen_events: list[str] = []
    bus = app.state.bus

    async def collector():
        sub = bus.subscribe()
        try:
            for _ in range(20):
                evt = await sub.get()
                seen_events.append(evt.event_type.value)
        except Exception:  # pragma: no cover
            pass

    import asyncio

    task = asyncio.create_task(collector())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Ingest something so the retriever has hits
            await c.post(
                "/api/knowledge/ingest",
                json={
                    "text": "# Auth\n\nJWT tokens are stateless.",
                    "namespace": "shared",
                    "source_id": "auth",
                },
            )
            resp = await c.post(
                "/api/prompt",
                json={
                    "prompt": "JWT tokens are stateless",
                    "model": "test-model",
                    "provider": "openrouter",
                    "rag_enabled": True,
                    "rag_namespace": "shared",
                    "rag_k": 3,
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert "rag" in body
            assert body["rag"]["namespace"] == "shared"
            assert body["rag"]["hits"] >= 1
    finally:
        await asyncio.sleep(0.05)
        task.cancel()

    assert EventType.KNOWLEDGE_RETRIEVED.value in seen_events
