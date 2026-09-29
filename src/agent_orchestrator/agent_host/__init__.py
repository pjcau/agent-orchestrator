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

from .client import (
    AgentHostClient,
    LocalToolRunner,
    ServerEvent,
    SessionInfo,
    ToolProgress,
)
from .path_sandbox import PathOutsideWorkspaceError, enforce_workspace
from .protocol import (
    KIND_ACK,
    KIND_ASSISTANT_TEXT,
    KIND_CANCEL,
    KIND_ERROR,
    KIND_HELLO,
    KIND_PROMPT,
    KIND_STEP,
    KIND_TOOL_CALL,
    KIND_TOOL_CHUNK,
    KIND_TOOL_RESULT,
    KIND_TURN_END,
    PROTOCOL_VERSION,
    Ack,
    AssistantText,
    Cancel,
    Error,
    Frame,
    Hello,
    Prompt,
    Step,
    ToolCall,
    ToolChunk,
    ToolResult,
    TurnEnd,
    UnknownFrameError,
    parse_frame,
)
from .server import (
    DEFAULT_TOOL_TTL_SECONDS,
    HANDSHAKE_TIMEOUT_SECONDS,
    AgentHostError,
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
    "DEFAULT_TOOL_TTL_SECONDS",
    "HANDSHAKE_TIMEOUT_SECONDS",
    "KIND_ACK",
    "KIND_ASSISTANT_TEXT",
    "KIND_CANCEL",
    "KIND_ERROR",
    "KIND_HELLO",
    "KIND_PROMPT",
    "KIND_STEP",
    "KIND_TOOL_CALL",
    "KIND_TOOL_CHUNK",
    "KIND_TOOL_RESULT",
    "KIND_TURN_END",
    "PROTOCOL_VERSION",
    "Ack",
    "AgentHostClient",
    "AgentHostError",
    "AssistantText",
    "Cancel",
    "ConfirmCallback",
    "Error",
    "Frame",
    "Hello",
    "LocalToolRunner",
    "PathOutsideWorkspaceError",
    "PendingToolCallsRegistry",
    "Prompt",
    "PromptHandler",
    "RemoteSkillAdapter",
    "ServerEvent",
    "SessionInfo",
    "ShellAllowlist",
    "ShellAllowlistError",
    "SigningKeyMissingError",
    "Step",
    "ToolCall",
    "ToolChunk",
    "ToolProgress",
    "ToolResult",
    "TurnEnd",
    "UnknownFrameError",
    "WebSocketLike",
    "build_remote_registry",
    "compute_signature",
    "drive_session",
    "enforce_workspace",
    "is_high_risk",
    "new_nonce",
    "parse_frame",
    "perform_handshake",
    "serve_agent_host",
    "verify_signature",
]
