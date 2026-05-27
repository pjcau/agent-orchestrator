"""Tests for the device-flow OAuth (RFC 8628) endpoints under ``/api/cli/v1/auth``.

Two layers are exercised:

1. ``DeviceFlowStore`` — pure unit tests for the state machine
   (create → approve → consume; deny / expire transitions; cleanup).
2. The FastAPI endpoints, end-to-end against a ``TestClient`` with the
   real ``APIKeyMiddleware`` mounted.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from agent_orchestrator.dashboard.auth import APIKeyMiddleware
from agent_orchestrator.dashboard.cli_device_flow import (
    DEFAULT_INTERVAL,
    EPHEMERAL_KEY_PREFIX,
    STATUS_APPROVED,
    STATUS_DENIED,
    STATUS_EXPIRED,
    DeviceFlowStore,
    normalize_user_code,
)
from agent_orchestrator.dashboard.cli_routes import cli_router


# ---------------------------------------------------------------------------
# Pure unit tests on the store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_create_returns_unique_codes():
    store = DeviceFlowStore()
    a = await store.create()
    b = await store.create()
    assert a.device_code != b.device_code
    assert a.user_code != b.user_code
    assert a.status == "authorization_pending"


@pytest.mark.asyncio
async def test_store_approve_then_consume_marks_single_use():
    store = DeviceFlowStore()
    flow = await store.create()
    approved, token = await store.approve(flow.user_code, {"name": "alice", "role": "admin"})
    assert approved.status == STATUS_APPROVED
    assert token.startswith(EPHEMERAL_KEY_PREFIX)
    consumed = await store.consume_token(flow.device_code)
    assert consumed is not None
    assert consumed.access_token == token
    # The same device_code is no longer in the store.
    assert await store.lookup_by_device_code(flow.device_code) is None


@pytest.mark.asyncio
async def test_store_deny_blocks_approval():
    store = DeviceFlowStore()
    flow = await store.create()
    await store.deny(flow.user_code)
    refreshed = await store.lookup_by_user_code(flow.user_code)
    assert refreshed is not None
    assert refreshed.status == STATUS_DENIED
    with pytest.raises(KeyError):
        await store.approve(flow.user_code, {"name": "x"})


@pytest.mark.asyncio
async def test_store_cleanup_removes_expired():
    store = DeviceFlowStore()
    flow = await store.create(expires_in=0)
    # Make sure the wall clock advances past expiry.
    await asyncio.sleep(0.01)
    removed = await store.cleanup()
    assert removed == 1
    assert await store.lookup_by_device_code(flow.device_code) is None


@pytest.mark.asyncio
async def test_store_lookup_user_code_is_case_insensitive():
    store = DeviceFlowStore()
    flow = await store.create()
    found = await store.lookup_by_user_code(flow.user_code.lower())
    assert found is not None
    assert found.device_code == flow.device_code


def test_normalize_user_code_round_trips():
    assert normalize_user_code("abcd-efgh") == "ABCD-EFGH"
    assert normalize_user_code("abcdefgh") == "ABCD-EFGH"
    assert normalize_user_code("abcd efgh") == "ABCD-EFGH"
    # 0 is not in the alphabet — gets stripped.
    assert normalize_user_code("0bcd-efgh") is None


# ---------------------------------------------------------------------------
# End-to-end against TestClient
# ---------------------------------------------------------------------------


def _make_app(api_keys: list[str]) -> FastAPI:
    app = FastAPI(version="0.2.0")
    app.add_middleware(APIKeyMiddleware, api_keys=api_keys)
    app.include_router(cli_router)
    # The middleware uses request.app.state.ephemeral_api_keys — initialise it
    # to an empty dict so the in-app code does not have to defensively create.
    app.state.ephemeral_api_keys = {}
    return app


def test_device_endpoint_is_anonymous(monkeypatch):
    """device-start is the bootstrap endpoint — it MUST be reachable without a key.

    Otherwise the CLI could never use `ago login --device` to acquire its
    first token. The trust boundary is moved to the browser-side approval
    step, which still requires a valid dashboard session.
    """
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
    client = TestClient(_make_app(["secret"]))
    resp = client.post("/api/cli/v1/auth/device-start")
    assert resp.status_code == 200
    assert "device_code" in resp.json()


def test_device_endpoint_returns_pair(monkeypatch):
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
    client = TestClient(_make_app(["secret"]))
    resp = client.post("/api/cli/v1/auth/device-start", headers={"X-API-Key": "secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert "device_code" in body
    assert "user_code" in body
    assert "verification_uri" in body
    assert "verification_uri_complete" in body
    assert body["interval"] == DEFAULT_INTERVAL


def test_token_endpoint_pending_until_approved(monkeypatch):
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", "unit-test-jwt-secret")
    client = TestClient(_make_app(["secret"]))

    start = client.post("/api/cli/v1/auth/device-start", headers={"X-API-Key": "secret"}).json()
    device_code = start["device_code"]
    user_code = start["user_code"]

    poll = client.post(
        "/api/cli/v1/auth/device-poll",
        headers={"X-API-Key": "secret"},
        json={"device_code": device_code},
    )
    assert poll.status_code == 400
    assert poll.json()["error"] == "authorization_pending"

    # Approve as an authenticated browser via JWT cookie.
    from agent_orchestrator.dashboard.auth import create_session_token

    token = create_session_token(
        {
            "email": "alice@example.com",
            "name": "Alice",
            "role": "admin",
            "provider": "google",
        }
    )
    client.cookies.set("auth_session", token)
    approve = client.post(
        "/api/cli/v1/auth/device/approve",
        data={"user_code": user_code, "decision": "approve"},
    )
    assert approve.status_code == 200
    # Drop the cookie so the next poll uses only the API key.
    client.cookies.clear()
    # The store enforces RFC 8628's `slow_down` if we poll too quickly — wait
    # the interval out.
    time.sleep(DEFAULT_INTERVAL + 1)
    poll2 = client.post(
        "/api/cli/v1/auth/device-poll",
        headers={"X-API-Key": "secret"},
        json={"device_code": device_code},
    )
    assert poll2.status_code == 200, poll2.text
    access_token = poll2.json()["access_token"]
    assert access_token.startswith(EPHEMERAL_KEY_PREFIX)
    # And the token is now accepted by the middleware.
    whoami = client.get("/api/cli/v1/whoami", headers={"X-API-Key": access_token})
    assert whoami.status_code == 200
    assert whoami.json()["provider"] == "device-flow"


def test_token_endpoint_unknown_device(monkeypatch):
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
    client = TestClient(_make_app(["secret"]))
    resp = client.post(
        "/api/cli/v1/auth/device-poll",
        headers={"X-API-Key": "secret"},
        json={"device_code": "does-not-exist"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "unknown_device_code"


def test_token_endpoint_expired(monkeypatch):
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
    client = TestClient(_make_app(["secret"]))
    # Reach into the app's store directly to inject an already-expired flow.
    store = DeviceFlowStore()
    client.app.state.device_flow_store = store

    async def _inject():
        return await store.create(expires_in=0)

    flow = asyncio.new_event_loop().run_until_complete(_inject())
    time.sleep(0.05)
    resp = client.post(
        "/api/cli/v1/auth/device-poll",
        headers={"X-API-Key": "secret"},
        json={"device_code": flow.device_code},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == STATUS_EXPIRED


def test_approval_page_requires_session(monkeypatch):
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
    client = TestClient(_make_app(["secret"]))
    resp = client.get(
        "/api/cli/v1/auth/device?user_code=ABCD-EFGH", headers={"X-API-Key": "secret"}
    )
    # API-key auth does NOT set request.state.user, so the page should refuse
    # because there is no user identity to attribute the approval to.
    assert resp.status_code == 401
    assert "Sign in required" in resp.text


def test_approval_page_renders_with_session(monkeypatch):
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", "unit-test-jwt-secret")
    from agent_orchestrator.dashboard.auth import create_session_token

    app = _make_app(["secret"])
    client = TestClient(app)

    # Start a flow so the user_code exists.
    started = client.post("/api/cli/v1/auth/device-start", headers={"X-API-Key": "secret"}).json()
    user_code = started["user_code"]

    token = create_session_token(
        {
            "email": "alice@example.com",
            "name": "Alice",
            "role": "developer",
            "provider": "google",
        }
    )
    client.cookies.set("auth_session", token)
    resp = client.get(f"/api/cli/v1/auth/device?user_code={user_code}")
    assert resp.status_code == 200
    assert "Authorize CLI" in resp.text
    assert "Alice" in resp.text


def test_denied_flow_returns_access_denied(monkeypatch):
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
    monkeypatch.setenv("JWT_SECRET_KEY", "unit-test-jwt-secret")
    from agent_orchestrator.dashboard.auth import create_session_token

    client = TestClient(_make_app(["secret"]))
    started = client.post("/api/cli/v1/auth/device-start", headers={"X-API-Key": "secret"}).json()
    user_code = started["user_code"]
    device_code = started["device_code"]

    token = create_session_token(
        {"email": "x@y.io", "name": "X", "role": "admin", "provider": "google"}
    )
    client.cookies.set("auth_session", token)
    deny = client.post(
        "/api/cli/v1/auth/device/approve",
        data={"user_code": user_code, "decision": "deny"},
    )
    assert deny.status_code == 200
    client.cookies.clear()
    time.sleep(DEFAULT_INTERVAL + 1)
    poll = client.post(
        "/api/cli/v1/auth/device-poll",
        headers={"X-API-Key": "secret"},
        json={"device_code": device_code},
    )
    assert poll.status_code == 400
    assert poll.json()["error"] == STATUS_DENIED
