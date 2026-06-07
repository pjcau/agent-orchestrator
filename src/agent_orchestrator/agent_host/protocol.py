"""Wire protocol for the agent-host channel.

Every frame is a JSON object with a ``kind`` discriminator and an immutable
``frame_id`` (UUID hex). Subclass payloads are additive so the schema can
grow without breaking older peers; an unknown ``kind`` is rejected by
``parse_frame`` so peers fail loudly rather than silently dropping frames.

The frame catalogue mirrors the agent-loop state machine:

| Direction        | Kind            | Purpose                                  |
|------------------|-----------------|------------------------------------------|
| client → server  | ``hello``       | Open the session, declare cwd + manifest |
| server → client  | ``ack``         | Confirm pairing, assign ``run_id``       |
| client → server  | ``prompt``      | User turn input                          |
| server → client  | ``tool_call``   | Server asks the client to run a tool     |
| client → server  | ``tool_result`` | Client returns the tool outcome          |
| client → server  | ``tool_chunk``  | Streamed tool output (commit #4)         |
| either           | ``cancel``      | Abort an in-flight tool call             |
| server → client  | ``assistant_text`` | Streamed LLM tokens                   |
| server → client  | ``turn_end``    | Server-side turn complete                |
| either           | ``error``       | Hard failure with a typed ``code``       |

The pattern (frozen dataclass + ``from_dict`` / ``to_dict``) matches
``core.cooperation_messages`` so the project keeps a single message-style
convention. Signing of ``tool_call`` / ``tool_result`` / ``tool_chunk`` is
delegated to ``agent_host.signing`` — the schema only carries ``nonce`` and
``signature`` strings, never computes them.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, fields
from typing import Any, ClassVar


PROTOCOL_VERSION = 1

KIND_HELLO = "hello"
KIND_ACK = "ack"
KIND_PROMPT = "prompt"
KIND_TOOL_CALL = "tool_call"
KIND_TOOL_RESULT = "tool_result"
KIND_TOOL_CHUNK = "tool_chunk"
KIND_CANCEL = "cancel"
KIND_ASSISTANT_TEXT = "assistant_text"
KIND_TURN_END = "turn_end"
KIND_ERROR = "error"

ALL_KINDS: tuple[str, ...] = (
    KIND_HELLO,
    KIND_ACK,
    KIND_PROMPT,
    KIND_TOOL_CALL,
    KIND_TOOL_RESULT,
    KIND_TOOL_CHUNK,
    KIND_CANCEL,
    KIND_ASSISTANT_TEXT,
    KIND_TURN_END,
    KIND_ERROR,
)


class UnknownFrameError(ValueError):
    """Raised by ``parse_frame`` when ``kind`` is missing or unknown.

    Fail-loud by design: silently dropping unknown frames hides protocol
    drift between client and server and turns into impossible-to-debug
    intermittent freezes during a long chat session.
    """


def _new_frame_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class Frame:
    """Common header for every agent-host frame.

    Subclasses MUST override ``kind`` (class variable) and may add payload
    fields. The header is stable across protocol versions; payloads are
    additive — unknown payload fields are dropped tolerantly by ``from_dict``
    so a v1 peer keeps working when a v2 peer adds optional fields.
    """

    kind: ClassVar[str] = "frame"

    frame_id: str = field(default_factory=_new_frame_id)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "kind": self.kind,
            "frame_id": self.frame_id,
            "timestamp": self.timestamp,
        }
        base = {"frame_id", "timestamp"}
        for f in fields(self):
            if f.name in base:
                continue
            out[f.name] = getattr(self, f.name)
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Frame":
        own = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in d.items() if k in own}
        return cls(**kwargs)


@dataclass(frozen=True)
class Hello(Frame):
    """Client → server. First frame on every connection.

    ``version`` lets the server reject incompatible peers explicitly (a
    server-side handler emits ``Error(code="version_unsupported")`` instead
    of guessing).  ``cwd`` is informational — the client enforces the path
    sandbox locally, the server never trusts the value.  ``tool_manifest``
    is the list of tool names the client can execute; the server treats it
    as the closed set of callable tools for the run.
    """

    kind: ClassVar[str] = KIND_HELLO

    version: int = PROTOCOL_VERSION
    cwd: str = ""
    tool_manifest: list[str] = field(default_factory=list)
    stream_caps: list[str] = field(default_factory=list)
    agent: str = ""
    model: str = ""
    provider: str = ""


@dataclass(frozen=True)
class Ack(Frame):
    """Server → client. Confirms pairing and announces the assigned run_id.

    ``run_id`` is the server-side identifier used in every subsequent signed
    frame. ``capabilities`` lets the server advertise what it understands of
    the client's manifest (so the client can disable a tool path if the
    server says "I can't see this tool in any agent on this server").

    ``signing_key`` is the per-session HMAC secret as a hex string
    (32 bytes = 64 hex chars), minted by the server on accept. Both
    peers use it for ``tool_call`` / ``tool_result`` / ``tool_chunk``
    HMAC sign+verify. The dashboard's stable ``JWT_SECRET_KEY`` is
    *never* shipped to the client — see ``agent_host.signing`` for the
    threat model.
    """

    kind: ClassVar[str] = KIND_ACK

    run_id: str = ""
    agent: str = ""
    model: str = ""
    provider: str = ""
    capabilities: list[str] = field(default_factory=list)
    signing_key: str = ""


@dataclass(frozen=True)
class Prompt(Frame):
    """Client → server. The user turn input."""

    kind: ClassVar[str] = KIND_PROMPT

    text: str = ""


@dataclass(frozen=True)
class ToolCall(Frame):
    """Server → client. Server asks the client to execute a tool.

    ``nonce`` is a server-generated random value that the client MUST echo
    verbatim in the matching ``ToolResult`` / ``ToolChunk``. ``signature``
    is the HMAC over ``(run_id, tool_call_id, nonce, name)`` — see
    ``agent_host.signing``. Replay is naturally blocked because every
    ``tool_call_id`` is single-use on the server's pending-calls registry.
    """

    kind: ClassVar[str] = KIND_TOOL_CALL

    tool_call_id: str = ""
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    nonce: str = ""
    signature: str = ""


@dataclass(frozen=True)
class ToolResult(Frame):
    """Client → server. Final outcome of a delegated tool call.

    ``status`` is ``"ok"`` or ``"error"``.  ``output`` is JSON-serialisable
    (string for shell stdout, dict for structured tools). The client echoes
    the ``nonce`` it received in the matching ``ToolCall`` and signs the
    same tuple — the server verifies before resolving the pending Future.
    """

    kind: ClassVar[str] = KIND_TOOL_RESULT

    tool_call_id: str = ""
    status: str = "ok"
    output: Any = None
    error_code: str = ""
    nonce: str = ""
    signature: str = ""


@dataclass(frozen=True)
class ToolChunk(Frame):
    """Client → server. Streamed tool output, ordered by ``seq``.

    ``eof=True`` marks the last chunk; the server then waits for the final
    ``ToolResult`` carrying the status. Used by long shell commands and
    large file reads (commit #4).
    """

    kind: ClassVar[str] = KIND_TOOL_CHUNK

    tool_call_id: str = ""
    seq: int = 0
    chunk: str = ""
    eof: bool = False
    nonce: str = ""
    signature: str = ""


@dataclass(frozen=True)
class Cancel(Frame):
    """Either direction. Aborts an in-flight tool call.

    Sent by the server when a tool exceeds its TTL or by the client on
    Ctrl-C. ``reason`` is a short human-readable string for the log; the
    receiver MUST stop work on ``tool_call_id`` and free associated
    resources.
    """

    kind: ClassVar[str] = KIND_CANCEL

    tool_call_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class AssistantText(Frame):
    """Server → client. Streamed LLM tokens for the active turn."""

    kind: ClassVar[str] = KIND_ASSISTANT_TEXT

    chunk: str = ""


@dataclass(frozen=True)
class TurnEnd(Frame):
    """Server → client. The server-side turn finished.

    ``status`` mirrors ``ok`` / ``error`` / ``cancelled`` for the whole
    turn (not a single tool call). ``step_count`` is how many agent steps
    the server ran for the turn — useful for budget reporting in the CLI
    REPL footer.
    """

    kind: ClassVar[str] = KIND_TURN_END

    status: str = "ok"
    step_count: int = 0


@dataclass(frozen=True)
class Error(Frame):
    """Either direction. Hard failure with a typed ``code``.

    Codes are stable strings (e.g. ``version_unsupported``,
    ``signature_invalid``, ``tool_timeout``, ``manifest_rejected``); the
    string ``message`` is human-readable. Receiving ``Error`` after the
    handshake means the session is dead — the peer should close the WS
    and surface the code to the user.
    """

    kind: ClassVar[str] = KIND_ERROR

    code: str = ""
    message: str = ""


_KIND_MAP: dict[str, type[Frame]] = {
    KIND_HELLO: Hello,
    KIND_ACK: Ack,
    KIND_PROMPT: Prompt,
    KIND_TOOL_CALL: ToolCall,
    KIND_TOOL_RESULT: ToolResult,
    KIND_TOOL_CHUNK: ToolChunk,
    KIND_CANCEL: Cancel,
    KIND_ASSISTANT_TEXT: AssistantText,
    KIND_TURN_END: TurnEnd,
    KIND_ERROR: Error,
}


def parse_frame(d: dict[str, Any]) -> Frame:
    """Dispatch a raw dict on its ``kind`` field.

    Raises ``UnknownFrameError`` if ``kind`` is missing or not in the
    catalogue. Unknown payload fields are dropped tolerantly so the parser
    accepts forward-compatible frames; the *kind discriminator* is the
    only hard boundary.
    """
    kind = d.get("kind")
    if not kind or kind not in _KIND_MAP:
        raise UnknownFrameError(f"unknown or missing frame kind: {kind!r}")
    return _KIND_MAP[kind].from_dict(d)
