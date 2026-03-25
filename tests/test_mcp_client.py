"""Tests for the MCP client module (core/mcp_client.py).

All external I/O is mocked — no real subprocesses or HTTP calls are made.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_orchestrator.core.mcp_client import (
    MCPClient,
    MCPClientManager,
    MCPServerConfig,
    MCPTool,
    SSETransport,
    ServerCapabilities,
    StdioTransport,
)
from agent_orchestrator.core.skill import SkillRegistry


# ---------------------------------------------------------------------------
# Helpers — minimal in-memory transport stub
# ---------------------------------------------------------------------------


class _FakeTransport:
    """In-memory transport that replays a pre-loaded sequence of responses."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self._sent: list[dict[str, Any]] = []
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    async def send(self, message: dict[str, Any]) -> None:
        self._sent.append(message)

    async def receive(self) -> dict[str, Any]:
        if not self._responses:
            raise EOFError("no more responses")
        return self._responses.pop(0)

    async def close(self) -> None:
        self.closed = True


def _ok(id_: int, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _err(id_: int, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# MCPServerConfig validation
# ---------------------------------------------------------------------------


class TestMCPServerConfig:
    def test_valid_stdio_config(self):
        cfg = MCPServerConfig(transport="stdio", command=["python", "server.py"])
        cfg.validate()  # should not raise

    def test_valid_sse_config(self):
        cfg = MCPServerConfig(transport="sse", url="http://localhost:8080")
        cfg.validate()  # should not raise

    def test_invalid_transport_raises(self):
        cfg = MCPServerConfig(transport="grpc")
        with pytest.raises(ValueError, match="transport must be"):
            cfg.validate()

    def test_stdio_without_command_raises(self):
        cfg = MCPServerConfig(transport="stdio", command=[])
        with pytest.raises(ValueError, match="non-empty 'command'"):
            cfg.validate()

    def test_sse_without_url_raises(self):
        cfg = MCPServerConfig(transport="sse", url="")
        with pytest.raises(ValueError, match="non-empty 'url'"):
            cfg.validate()

    def test_stdio_none_command_raises(self):
        cfg = MCPServerConfig(transport="stdio", command=None)
        with pytest.raises(ValueError, match="non-empty 'command'"):
            cfg.validate()

    def test_sse_none_url_raises(self):
        cfg = MCPServerConfig(transport="sse", url=None)
        with pytest.raises(ValueError, match="non-empty 'url'"):
            cfg.validate()

    def test_optional_fields_default_to_none(self):
        cfg = MCPServerConfig(transport="sse", url="http://x")
        assert cfg.env is None
        assert cfg.headers is None
        assert cfg.command is None


# ---------------------------------------------------------------------------
# StdioTransport
# ---------------------------------------------------------------------------


class TestStdioTransport:
    @pytest.mark.asyncio
    async def test_connect_launches_subprocess(self):
        mock_process = MagicMock()
        mock_process.pid = 42
        mock_process.stdin = AsyncMock()
        mock_process.stdout = AsyncMock()

        with patch(
            "agent_orchestrator.core.mcp_client.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_process,
        ) as mock_exec:
            transport = StdioTransport(command=["fake-server", "--arg"])
            await transport.connect()
            mock_exec.assert_called_once()
            call_args = mock_exec.call_args
            assert "fake-server" in call_args.args or "fake-server" == call_args.args[0]

    @pytest.mark.asyncio
    async def test_send_writes_newline_delimited_json(self):
        mock_stdin = AsyncMock()
        mock_process = MagicMock()
        mock_process.pid = 1
        mock_process.stdin = mock_stdin
        mock_process.stdout = AsyncMock()

        with patch(
            "agent_orchestrator.core.mcp_client.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_process,
        ):
            transport = StdioTransport(command=["srv"])
            await transport.connect()
            msg = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
            await transport.send(msg)

        mock_stdin.write.assert_called_once()
        written = mock_stdin.write.call_args.args[0]
        assert written.endswith(b"\n")
        parsed = json.loads(written.decode().strip())
        assert parsed == msg

    @pytest.mark.asyncio
    async def test_receive_reads_line_from_stdout(self):
        response = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        encoded = json.dumps(response).encode() + b"\n"

        mock_stdout = AsyncMock()
        mock_stdout.readline = AsyncMock(return_value=encoded)
        mock_process = MagicMock()
        mock_process.pid = 1
        mock_process.stdin = AsyncMock()
        mock_process.stdout = mock_stdout

        with patch(
            "agent_orchestrator.core.mcp_client.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_process,
        ):
            transport = StdioTransport(command=["srv"])
            await transport.connect()
            result = await transport.receive()

        assert result == response

    @pytest.mark.asyncio
    async def test_receive_raises_on_eof(self):
        mock_stdout = AsyncMock()
        mock_stdout.readline = AsyncMock(return_value=b"")
        mock_process = MagicMock()
        mock_process.pid = 1
        mock_process.stdin = AsyncMock()
        mock_process.stdout = mock_stdout

        with patch(
            "agent_orchestrator.core.mcp_client.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_process,
        ):
            transport = StdioTransport(command=["srv"])
            await transport.connect()
            with pytest.raises(EOFError):
                await transport.receive()

    @pytest.mark.asyncio
    async def test_send_raises_when_not_connected(self):
        transport = StdioTransport(command=["srv"])
        with pytest.raises(RuntimeError, match="not connected"):
            await transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})

    @pytest.mark.asyncio
    async def test_receive_raises_when_not_connected(self):
        transport = StdioTransport(command=["srv"])
        with pytest.raises(RuntimeError, match="not connected"):
            await transport.receive()

    @pytest.mark.asyncio
    async def test_close_terminates_process(self):
        mock_stdin = MagicMock()
        mock_stdin.close = MagicMock()
        mock_process = MagicMock()
        mock_process.pid = 1
        mock_process.stdin = mock_stdin
        mock_process.stdout = AsyncMock()
        mock_process.terminate = MagicMock()
        mock_process.wait = AsyncMock(return_value=0)

        with patch(
            "agent_orchestrator.core.mcp_client.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_process,
        ):
            transport = StdioTransport(command=["srv"])
            await transport.connect()
            await transport.close()

        mock_process.terminate.assert_called_once()
        assert transport._process is None

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self):
        transport = StdioTransport(command=["srv"])
        # close without connecting should not raise
        await transport.close()


