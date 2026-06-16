"""Tests for the modular process split (gateway_api + agent_runtime_router).

Verifies:
- gateway_router contains expected management routes
- runtime_router contains expected execution routes
- Composed app (create_dashboard_app) has ALL routes from both routers
- Standalone gateway app works (health check returns 200)
- Standalone runtime app has the runtime routes
- Shared state is accessible from both routers via request.app.state
- gateway_api and agent_runtime_router can be imported independently
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _route_paths(app_or_router) -> set[str]:
    """Return every route path reachable from an app or router.

    Recurses through mounts and Starlette 1.x ``_IncludedRouter`` wrappers.
    In Starlette 1.0 ``app.include_router()`` no longer flattens the child
    routes into ``app.routes``; instead each call appends a single
    ``_IncludedRouter`` node (``path is None``) that exposes the real routes
    via ``.original_router``. A shallow ``r.path`` scan therefore sees none of
    them — which made the composed-app assertions below fail on CI (Starlette
    1.3) while passing locally on the older 0.52. This walks both shapes so the
    test is version-agnostic. (Caught 2026-06-16 when CI's fresh resolve pulled
    starlette 1.3.1 vs 0.52.1 locally.)
    """
    paths: set[str] = set()
    for route in getattr(app_or_router, "routes", []) or []:
        path = getattr(route, "path", None)
        if path:
            paths.add(path)
        # Starlette 1.x: include_router() wraps the child router here.
        original = getattr(route, "original_router", None)
        if original is not None:
            paths |= _route_paths(original)
        # Mounts and nested routers (and Starlette 0.x include flattening).
        if getattr(route, "routes", None):
            paths |= _route_paths(route)
    return paths


# ---------------------------------------------------------------------------
# Independent import tests
# ---------------------------------------------------------------------------


def test_gateway_api_importable():
    """gateway_api can be imported without importing the full app."""
    from agent_orchestrator.dashboard import gateway_api  # noqa: F401

    assert hasattr(gateway_api, "gateway_router")
    assert hasattr(gateway_api, "health_router")
    assert hasattr(gateway_api, "metrics_router")


def test_agent_runtime_router_importable():
    """agent_runtime_router can be imported without importing the full app."""
    from agent_orchestrator.dashboard import agent_runtime_router  # noqa: F401

    assert hasattr(agent_runtime_router, "runtime_router")


# ---------------------------------------------------------------------------
# gateway_router route coverage
# ---------------------------------------------------------------------------


def test_gateway_router_has_expected_routes():
    from agent_orchestrator.dashboard.gateway_api import gateway_router

    paths = _route_paths(gateway_router)

    expected = {
        "/api/session",
        "/api/session/history",
        "/api/jobs/list",
        "/api/jobs/{session_id}",
        "/api/jobs/{session_id}/switch",
        "/api/jobs/{session_id}/restore",
        "/api/jobs/{session_id}/files",
        "/api/jobs/{session_id}/files/{filename:path}",
        "/api/jobs/{session_id}/download",
        "/api/upload",
        "/api/usage",
        "/api/errors",
        "/api/errors/client",
        "/api/alerts/webhook",
        "/api/alerts/recent",
        "/api/sandbox/status",
        "/api/sandbox/{session_id}",
        "/api/mcp/manifest",
        "/api/mcp/tools",
        "/api/mcp/tools/{tool_name}/invoke",
        "/api/mcp/servers",
        "/api/mcp/servers/{name}",
        "/api/mcp/resources/{uri:path}",
        "/api/models",
        "/api/agents",
        "/api/agent/config",
        "/api/files",
        "/api/file",
        "/api/conversation/new",
        "/api/conversations",
        "/api/presets",
        "/api/graph/reset",
        "/api/graph/replay",
        "/api/graph/last-run",
        "/api/snapshot",
        "/api/cache/stats",
        "/api/cache/clear",
        "/api/events",
        "/api/runs",
        "/api/runs/{run_id}",
        "/api/runs/{run_id}/stream",
        "/api/runs/{run_id}/resume",
        "/api/memory/namespaces",
        "/api/memory/stats",
        "/api/skill/invoke",
        "/api/cost/preview",
        "/api/openrouter/pricing",
        "/api/ollama/pull",
        "/api/ollama/model",
    }

    missing = expected - paths
    assert not missing, f"gateway_router missing expected routes: {sorted(missing)}"


def test_health_router_has_health():
    from agent_orchestrator.dashboard.gateway_api import health_router

    paths = _route_paths(health_router)
    assert "/health" in paths


def test_metrics_router_has_metrics():
    from agent_orchestrator.dashboard.gateway_api import metrics_router

    paths = _route_paths(metrics_router)
    assert "/metrics" in paths


# ---------------------------------------------------------------------------
# runtime_router route coverage
# ---------------------------------------------------------------------------


def test_runtime_router_has_expected_routes():
    from agent_orchestrator.dashboard.agent_runtime_router import runtime_router

    paths = _route_paths(runtime_router)

    expected = {
        "/api/prompt",
        "/api/agent/run",
        "/api/team/run",
        "/api/team/status/{job_id}",
        "/api/team/{job_id}/cancel",
        "/ws/stream",
        "/ws",
    }

    missing = expected - paths
    assert not missing, f"runtime_router missing expected routes: {sorted(missing)}"


def test_runtime_router_does_not_contain_gateway_routes():
    """Runtime router must not contain management-only endpoints."""
    from agent_orchestrator.dashboard.agent_runtime_router import runtime_router

    paths = _route_paths(runtime_router)
    gateway_only = {"/api/models", "/api/mcp/manifest", "/api/usage", "/api/errors"}
    overlap = gateway_only & paths
    assert not overlap, f"runtime_router unexpectedly contains gateway routes: {overlap}"


# ---------------------------------------------------------------------------
# Composed app (create_dashboard_app) — all routes present
# ---------------------------------------------------------------------------


@pytest.fixture
def composed_app():
    from agent_orchestrator.dashboard.app import create_dashboard_app
    from agent_orchestrator.dashboard.events import EventBus

    bus = EventBus()
    return create_dashboard_app(bus)


def test_composed_app_has_all_gateway_routes(composed_app):
    """The composed app must include every route from gateway_router."""
    from agent_orchestrator.dashboard.gateway_api import gateway_router

    gateway_paths = _route_paths(gateway_router)
    all_paths = _route_paths(composed_app)

    missing = gateway_paths - all_paths
    assert not missing, f"Composed app missing gateway routes: {sorted(missing)}"


def test_composed_app_has_all_runtime_routes(composed_app):
    """The composed app must include every route from runtime_router."""
    from agent_orchestrator.dashboard.agent_runtime_router import runtime_router

    runtime_paths = _route_paths(runtime_router)
    all_paths = _route_paths(composed_app)

    missing = runtime_paths - all_paths
    assert not missing, f"Composed app missing runtime routes: {sorted(missing)}"


def test_composed_app_has_root_and_static(composed_app):
    """The composition root adds / and /static mount."""
    all_paths = _route_paths(composed_app)
    assert "/" in all_paths or "/static" in all_paths


# ---------------------------------------------------------------------------
# Standalone gateway app — health check
# ---------------------------------------------------------------------------


@pytest.fixture
def gateway_only_app():
    from agent_orchestrator.dashboard.server import _create_gateway_only_app
    from agent_orchestrator.dashboard.events import EventBus

    bus = EventBus()
    return _create_gateway_only_app(bus)


def test_standalone_gateway_health(gateway_only_app):
    """Standalone gateway app responds to /health."""
    client = TestClient(gateway_only_app, raise_server_exceptions=False)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_standalone_gateway_has_no_runtime_routes(gateway_only_app):
    """Standalone gateway app must not expose /api/agent/run or /ws."""
    all_paths = _route_paths(gateway_only_app)

    assert "/api/agent/run" not in all_paths
    assert "/ws" not in all_paths


# ---------------------------------------------------------------------------
# Standalone runtime app
# ---------------------------------------------------------------------------


@pytest.fixture
def runtime_only_app():
    from agent_orchestrator.dashboard.server import _create_runtime_only_app
    from agent_orchestrator.dashboard.events import EventBus

    bus = EventBus()
    return _create_runtime_only_app(bus)


def test_standalone_runtime_has_prompt_route(runtime_only_app):
    """Standalone runtime app exposes /api/prompt."""
    all_paths = _route_paths(runtime_only_app)

    assert "/api/prompt" in all_paths


def test_standalone_runtime_has_no_gateway_routes(runtime_only_app):
    """Standalone runtime app must not expose /api/models or /api/usage."""
    all_paths = _route_paths(runtime_only_app)

    assert "/api/models" not in all_paths
    assert "/api/usage" not in all_paths


# ---------------------------------------------------------------------------
# Shared state accessible from both routers
# ---------------------------------------------------------------------------


def test_shared_state_accessible_from_composed_app(composed_app):
    """All required shared state keys are set on the composed app."""
    state = composed_app.state

    required_keys = [
        "bus",
        "usage_db",
        "job_logger",
        "conv_manager",
        "alert_handler",
        "frontend_error_count",
        "active_ws",
        "active_jobs",
        "ws_api_keys",
        "store_holder",
        "sandbox_manager",
        "run_manager",
        "mcp_client_manager",
    ]

    for key in required_keys:
        assert hasattr(state, key), f"app.state missing key: {key}"


def test_shared_state_bus_is_event_bus(composed_app):
    """app.state.bus must be an EventBus instance."""
    from agent_orchestrator.dashboard.events import EventBus

    assert isinstance(composed_app.state.bus, EventBus)


def test_shared_state_frontend_error_count_is_mutable_list(composed_app):
    """frontend_error_count must be a mutable list (shared counter pattern)."""
    count = composed_app.state.frontend_error_count
    assert isinstance(count, list)
    assert len(count) == 1
    assert isinstance(count[0], int)


# ---------------------------------------------------------------------------
# WebSocket connection test (runtime)
# ---------------------------------------------------------------------------


def test_standalone_runtime_ws_connects(monkeypatch):
    """WebSocket /ws endpoint accepts a connection when dev mode is enabled."""
    from agent_orchestrator.dashboard.server import _create_runtime_only_app
    from agent_orchestrator.dashboard.events import EventBus

    # Enable dev mode so APIKeyMiddleware passes all requests through
    monkeypatch.setenv("ALLOW_DEV_MODE", "true")
    # Remove ENVIRONMENT so dev mode is not blocked
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    bus = EventBus()
    # Re-create the app AFTER setting the env var so middleware picks it up
    app = _create_runtime_only_app(bus)
    app.state.ws_api_keys = set()

    client = TestClient(app, raise_server_exceptions=False)
    with client.websocket_connect("/ws") as ws:
        # Should receive initial snapshot message
        data = ws.receive_json()
        assert data.get("type") == "snapshot"
