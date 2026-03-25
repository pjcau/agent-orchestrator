"""MCP client for connecting to external MCP servers.

Supports stdio and SSE transports. Discovers tools and resources
from external servers and makes them available to agents.

JSON-RPC 2.0 is used as the wire protocol, matching the MCP specification.
No external MCP library is required — the implementation is self-contained.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class MCPServerConfig:
    """Configuration for connecting to an external MCP server."""

    transport: str  # "stdio" or "sse"
    command: Optional[list[str]] = None  # used for stdio transport
    url: Optional[str] = None  # used for sse transport
    env: Optional[dict[str, str]] = None
    headers: Optional[dict[str, str]] = None

    def validate(self) -> None:
        """Raise ValueError when the config is inconsistent."""
        if self.transport not in ("stdio", "sse"):
            raise ValueError(f"transport must be 'stdio' or 'sse', got {self.transport!r}")
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio transport requires a non-empty 'command' list")
        if self.transport == "sse" and not self.url:
            raise ValueError("sse transport requires a non-empty 'url'")


@dataclass
class ServerCapabilities:
    """Capabilities reported by an MCP server during initialisation."""

    tools: bool = False
    resources: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPTool:
    """An MCP tool discovered from an external server."""

    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str = ""


@dataclass
class MCPResource:
    """An MCP resource discovered from an external server."""

    uri: str
    name: str
    description: str
    mime_type: str = "text/plain"
    server_name: str = ""


# ---------------------------------------------------------------------------
# Transport protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class MCPTransport(Protocol):
    """Wire transport for MCP JSON-RPC messages."""

    async def connect(self) -> None:
        """Establish the connection."""
        ...

    async def send(self, message: dict[str, Any]) -> None:
        """Send a JSON-RPC message."""
        ...

    async def receive(self) -> dict[str, Any]:
        """Receive the next JSON-RPC message."""
        ...

    async def close(self) -> None:
        """Tear down the connection."""
        ...


# ---------------------------------------------------------------------------
# Stdio transport
# ---------------------------------------------------------------------------


class StdioTransport:
    """Launch a subprocess and communicate via stdin/stdout JSON-RPC.

    Each message is a newline-delimited JSON object written to the
    subprocess stdin; responses are read from stdout one line at a time.
    """

    def __init__(self, command: list[str], env: Optional[dict[str, str]] = None) -> None:
        self.command = command
        self.env = env
        self._process: Optional[asyncio.subprocess.Process] = None

    async def connect(self) -> None:
        import os as _os

        proc_env = {**_os.environ}
        if self.env:
            proc_env.update(self.env)

        self._process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=proc_env,
        )
        logger.debug("StdioTransport: subprocess started (pid=%s)", self._process.pid)

    async def send(self, message: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("StdioTransport: not connected")
        data = json.dumps(message).encode() + b"\n"
        self._process.stdin.write(data)
        await self._process.stdin.drain()

    async def receive(self) -> dict[str, Any]:
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("StdioTransport: not connected")
        line = await self._process.stdout.readline()
        if not line:
            raise EOFError("StdioTransport: subprocess closed stdout")
        return json.loads(line.decode().strip())

    async def close(self) -> None:
        if self._process is not None:
            try:
                if self._process.stdin:
                    self._process.stdin.close()
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except Exception:
                pass
            self._process = None
            logger.debug("StdioTransport: subprocess terminated")


# ---------------------------------------------------------------------------
# SSE transport
# ---------------------------------------------------------------------------


class SSETransport:
    """Connect to an MCP server over Server-Sent Events (GET) + HTTP POST.

    Reads SSE events from a GET stream; sends messages via HTTP POST.
    Requires ``httpx`` which is already a dashboard dependency.
    """

    def __init__(self, url: str, headers: Optional[dict[str, str]] = None) -> None:
        self.url = url.rstrip("/")
        self.headers = headers or {}
        self._client: Any = None  # httpx.AsyncClient
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task[None]] = None

    async def connect(self) -> None:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("SSETransport requires httpx (pip install httpx)") from exc

        self._client = httpx.AsyncClient(headers=self.headers, timeout=None)
        self._reader_task = asyncio.create_task(self._sse_reader())
        logger.debug("SSETransport: connected to %s", self.url)

    async def _sse_reader(self) -> None:
        """Background task that reads SSE events and enqueues parsed JSON."""
        try:
            async with self._client.stream("GET", f"{self.url}/sse") as response:
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        payload = line[5:].strip()
                        if payload:
                            try:
                                msg = json.loads(payload)
                                await self._queue.put(msg)
                            except json.JSONDecodeError:
                                logger.warning("SSETransport: invalid JSON in SSE: %s", payload)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("SSETransport: reader error: %s", exc)

    async def send(self, message: dict[str, Any]) -> None:
        if self._client is None:
            raise RuntimeError("SSETransport: not connected")
        response = await self._client.post(
            f"{self.url}/message",
            json=message,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()

    async def receive(self) -> dict[str, Any]:
        return await self._queue.get()

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.debug("SSETransport: closed")


# ---------------------------------------------------------------------------
# MCP client
# ---------------------------------------------------------------------------


class MCPClient:
    """JSON-RPC 2.0 client for a single MCP server.

    Wraps a transport and provides high-level methods that map to MCP
    protocol methods (initialize, tools/list, tools/call, resources/list,
    resources/read).
    """

    def __init__(self, name: str, transport: MCPTransport) -> None:
        self.name = name
        self._transport = transport
        self._next_id = 1

    # ------------------------------------------------------------------
    # Internal JSON-RPC helpers
    # ------------------------------------------------------------------

    def _make_request(self, method: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
        }
        if params is not None:
            msg["params"] = params
        self._next_id += 1
        return msg

    async def _call(self, method: str, params: Optional[dict[str, Any]] = None) -> Any:
        request = self._make_request(method, params)
        await self._transport.send(request)
        response = await self._transport.receive()
        if "error" in response:
            error = response["error"]
            code = error.get("code", -1) if isinstance(error, dict) else -1
            msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            raise RuntimeError(f"MCP error {code}: {msg}")
        return response.get("result")

    # ------------------------------------------------------------------
    # MCP protocol methods
    # ------------------------------------------------------------------

    async def initialize(self) -> ServerCapabilities:
        """Send the MCP initialize handshake and return server capabilities."""
        result = await self._call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "agent-orchestrator", "version": "0.1.0"},
                "capabilities": {},
            },
        )
        if result is None:
            return ServerCapabilities()
        caps_raw = result.get("capabilities", {}) if isinstance(result, dict) else {}
        return ServerCapabilities(
            tools="tools" in caps_raw,
            resources="resources" in caps_raw,
            raw=caps_raw,
        )

    async def list_tools(self) -> list[MCPTool]:
        """Retrieve the list of tools exposed by the server."""
        result = await self._call("tools/list")
        tools_raw = result.get("tools", []) if isinstance(result, dict) else []
        tools: list[MCPTool] = []
        for t in tools_raw:
            tools.append(
                MCPTool(
                    name=t.get("name", ""),
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", t.get("input_schema", {})),
                    server_name=self.name,
                )
            )
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a tool on the server and return the raw result."""
        result = await self._call("tools/call", {"name": name, "arguments": arguments})
        # MCP servers may wrap the output in {"content": [...]}
        if isinstance(result, dict) and "content" in result:
            content = result["content"]
            if isinstance(content, list) and len(content) == 1:
                item = content[0]
                if isinstance(item, dict) and "text" in item:
                    return item["text"]
            return content
        return result

    async def list_resources(self) -> list[MCPResource]:
        """Retrieve the list of resources exposed by the server."""
        result = await self._call("resources/list")
        resources_raw = result.get("resources", []) if isinstance(result, dict) else []
        resources: list[MCPResource] = []
        for r in resources_raw:
            resources.append(
                MCPResource(
                    uri=r.get("uri", ""),
                    name=r.get("name", ""),
                    description=r.get("description", ""),
                    mime_type=r.get("mimeType", r.get("mime_type", "text/plain")),
                    server_name=self.name,
                )
            )
        return resources

    async def read_resource(self, uri: str) -> str:
        """Read the content of a resource identified by URI."""
        result = await self._call("resources/read", {"uri": uri})
        if isinstance(result, dict):
            # MCP servers return {"contents": [{"text": "..."}]}
            contents = result.get("contents", [])
            if isinstance(contents, list) and contents:
                first = contents[0]
                if isinstance(first, dict):
                    return first.get("text", str(first))
            return result.get("text", str(result))
        return str(result) if result is not None else ""

    async def close(self) -> None:
        """Shut down the transport."""
        await self._transport.close()