# ---------------------------------------------------------------------------
# SSETransport
# ---------------------------------------------------------------------------


class TestSSETransport:
    @pytest.mark.asyncio
    async def test_connect_creates_http_client(self):
        mock_client = MagicMock()
        mock_client.aclose = AsyncMock()
        mock_client.stream = MagicMock()

        async_cm = AsyncMock()
        async_cm.__aenter__ = AsyncMock(return_value=async_cm)
        async_cm.__aexit__ = AsyncMock(return_value=False)
        async_cm.aiter_lines = AsyncMock(return_value=AsyncMock())

        async def fake_aiter_lines():
            return
            yield  # make it an async generator

        async_cm.aiter_lines = fake_aiter_lines

        mock_client.stream.return_value = async_cm

        with (
            patch("agent_orchestrator.core.mcp_client.asyncio.create_task") as mock_task,
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            transport = SSETransport(url="http://localhost:9000")
            await transport.connect()
            assert transport._client is mock_client
            mock_task.assert_called_once()

        # cleanup
        if transport._reader_task:
            transport._reader_task.cancel()

    @pytest.mark.asyncio
    async def test_send_posts_json(self):
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()

        transport = SSETransport(url="http://localhost:9000")
        transport._client = mock_client

        msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
        await transport.send(msg)

        mock_client.post.assert_called_once_with(
            "http://localhost:9000/message",
            json=msg,
            headers={"Content-Type": "application/json"},
        )
        mock_response.raise_for_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_raises_when_not_connected(self):
        transport = SSETransport(url="http://localhost:9000")
        with pytest.raises(RuntimeError, match="not connected"):
            await transport.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})

    @pytest.mark.asyncio
    async def test_receive_returns_queued_message(self):
        transport = SSETransport(url="http://localhost:9000")
        expected = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        await transport._queue.put(expected)
        result = await transport.receive()
        assert result == expected

    @pytest.mark.asyncio
    async def test_close_cancels_reader_and_closes_client(self):
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()

        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()

        transport = SSETransport(url="http://localhost:9000")
        transport._client = mock_client
        transport._reader_task = mock_task

        # Simulate the task finishing on cancel
        async def wait_for_task():
            pass

        with patch("asyncio.shield", side_effect=asyncio.CancelledError):
            pass  # not needed here

        mock_task_awaitable = asyncio.create_task(asyncio.sleep(0))
        transport._reader_task = mock_task_awaitable
        mock_task_awaitable.cancel()

        await transport.close()

        mock_client.aclose.assert_called_once()
        assert transport._client is None
        assert transport._reader_task is None

    @pytest.mark.asyncio
    async def test_sse_reader_parses_data_lines(self):
        """The _sse_reader co-routine correctly parses SSE data: lines."""
        transport = SSETransport(url="http://localhost:9000")

        lines = [
            "",
            "data: " + json.dumps({"jsonrpc": "2.0", "id": 1, "result": "ok"}),
            "data: not-json-{{{",
            "event: ping",
        ]

        async def fake_aiter_lines(self_):
            for line in lines:
                yield line

        class FakeResponse:
            aiter_lines = fake_aiter_lines

        class FakeStreamCM:
            """Async context manager returning FakeResponse."""

            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, *_):
                pass

        class FakeClient:
            def stream(self, *args, **kwargs):
                return FakeStreamCM()

        transport._client = FakeClient()  # type: ignore[assignment]
        await transport._sse_reader()

        # Only the valid JSON line should have been enqueued
        assert transport._queue.qsize() == 1
        msg = transport._queue.get_nowait()
        assert msg["result"] == "ok"


