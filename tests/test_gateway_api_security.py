"""Security regression tests for gateway_api path/error handling.

Covers CodeQL alert fixes on the /api/files and /api/file endpoints:
- py/path-injection (inline realpath + startswith sanitizer)
- py/stack-trace-exposure (generic error responses)
- py/log-injection (sanitized log calls)
"""

import pytest


def _make_client():
    """Build an AsyncClient wired to the dashboard ASGI app."""
    from httpx import ASGITransport, AsyncClient
    from agent_orchestrator.dashboard.app import create_dashboard_app

    app = create_dashboard_app()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
class TestFilesEndpointSanitizer:
    async def test_list_files_empty_path_lists_project_root(self, monkeypatch):
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        async with _make_client() as client:
            resp = await client.get("/api/files")
        assert resp.status_code == 200
        body = resp.json()
        assert body["path"] == ""
        assert isinstance(body["items"], list)

    async def test_list_files_valid_subdir(self, monkeypatch):
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        async with _make_client() as client:
            resp = await client.get("/api/files", params={"path": "src"})
        assert resp.status_code == 200
        assert resp.json()["path"] == "src"

    async def test_list_files_parent_traversal_blocked(self, monkeypatch):
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        async with _make_client() as client:
            resp = await client.get("/api/files", params={"path": "../../../etc"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "Path traversal denied"

    async def test_list_files_absolute_path_blocked(self, monkeypatch):
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        async with _make_client() as client:
            resp = await client.get("/api/files", params={"path": "/etc"})
        assert resp.status_code == 400

    async def test_list_files_null_byte_blocked(self, monkeypatch):
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        async with _make_client() as client:
            resp = await client.get("/api/files", params={"path": "src\x00/evil"})
        assert resp.status_code == 400

    async def test_read_file_valid(self, monkeypatch):
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        async with _make_client() as client:
            resp = await client.get("/api/file", params={"path": "README.md"})
        assert resp.status_code == 200
        assert "content" in resp.json()

    async def test_read_file_parent_traversal_blocked(self, monkeypatch):
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        async with _make_client() as client:
            resp = await client.get("/api/file", params={"path": "../../../etc/passwd"})
        assert resp.status_code == 400

    async def test_read_file_absolute_blocked(self, monkeypatch):
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        async with _make_client() as client:
            resp = await client.get("/api/file", params={"path": "/etc/passwd"})
        assert resp.status_code == 400

    async def test_read_file_mixed_traversal_blocked(self, monkeypatch):
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        async with _make_client() as client:
            resp = await client.get("/api/file", params={"path": "src/../../etc/passwd"})
        assert resp.status_code == 400

    async def test_read_file_not_a_file(self, monkeypatch):
        monkeypatch.setenv("ALLOW_DEV_MODE", "true")
        async with _make_client() as client:
            resp = await client.get("/api/file", params={"path": "src"})
        assert resp.status_code == 404
