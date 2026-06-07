"""Tests for ``/api/cli/v1/*`` endpoints consumed by the Rust ``ago`` CLI.

These tests stand up a minimal FastAPI app that wires only the bits required
to exercise the CLI routes — the same shape as the auth tests for other
routers.  This keeps them fast and independent of the full dashboard build.
"""

from __future__ import annotations

import json

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
    for path in ("/api/cli/v1/whoami", "/api/cli/v1/version", "/api/cli/v1/run"):
        for prefix in APIKeyMiddleware.EXEMPT_PREFIXES:
            assert not path.startswith(prefix), (
                f"CLI path {path} must not match exempt prefix {prefix}"
            )


# ---------------------------------------------------------------------------
# /api/cli/v1/run SSE streaming
# ---------------------------------------------------------------------------


def _parse_sse(stream: bytes) -> list[dict]:
    """Parse an SSE text/event-stream payload into a list of {event, data}."""
    events: list[dict] = []
    current_event = "message"
    current_data: list[str] = []
    for line in stream.decode("utf-8").splitlines() + [""]:
        if not line:
            if current_data:
                payload = "\n".join(current_data)
                try:
                    parsed = json.loads(payload)
                except Exception:
                    parsed = payload
                events.append({"event": current_event, "data": parsed})
                current_data = []
                current_event = "message"
            continue
        if line.startswith(":"):  # comment / keepalive
            continue
        if line.startswith("event:"):
            current_event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            current_data.append(line[len("data:") :].strip())
    return events


def _install_quick_provider(monkeypatch):
    """Patch _make_provider so the CLI run endpoint executes a fast stub."""
    from agent_orchestrator.core.provider import (
        Completion,
        ModelCapabilities,
        Provider,
        StreamChunk,
        Usage,
    )

    class QuickProvider(Provider):
        @property
        def model_id(self):
            return "quick-1"

        @property
        def capabilities(self):
            return ModelCapabilities(
                max_context=4096,
                supports_tools=False,
                supports_streaming=False,
                max_output_tokens=64,
            )

        @property
        def input_cost_per_million(self) -> float:
            return 0.0

        @property
        def output_cost_per_million(self) -> float:
            return 0.0

        async def complete(self, messages, *, tools=None, system=None, max_tokens=64):
            return Completion(
                content="hello",
                tool_calls=[],
                stop_reason="end_turn",
                usage=Usage(input_tokens=3, output_tokens=2, cost_usd=0.0),
            )

        async def stream(self, messages, *, tools=None, system=None, max_tokens=64):
            yield StreamChunk(content="hello", stop_reason="end_turn")

    monkeypatch.setattr(
        "agent_orchestrator.dashboard.cli_routes._make_provider",
        lambda *args, **kwargs: QuickProvider(),
    )


def test_run_requires_required_fields(monkeypatch):
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
    client = TestClient(_make_app(["secret-key"]))
    resp = client.post(
        "/api/cli/v1/run",
        headers={"X-API-Key": "secret-key"},
        json={"agent": "backend"},
    )
    assert resp.status_code == 400


def test_run_requires_auth(monkeypatch):
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
    client = TestClient(_make_app(["secret-key"]))
    resp = client.post(
        "/api/cli/v1/run",
        json={"agent": "backend", "task": "x", "model": "m"},
    )
    assert resp.status_code == 401


