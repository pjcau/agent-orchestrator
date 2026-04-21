"""Integration regression tests for gateway_api error responses.

These lock in the CodeQL fixes around HTTP error handling:
- py/stack-trace-exposure — responses must not contain ``str(exc)`` or
  raw exception message; they must carry a generic, static string.
- py/log-injection — user-controlled values logged during error handling
  must be sanitized with ``_sanitize_log`` (CR/LF/TAB escaped).

We exercise the ASGI app end-to-end so that any future refactor that
reintroduces ``{"error": str(exc)}`` or a raw ``logger.warning(%s, name)``
will trip these tests.
"""

import logging

import pytest


def _make_client():
    from httpx import ASGITransport, AsyncClient
    from agent_orchestrator.dashboard.app import create_dashboard_app

    app = create_dashboard_app()
    return app, AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# resume_run — py/stack-trace-exposure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_run_unknown_id_returns_generic_error(monkeypatch):
    monkeypatch.setenv("ALLOW_DEV_MODE", "true")
    _, ctx = _make_client()
    async with ctx as client:
        resp = await client.post(
            "/api/runs/nonexistent-run-id/resume",
            json={"human_input": {}},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body
    # Must be the fixed generic copy; exception repr/message must not leak.
    assert body["error"] == "Cannot resume run (not found or not interrupted)"
    # Defense-in-depth: no pythonic traces in the response body
    raw = resp.text.lower()
    for banned in ("traceback", "valueerror", "keyerror", "typeerror", "exception"):
        assert banned not in raw


# ---------------------------------------------------------------------------
# mcp_add_server — py/stack-trace-exposure + py/log-injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_add_server_missing_name_returns_400(monkeypatch):
    monkeypatch.setenv("ALLOW_DEV_MODE", "true")
    _, ctx = _make_client()
    async with ctx as client:
        resp = await client.post("/api/mcp/servers", json={"transport": "stdio"})
    assert resp.status_code == 400
    assert resp.json() == {"error": "'name' is required"}


@pytest.mark.asyncio
async def test_mcp_add_server_invalid_config_returns_generic(monkeypatch):
    """Config.validate() ValueError message must not leak into the response."""
    monkeypatch.setenv("ALLOW_DEV_MODE", "true")
    _, ctx = _make_client()
    async with ctx as client:
        # transport omitted -> validate() raises ValueError with a specific message
        resp = await client.post(
            "/api/mcp/servers",
            json={"name": "test-srv", "transport": ""},
        )
    assert resp.status_code == 400
    assert resp.json() == {"error": "Invalid MCP server configuration"}


@pytest.mark.asyncio
async def test_mcp_add_server_connection_failure_returns_generic(monkeypatch):
    """add_server() Exception must be swallowed into a generic 502."""
    monkeypatch.setenv("ALLOW_DEV_MODE", "true")
    app, ctx = _make_client()

    class _BoomManager:
        async def add_server(self, _name, _config):
            raise RuntimeError("secret internals: redis://user:pw@host/0")

        def get_all_tools(self):
            return []

        def list_servers(self):
            return []

    app.state.mcp_client_manager = _BoomManager()
    async with ctx as client:
        resp = await client.post(
            "/api/mcp/servers",
            json={"name": "test-srv", "transport": "stdio", "command": ["echo"]},
        )
    assert resp.status_code == 502
    body = resp.json()
    assert body == {"error": "Failed to connect to MCP server"}
    # The leaked internals must not appear anywhere in the response.
    assert "secret" not in resp.text
    assert "redis" not in resp.text


@pytest.mark.asyncio
async def test_mcp_add_server_logs_sanitized_name(monkeypatch, caplog):
    """Log-injection: a name carrying CRLF must be escaped in log records."""
    monkeypatch.setenv("ALLOW_DEV_MODE", "true")
    app, ctx = _make_client()

    class _BoomManager:
        async def add_server(self, _name, _config):
            raise RuntimeError("boom")

        def get_all_tools(self):
            return []

        def list_servers(self):
            return []

    app.state.mcp_client_manager = _BoomManager()
    attack_name = "evil\nINFO admin login\rgranted\tnow"
    with caplog.at_level(logging.WARNING, logger="agent_orchestrator.dashboard.gateway_api"):
        async with ctx as client:
            await client.post(
                "/api/mcp/servers",
                json={"name": attack_name, "transport": "stdio", "command": ["echo"]},
            )
    # Locate the add_server warning record.
    matches = [r for r in caplog.records if "MCP add_server failed" in r.getMessage()]
    assert matches, "expected an MCP add_server warning"
    rendered = matches[0].getMessage()
    # Raw control chars must be escaped, but the textual content remains.
    assert "\n" not in rendered
    assert "\r" not in rendered
    assert "\t" not in rendered
    assert "\\n" in rendered
    assert "\\r" in rendered
    assert "\\t" in rendered


# ---------------------------------------------------------------------------
# mcp_read_resource — py/stack-trace-exposure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_read_resource_missing_server_returns_404(monkeypatch):
    monkeypatch.setenv("ALLOW_DEV_MODE", "true")
    _, ctx = _make_client()
    async with ctx as client:
        resp = await client.get("/api/mcp/resources/ghost-server/some-uri")
    assert resp.status_code == 404
    assert resp.json() == {"error": "Server 'ghost-server' not connected"}


@pytest.mark.asyncio
async def test_mcp_read_resource_exception_returns_generic(monkeypatch):
    """A failure inside ``client.read_resource`` must not leak the message."""
    monkeypatch.setenv("ALLOW_DEV_MODE", "true")
    app, ctx = _make_client()

    class _BoomClient:
        async def read_resource(self, _uri):
            raise RuntimeError("db://internal/password=hunter2")

    class _Manager:
        _clients = {"srv": _BoomClient()}

        def list_servers(self):
            return ["srv"]

        def get_all_tools(self):
            return []

    app.state.mcp_client_manager = _Manager()
    async with ctx as client:
        resp = await client.get("/api/mcp/resources/srv/any/uri")
    assert resp.status_code == 502
    assert resp.json() == {"error": "Failed to read resource"}
    assert "hunter2" not in resp.text
    assert "password" not in resp.text


@pytest.mark.asyncio
async def test_mcp_read_resource_missing_uri_returns_400(monkeypatch):
    """URI format must be server/resource; a single segment is a 400."""
    monkeypatch.setenv("ALLOW_DEV_MODE", "true")
    _, ctx = _make_client()
    async with ctx as client:
        resp = await client.get("/api/mcp/resources/only-one-segment")
    assert resp.status_code == 400