# ---------------------------------------------------------------------------
# MCPClient
# ---------------------------------------------------------------------------


class TestMCPClient:
    def _make_client(self, responses: list[dict]) -> tuple[MCPClient, _FakeTransport]:
        transport = _FakeTransport(responses)
        client = MCPClient(name="test-server", transport=transport)
        return client, transport

    @pytest.mark.asyncio
    async def test_initialize_returns_capabilities(self):
        responses = [
            _ok(
                1,
                {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "my-server"},
                    "capabilities": {"tools": {}, "resources": {}},
                },
            )
        ]
        client, _ = self._make_client(responses)
        caps = await client.initialize()
        assert isinstance(caps, ServerCapabilities)
        assert caps.tools is True
        assert caps.resources is True

    @pytest.mark.asyncio
    async def test_initialize_handles_empty_response(self):
        client, _ = self._make_client([_ok(1, None)])
        caps = await client.initialize()
        assert caps.tools is False
        assert caps.resources is False

    @pytest.mark.asyncio
    async def test_initialize_raises_on_rpc_error(self):
        client, _ = self._make_client([_err(1, -32600, "invalid request")])
        with pytest.raises(RuntimeError, match="MCP error"):
            await client.initialize()

    @pytest.mark.asyncio
    async def test_list_tools(self):
        responses = [
            _ok(
                1,
                {
                    "tools": [
                        {
                            "name": "search",
                            "description": "Search the web",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"query": {"type": "string"}},
                            },
                        }
                    ]
                },
            )
        ]
        client, transport = self._make_client(responses)
        tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "search"
        assert tools[0].description == "Search the web"
        assert tools[0].server_name == "test-server"
        # Verify the correct RPC method was sent
        assert transport._sent[0]["method"] == "tools/list"

    @pytest.mark.asyncio
    async def test_list_tools_empty(self):
        client, _ = self._make_client([_ok(1, {"tools": []})])
        tools = await client.list_tools()
        assert tools == []

    @pytest.mark.asyncio
    async def test_call_tool_returns_result(self):
        responses = [_ok(1, {"content": [{"type": "text", "text": "42"}]})]
        client, transport = self._make_client(responses)
        result = await client.call_tool("calculate", {"expr": "6*7"})
        assert result == "42"
        sent = transport._sent[0]
        assert sent["method"] == "tools/call"
        assert sent["params"]["name"] == "calculate"
        assert sent["params"]["arguments"] == {"expr": "6*7"}

    @pytest.mark.asyncio
    async def test_call_tool_returns_raw_when_no_content_wrapper(self):
        responses = [_ok(1, {"answer": 42})]
        client, _ = self._make_client(responses)
        result = await client.call_tool("compute", {})
        assert result == {"answer": 42}

    @pytest.mark.asyncio
    async def test_call_tool_raises_on_rpc_error(self):
        client, _ = self._make_client([_err(1, -32601, "method not found")])
        with pytest.raises(RuntimeError, match="MCP error"):
            await client.call_tool("missing_tool", {})

    @pytest.mark.asyncio
    async def test_list_resources(self):
        responses = [
            _ok(
                1,
                {
                    "resources": [
                        {
                            "uri": "file:///README.md",
                            "name": "README",
                            "description": "Project readme",
                            "mimeType": "text/markdown",
                        }
                    ]
                },
            )
        ]
        client, transport = self._make_client(responses)
        resources = await client.list_resources()
        assert len(resources) == 1
        r = resources[0]
        assert r.uri == "file:///README.md"
        assert r.mime_type == "text/markdown"
        assert r.server_name == "test-server"
        assert transport._sent[0]["method"] == "resources/list"

    @pytest.mark.asyncio
    async def test_read_resource(self):
        responses = [
            _ok(
                1,
                {"contents": [{"uri": "file:///README.md", "text": "# Hello"}]},
            )
        ]
        client, transport = self._make_client(responses)
        text = await client.read_resource("file:///README.md")
        assert text == "# Hello"
        sent = transport._sent[0]
        assert sent["method"] == "resources/read"
        assert sent["params"]["uri"] == "file:///README.md"

    @pytest.mark.asyncio
    async def test_read_resource_fallback_string(self):
        client, _ = self._make_client([_ok(1, "plain text")])
        text = await client.read_resource("file:///notes.txt")
        assert text == "plain text"

    @pytest.mark.asyncio
    async def test_message_ids_auto_increment(self):
        responses = [_ok(1, {}), _ok(2, {"tools": []})]
        client, transport = self._make_client(responses)
        await client.initialize()
        await client.list_tools()
        assert transport._sent[0]["id"] == 1
        assert transport._sent[1]["id"] == 2

    @pytest.mark.asyncio
    async def test_close_calls_transport_close(self):
        transport = _FakeTransport([])
        client = MCPClient(name="t", transport=transport)
        await client.close()
        assert transport.closed is True


