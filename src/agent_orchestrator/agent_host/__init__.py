"""Agent-host — client-side tool delegation for remote agent loops.

The agent-host protocol lets the agent loop run on a remote dashboard
(`agents-orchestrator.com`) while every tool call (file_read, file_write,
shell_exec, …) executes in the CLI's local environment. This gives `ago chat`
feature parity with `ago run --local` for the user's filesystem and shell,
without giving up multi-turn server-side conversation state.

The wire protocol is a single WebSocket per chat session at
``/api/cli/v1/agent-host``, full-duplex, framed by JSON dicts with a ``kind``
discriminator. See ``protocol.py`` for the catalogue, ``signing.py`` for the
HMAC scheme that ties every TOOL_RESULT to the matching server-issued
TOOL_CALL.

This package contains **only schemas and signing** at this commit. The
WebSocket endpoint (server) and the Python client subprocess landed in
subsequent commits — see ``feat/agent-host-protocol`` branch history.
"""

from __future__ import annotations

from .protocol import (
    PROTOCOL_VERSION,
    Ack,
    AssistantText,
    Cancel,
    Error,
    Frame,
    Hello,
    Prompt,
    ToolCall,
    ToolChunk,
    ToolResult,
    TurnEnd,
    parse_frame,
    UnknownFrameError,
    KIND_ACK,
    KIND_ASSISTANT_TEXT,
    KIND_CANCEL,
    KIND_ERROR,
    KIND_HELLO,
    KIND_PROMPT,
    KIND_TOOL_CALL,
    KIND_TOOL_CHUNK,
    KIND_TOOL_RESULT,
    KIND_TURN_END,
)
from .client import (
    AgentHostClient,
    LocalToolRunner,
    ServerEvent,
    SessionInfo,
    ToolProgress,
)
from .path_sandbox import PathOutsideWorkspaceError, enforce_workspace
from .server import (
    AgentHostError,
    DEFAULT_TOOL_TTL_SECONDS,
    HANDSHAKE_TIMEOUT_SECONDS,
    PendingToolCallsRegistry,
    PromptHandler,
    RemoteSkillAdapter,
    WebSocketLike,
    build_remote_registry,
    drive_session,
    perform_handshake,
    serve_agent_host,
)
from .shell_allowlist import ConfirmCallback, ShellAllowlist, ShellAllowlistError, is_high_risk
from .signing import (
    SigningKeyMissingError,
    compute_signature,
    new_nonce,
    verify_signature,
)

__all__ = [
    "PROTOCOL_VERSION",
    "DEFAULT_TOOL_TTL_SECONDS",
    "HANDSHAKE_TIMEOUT_SECONDS",
    "AgentHostError",
    "PendingToolCallsRegistry",
    "PromptHandler",
    "RemoteSkillAdapter",
    "WebSocketLike",
    "drive_session",
    "perform_handshake",
    "serve_agent_host",
    "build_remote_registry",
    "AgentHostClient",
    "ToolProgress",
    "LocalToolRunner",
    "ServerEvent",
    "SessionInfo",
    "PathOutsideWorkspaceError",
    "enforce_workspace",
    "ShellAllowlist",
    "ShellAllowlistError",
    "ConfirmCallback",
    "is_high_risk",
    "Frame",
    "Hello",
    "Ack",
    "Prompt",
    "ToolCall",
    "ToolResult",
    "ToolChunk",
    "Cancel",
    "AssistantText",
    "TurnEnd",
    "Error",
    "parse_frame",
    "UnknownFrameError",
    "KIND_HELLO",
    "KIND_ACK",
    "KIND_PROMPT",
    "KIND_TOOL_CALL",
    "KIND_TOOL_RESULT",
    "KIND_TOOL_CHUNK",
    "KIND_CANCEL",
    "KIND_ASSISTANT_TEXT",
    "KIND_TURN_END",
    "KIND_ERROR",
    "compute_signature",
    "verify_signature",
    "new_nonce",
    "SigningKeyMissingError",
]