def test_run_streams_start_and_complete(monkeypatch):
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)
    _install_quick_provider(monkeypatch)
    client = TestClient(_make_app(["secret-key"]))
    with client.stream(
        "POST",
        "/api/cli/v1/run",
        headers={"X-API-Key": "secret-key"},
        json={
            "agent": "backend",
            "task": "say hello",
            "model": "quick-1",
            "provider": "ollama",
            "max_steps": 2,
        },
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = b"".join(chunk for chunk in resp.iter_bytes())

    events = _parse_sse(body)
    assert events, f"expected events, got {body!r}"
    assert events[0]["event"] == "start"
    assert "run_id" in events[0]["data"]
    assert events[-1]["event"] == "complete"
    final = events[-1]["data"]
    assert final.get("success") is True
    assert "output" in final or "result" in final or final.get("steps") is not None


def test_run_invalid_provider_returns_400(monkeypatch):
    monkeypatch.delenv("ALLOW_DEV_MODE", raising=False)

    def _explode(*args, **kwargs):
        raise RuntimeError("nope")

    monkeypatch.setattr("agent_orchestrator.dashboard.cli_routes._make_provider", _explode)
    client = TestClient(_make_app(["secret-key"]))
    resp = client.post(
        "/api/cli/v1/run",
        headers={"X-API-Key": "secret-key"},
        json={"agent": "backend", "task": "x", "model": "m"},
    )
    assert resp.status_code == 400
    # The raw exception message must NOT leak to the client; the handler
    # returns a sanitized error and logs the traceback server-side.
    assert "nope" not in resp.text
    assert "provider error" in resp.text


# ===== agent-host prompt handler: turn error surfacing =====


def test_agent_host_turn_end_surfaces_error_reason(monkeypatch):
    """A failed turn must report *why* in the TurnEnd.error field.

    Regression guard: the handler used to send a bare TurnEnd(status=
    "error") with no reason, leaving the CLI showing "✗ turn error" with
    no explanation.
    """
    import asyncio

    import agent_orchestrator.dashboard.cli_routes as cr

    async def _fake_run_agent(*args, **kwargs):
        return {
            "success": False,
            "status": "stalled",
            "output": "",
            "steps_taken": 1,
            "input_tokens": 510,
            "output_tokens": 456,
            "total_cost_usd": 0.0002,
            "error": "Max steps (10) reached",
        }

    monkeypatch.setattr(cr, "run_agent", _fake_run_agent)
    monkeypatch.setattr(cr, "_make_provider", lambda **kw: object())

    sent: list[dict] = []

    class _FakeWS:
        def __init__(self):
            self.app = type("A", (), {"state": type("S", (), {})()})()

        async def send_json(self, data):
            sent.append(data)

    handler = cr._make_agent_host_prompt_handler(_FakeWS())

    class _Hello:
        agent = "backend"
        model = "m"
        provider = "openrouter"

    asyncio.run(handler("do it", object(), "run-1", _Hello()))

    turn_end = next(f for f in sent if f.get("kind") == "turn_end")
    assert turn_end["status"] == "error"
    assert turn_end["error"] == "Max steps (10) reached"
    assert turn_end["input_tokens"] == 510
    assert turn_end["output_tokens"] == 456


def _run_handler_capture_max_steps(hello_max_steps, monkeypatch):
    """Drive the agent-host handler and return the max_steps run_agent got."""
    import asyncio

    import agent_orchestrator.dashboard.cli_routes as cr

    captured: dict = {}

    async def _fake_run_agent(*args, **kwargs):
        captured["max_steps"] = kwargs.get("max_steps")
        return {"success": True, "status": "completed", "output": "ok", "steps_taken": 1}

    monkeypatch.setattr(cr, "run_agent", _fake_run_agent)
    monkeypatch.setattr(cr, "_make_provider", lambda **kw: object())

    class _FakeWS:
        def __init__(self):
            self.app = type("A", (), {"state": type("S", (), {})()})()

        async def send_json(self, data):
            pass

    class _Hello:
        agent = "backend"
        model = "m"
        provider = "openrouter"
        max_steps = hello_max_steps

    handler = cr._make_agent_host_prompt_handler(_FakeWS())
    asyncio.run(handler("do it", object(), "run-1", _Hello()))
    return captured["max_steps"]


def test_agent_host_uses_client_max_steps(monkeypatch):
    # Client-requested value is honoured.
    assert _run_handler_capture_max_steps(25, monkeypatch) == 25


def test_agent_host_max_steps_defaults_when_unset(monkeypatch):
    # 0 (old client / unset) → server default, not the old hard-coded 10.
    assert _run_handler_capture_max_steps(0, monkeypatch) == 30


def test_agent_host_max_steps_clamped(monkeypatch):
    # Absurd values are clamped to the safe ceiling.
    assert _run_handler_capture_max_steps(99999, monkeypatch) == 100