# ---------------------------------------------------------------------------
# MCP client manager
# ---------------------------------------------------------------------------


class MCPClientManager:
    """Manages connections to multiple external MCP servers.

    Each server is identified by a unique name. The manager handles
    connecting, caching tool/resource lists, and routing tool calls.
    """

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._tools: dict[str, list[MCPTool]] = {}  # server_name -> tools

    async def add_server(self, name: str, config: MCPServerConfig) -> None:
        """Connect to an external MCP server and cache its tool list.

        Raises ValueError when the config is invalid.
        Raises RuntimeError when the server cannot be reached.
        """
        config.validate()

        transport: MCPTransport
        if config.transport == "stdio":
            transport = StdioTransport(command=config.command or [], env=config.env)
        else:
            transport = SSETransport(url=config.url or "", headers=config.headers)

        await transport.connect()
        client = MCPClient(name=name, transport=transport)

        try:
            await client.initialize()
            tools = await client.list_tools()
        except Exception:
            await transport.close()
            raise

        self._clients[name] = client
        self._tools[name] = tools
        logger.info("MCPClientManager: connected to %s (%d tools)", name, len(tools))

    async def remove_server(self, name: str) -> None:
        """Disconnect from a server and remove it from the registry."""
        client = self._clients.pop(name, None)
        self._tools.pop(name, None)
        if client is not None:
            await client.close()
            logger.info("MCPClientManager: disconnected from %s", name)

    def get_all_tools(self) -> list[MCPTool]:
        """Return all tools from all connected servers.

        Each tool name is prefixed with the server name (``{server}/{tool}``).
        """
        aggregated: list[MCPTool] = []
        for server_name, tools in self._tools.items():
            for tool in tools:
                aggregated.append(
                    MCPTool(
                        name=f"{server_name}/{tool.name}",
                        description=tool.description,
                        input_schema=tool.input_schema,
                        server_name=server_name,
                    )
                )
        return aggregated

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a tool on a specific server.

        Raises KeyError when the server is not connected.
        """
        client = self._clients.get(server_name)
        if client is None:
            raise KeyError(f"MCP server not connected: {server_name!r}")
        return await client.call_tool(tool_name, arguments)

    def list_servers(self) -> list[str]:
        """Return names of all currently connected servers."""
        return list(self._clients.keys())