# ---------------------------------------------------------------------------
# MCPClientManager
# ---------------------------------------------------------------------------


class TestMCPClientManager:
    def _manager_with_server(
        self, server_name: str = "srv", tools: list[MCPTool] | None = None
    ) -> tuple[MCPClientManager, _FakeTransport]:
        """Build a manager with a pre-connected server stub."""
        tools = tools or [
            MCPTool(
                name="greet",
                description="Greet someone",
                input_schema={"type": "object", "properties": {"name": {"type": "string"}}},
                server_name=server_name,
            )
        ]
        init_response = _ok(
            1,
            {"capabilities": {"tools": {}}, "protocolVersion": "2024-11-05"},
        )
        tool_response = _ok(
            2,
            {
                "tools": [
                    {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
                    for t in tools
                ]
            },
        )
        transport = _FakeTransport([init_response, tool_response])
        manager = MCPClientManager()
        return manager, transport

    @pytest.mark.asyncio
    async def test_add_server_connects_and_caches_tools(self):
        manager, transport = self._manager_with_server("alpha")

        async def fake_connect() -> None:
            transport.connected = True

        transport.connect = fake_connect  # type: ignore[method-assign]

        config = MCPServerConfig(transport="stdio", command=["fake"])

        # Patch StdioTransport so we get our controlled transport
        with patch(
            "agent_orchestrator.core.mcp_client.StdioTransport",
            return_value=transport,
        ):
            await manager.add_server("alpha", config)

        assert "alpha" in manager.list_servers()
        tools = manager.get_all_tools()
        assert len(tools) == 1
        assert tools[0].name == "alpha/greet"

    @pytest.mark.asyncio
    async def test_add_server_validates_config(self):
        manager = MCPClientManager()
        bad_config = MCPServerConfig(transport="stdio", command=[])
        with pytest.raises(ValueError):
            await manager.add_server("x", bad_config)

    @pytest.mark.asyncio
    async def test_remove_server(self):
        manager, transport = self._manager_with_server("beta")
        transport.connect = AsyncMock()  # type: ignore[method-assign]

        config = MCPServerConfig(transport="stdio", command=["fake"])
        with patch(
            "agent_orchestrator.core.mcp_client.StdioTransport",
            return_value=transport,
        ):
            await manager.add_server("beta", config)

        assert "beta" in manager.list_servers()
        await manager.remove_server("beta")
        assert "beta" not in manager.list_servers()
        assert transport.closed is True

    @pytest.mark.asyncio
    async def test_remove_nonexistent_server_is_noop(self):
        manager = MCPClientManager()
        await manager.remove_server("ghost")  # should not raise

    def test_list_servers_empty(self):
        manager = MCPClientManager()
        assert manager.list_servers() == []

    def test_get_all_tools_empty(self):
        manager = MCPClientManager()
        assert manager.get_all_tools() == []

    @pytest.mark.asyncio
    async def test_get_all_tools_prefixed(self):
        manager, transport = self._manager_with_server("myserver")
        transport.connect = AsyncMock()  # type: ignore[method-assign]

        config = MCPServerConfig(transport="stdio", command=["fake"])
        with patch(
            "agent_orchestrator.core.mcp_client.StdioTransport",
            return_value=transport,
        ):
            await manager.add_server("myserver", config)

        tools = manager.get_all_tools()
        assert all(t.name.startswith("myserver/") for t in tools)

    @pytest.mark.asyncio
    async def test_call_tool_delegates_to_client(self):
        manager, transport = self._manager_with_server("remote")
        transport.connect = AsyncMock()  # type: ignore[method-assign]

        config = MCPServerConfig(transport="stdio", command=["fake"])
        with patch(
            "agent_orchestrator.core.mcp_client.StdioTransport",
            return_value=transport,
        ):
            await manager.add_server("remote", config)

        # Inject a tool-call response
        transport._responses.append(_ok(3, {"content": [{"text": "hello Alice"}]}))
        result = await manager.call_tool("remote", "greet", {"name": "Alice"})
        assert result == "hello Alice"

    @pytest.mark.asyncio
    async def test_call_tool_raises_for_unknown_server(self):
        manager = MCPClientManager()
        with pytest.raises(KeyError, match="not connected"):
            await manager.call_tool("ghost", "ping", {})

    @pytest.mark.asyncio
    async def test_add_server_cleanup_on_failure(self):
        """If initialize() fails after connect(), the transport is closed."""
        transport = _FakeTransport([_err(1, -32000, "server error")])
        transport.connect = AsyncMock()  # type: ignore[method-assign]

        config = MCPServerConfig(transport="stdio", command=["bad-server"])
        with patch(
            "agent_orchestrator.core.mcp_client.StdioTransport",
            return_value=transport,
        ):
            with pytest.raises(RuntimeError):
                manager = MCPClientManager()
                await manager.add_server("bad", config)

        assert transport.closed is True
        assert "bad" not in MCPClientManager().list_servers()


# ---------------------------------------------------------------------------
# Tool injection into SkillRegistry
# ---------------------------------------------------------------------------


class TestRegisterMCPTools:
    def _manager_with_mock_tools(self, server: str, tool_names: list[str]) -> MCPClientManager:
        manager = MCPClientManager()
        manager._tools[server] = [
            MCPTool(
                name=n,
                description=f"Tool {n}",
                input_schema={"type": "object", "properties": {}},
                server_name=server,
            )
            for n in tool_names
        ]
        # Inject a stub client so call_tool works
        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value="stub-result")
        manager._clients[server] = mock_client  # type: ignore[assignment]
        return manager

    def test_register_mcp_tools_returns_count(self):
        manager = self._manager_with_mock_tools("srv", ["alpha", "beta", "gamma"])
        registry = SkillRegistry()
        count = registry.register_mcp_tools(manager)
        assert count == 3

    def test_register_mcp_tools_skills_are_accessible(self):
        manager = self._manager_with_mock_tools("myserver", ["search", "compute"])
        registry = SkillRegistry()
        registry.register_mcp_tools(manager)
        skill_names = registry.list_skills()
        assert "myserver/search" in skill_names
        assert "myserver/compute" in skill_names

    @pytest.mark.asyncio
    async def test_mcp_skill_execute_calls_client_manager(self):
        manager = self._manager_with_mock_tools("ext", ["ping"])
        registry = SkillRegistry()
        registry.register_mcp_tools(manager)

        result = await registry.execute("ext/ping", {"msg": "hello"})
        assert result.success is True
        assert result.output == "stub-result"
        manager._clients["ext"].call_tool.assert_called_once_with("ping", {"msg": "hello"})

    @pytest.mark.asyncio
    async def test_mcp_skill_execute_wraps_exception(self):
        manager = self._manager_with_mock_tools("ext", ["fail"])
        manager._clients["ext"].call_tool = AsyncMock(side_effect=RuntimeError("boom"))
        registry = SkillRegistry()
        registry.register_mcp_tools(manager)

        result = await registry.execute("ext/fail", {})
        assert result.success is False
        assert "boom" in result.error

    def test_register_mcp_tools_wrong_type_raises(self):
        registry = SkillRegistry()
        with pytest.raises(TypeError):
            registry.register_mcp_tools("not-a-manager")  # type: ignore[arg-type]

    def test_skill_category_is_mcp(self):
        manager = self._manager_with_mock_tools("s", ["t"])
        registry = SkillRegistry()
        registry.register_mcp_tools(manager)
        skill = registry.get("s/t")
        assert skill is not None
        assert skill.category == "mcp"

    def test_register_empty_manager_returns_zero(self):
        manager = MCPClientManager()
        registry = SkillRegistry()
        count = registry.register_mcp_tools(manager)
        assert count == 0

    def test_register_mcp_tools_appears_in_summaries(self):
        manager = self._manager_with_mock_tools("srv", ["tool1"])
        registry = SkillRegistry()
        registry.register_mcp_tools(manager)
        names = [s.name for s in registry.get_summaries()]
        assert "srv/tool1" in names
