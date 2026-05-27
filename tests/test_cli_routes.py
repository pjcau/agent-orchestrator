"""Tests for ``/api/cli/v1/*`` endpoints consumed by the Rust ``ago`` CLI.

These tests stand up a minimal FastAPI app that wires only the bits required
to exercise the CLI routes — the same shape as the auth tests for other
routers.  This keeps them fast and independent of the full dashboard build.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from agent_orchestrator.dashboard.auth import APIKeyMiddleware
from agent_orchestrator.dashboard.cli_routes import cli_router


def _make_app(api_keys: list[str]) -> FastAPI:
    app = FastAPI(version="0.2.0")
    app.add_middleware(APIKeyMiddleware, api_keys=api_keys)
    app.include_router(cli_router)
    return app


def test_whoami_requires_api_key(monkeypatch):
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
    client = TestClient(_make_app(["secret-key"]))

    resp = client.get("/api/cli/v1/whoami")
    assert resp.status_code == 401


def test_whoami_succeeds_with_valid_api_key(monkeypatch):
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
    client = TestClient(_make_app(["secret-key"]))

    resp = client.get("/api/cli/v1/whoami", headers={"X-API-Key": "secret-key"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "developer"
    assert body["provider"] == "api-key"
    assert body["server_version"] == "0.2.0"
    # Identity-less response — keep these explicit so the contract is locked.
    assert body["name"] == "api-key"
    assert body["email"] is None


def test_whoami_rejects_wrong_api_key(monkeypatch):
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
    client = TestClient(_make_app(["secret-key"]))

    resp = client.get("/api/cli/v1/whoami", headers={"X-API-Key": "nope"})
    assert resp.status_code == 401


def test_whoami_rejects_api_key_via_query_param(monkeypatch):
    """X-API-Key MUST come from the header. Query params would leak in access logs."""
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
    client = TestClient(_make_app(["secret-key"]))

    resp = client.get("/api/cli/v1/whoami?api_key=secret-key")
    assert resp.status_code == 401


def test_version_endpoint(monkeypatch):
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
    client = TestClient(_make_app(["secret-key"]))

    resp = client.get("/api/cli/v1/version", headers={"X-API-Key": "secret-key"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["server_version"] == "0.2.0"
    assert body["min_cli_version"] == "0.1.0"


def test_version_endpoint_is_not_anonymous(monkeypatch):
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
    client = TestClient(_make_app(["secret-key"]))

    resp = client.get("/api/cli/v1/version")
    assert resp.status_code == 401


def test_whoami_uses_session_user_when_present(monkeypatch):
    """If a JWT session is active, the route surfaces the real user identity."""
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", "unit-test-jwt-secret")

    from agent_orchestrator.dashboard.auth import create_session_token

    token = create_session_token(
        {
            "email": "alice@example.com",
            "name": "Alice",
            "role": "admin",
            "provider": "google",
        }
    )

    client = TestClient(_make_app(["unused"]))
    client.cookies.set("auth_session", token)
    resp = client.get("/api/cli/v1/whoami")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert body["name"] == "Alice"
    assert body["role"] == "admin"
    assert body["provider"] == "google"


def test_cli_routes_are_not_in_exempt_list():
    """Sanity check — CLI endpoints MUST be behind the auth middleware."""
    for path in ("/api/cli/v1/whoami", "/api/cli/v1/version"):
        for prefix in APIKeyMiddleware.EXEMPT_PREFIXES:
            assert not path.startswith(prefix), (
                f"CLI path {path} must not match exempt prefix {prefix}"
            )
