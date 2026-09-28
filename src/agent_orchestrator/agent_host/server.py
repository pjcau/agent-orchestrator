"""Server-side machinery for the agent-host channel.

Three pieces, kept transport-agnostic on purpose so the unit tests can
drive them with an in-memory fake WS:

* :class:`PendingToolCallsRegistry` — per-session map of
  ``tool_call_id → asyncio.Future``. Issues fresh nonces, signs the
  outgoing :class:`agent_host.ToolCall`, awaits the matching result with
  a TTL, and rejects stale resolutions.
* :class:`RemoteSkillAdapter` — adapts the existing ``core.skill.Skill``
  ABC to a registry-backed proxy. The agent-loop calls ``execute(params)``
  exactly as it would a local skill; the adapter ships the call through
  the WS and blocks on the registry until the client answers.
* :func:`serve_agent_host` — the connection driver. Handles the HELLO/ACK
  handshake, parses incoming frames, verifies signatures on every
  client→server ``tool_result`` / ``tool_chunk``, resolves the registry,
  and emits typed :class:`agent_host.Error` frames on hard failures.

The route stub that wires this into FastAPI lives in
``dashboard.cli_routes`` so the import boundary stays clean
(``agent_host`` does **not** depend on ``dashboard``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from ..core.skill import Skill, SkillRegistry, SkillResult
from .protocol import (
    PROTOCOL_VERSION,
    Ack,
    Cancel,
    Error,
    Hello,
    Prompt,
    ToolCall,
    ToolChunk,
    ToolResult,
    UnknownFrameError,
    parse_frame,
)
from .signing import compute_signature, new_nonce, new_session_key, verify_signature

logger = logging.getLogger(__name__)


def _tool_ttl_from_env(default: float = 300.0) -> float:
    """Resolve the per-tool result TTL (seconds) from the environment.

    The TTL clock starts when the server sends a ``ToolCall`` and runs
    until the client returns the matching ``ToolResult``. Crucially that
    window includes any **human-in-the-loop confirmation** the client
    shows (e.g. ``allow `ls`? [y/N]``). The old 60 s default was shorter
    than a user typically takes to read and answer such a prompt, so the
    call timed out mid-confirmation and the connection was torn down with
    a ``Broken pipe`` / ``peer closed connection`` error. The default is
    now generous (5 min) and overridable via
    ``AGENT_HOST_TOOL_TTL_SECONDS`` so operators can tune it.
    """
    raw = os.environ.get("AGENT_HOST_TOOL_TTL_SECONDS")
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "agent-host: invalid AGENT_HOST_TOOL_TTL_SECONDS=%r, using %.0fs",
            raw,
            default,
        )
        return default
    if value <= 0:
        logger.warning(
            "agent-host: AGENT_HOST_TOOL_TTL_SECONDS must be > 0, using %.0fs",
            default,
        )
        return default
    return value


DEFAULT_TOOL_TTL_SECONDS = _tool_ttl_from_env()
HANDSHAKE_TIMEOUT_SECONDS = 10.0


class WebSocketLike(Protocol):
    """Structural type for the WS surface this module consumes.

    Keeps ``server.py`` test-friendly: the production endpoint passes a
    Starlette ``WebSocket``; the test passes an in-memory fake with the
    same three methods.
    """

    async def send_json(self, data: dict) -> None: ...
    async def receive_json(self) -> dict: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...


class AgentHostError(Exception):
    """Raised internally when a peer violates the protocol.

    Carries a stable :attr:`code` that becomes the ``code`` field of the
    :class:`agent_host.Error` frame sent to the peer before close.
    """

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Pending tool calls
# ---------------------------------------------------------------------------


class PendingToolCallsRegistry:
    """Per-WS registry of in-flight tool calls keyed by ``tool_call_id``.

    Each :meth:`issue` mints a fresh ``tool_call_id`` + ``nonce``, signs
    the call with :func:`agent_host.signing.compute_signature`, sends it
    on ``ws``, and awaits the matching :class:`ToolResult` with a TTL.

    Replay protection comes "for free" from the dict semantics: once a
    result lands and the Future resolves, the id is removed. A second
    ``tool_result`` for the same id is silently dropped — see
    :meth:`resolve`.

    Cancellation (commit #4) and chunked streaming are layered on top
    via the ``_chunks`` dict; the registry itself only owns the *final*
    result Future.
    """

    #: Hard upper bound on accumulated streamed bytes per tool_call. Beyond
    #: this the server drops further chunks and force-fails the call.
    #: 10 MB matches the per-call ceiling documented in the threat model.
    MAX_STREAM_BYTES: int = 10 * 1024 * 1024

    #: Maximum number of concurrent streaming tool_calls *per run*. A noisy
    #: client cannot exhaust server memory by opening many streams.
    MAX_CONCURRENT_STREAMS: int = 4

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TOOL_TTL_SECONDS,
        signing_key: bytes | None = None,
    ) -> None:
        self._futures: dict[str, asyncio.Future[ToolResult]] = {}
        self._nonces: dict[str, str] = {}
        self._names: dict[str, str] = {}
        self._chunks: dict[str, list[str]] = {}
        self._chunk_seq: dict[str, int] = {}
        self._chunk_bytes: dict[str, int] = {}
        self._streaming_ids: set[str] = set()
        self._ttl = ttl_seconds
        # When ``signing_key`` is None we fall back to JWT_SECRET_KEY,
        # which is the right behaviour for unit tests that don't set up
        # a handshake. Production always supplies an explicit key.
        self._signing_key = signing_key

    def _new_id(self) -> str:
        # 16 hex chars is enough collision-resistance for a per-connection
        # registry (the secret-key signature is the actual integrity guard).
        return secrets.token_hex(8)

    @property
    def in_flight(self) -> int:
        return len(self._futures)

    def accumulated_chunks(self, tool_call_id: str) -> list[str]:
        """Streamed chunks for ``tool_call_id`` in arrival order.

        Read by tests and (commit #5) by the metrics emitter to record
        how much streaming a tool actually did.
        """
        return list(self._chunks.get(tool_call_id, []))

    async def issue(
        self,
        *,
        ws: WebSocketLike,
        run_id: str,
        name: str,
        args: dict[str, Any],
    ) -> ToolResult:
        """Send a TOOL_CALL on ``ws`` and await the matching TOOL_RESULT.

        Raises :class:`asyncio.TimeoutError` after :attr:`ttl_seconds`;
        the caller is expected to translate it into a :class:`SkillResult`
        with ``success=False`` (see :class:`RemoteSkillAdapter`).
        """
        tool_call_id = self._new_id()
        nonce = new_nonce()
        signature = compute_signature(
            run_id=run_id,
            tool_call_id=tool_call_id,
            nonce=nonce,
            name=name,
            key=self._signing_key,
        )
        frame = ToolCall(
            tool_call_id=tool_call_id,
            name=name,
            args=args,
            nonce=nonce,
            signature=signature,
        )
        loop = asyncio.get_event_loop()
        future: asyncio.Future[ToolResult] = loop.create_future()
        self._futures[tool_call_id] = future
        self._nonces[tool_call_id] = nonce
        self._names[tool_call_id] = name
        try:
            await ws.send_json(frame.to_dict())
            return await asyncio.wait_for(future, timeout=self._ttl)
        finally:
            # On timeout / cancellation, free the slot so a stuck client
            # never holds the slot forever — even if it later replies the
            # result lands on the missing-id branch and is dropped.
            self._futures.pop(tool_call_id, None)
            self._nonces.pop(tool_call_id, None)
            self._names.pop(tool_call_id, None)
            self._chunks.pop(tool_call_id, None)
            self._chunk_seq.pop(tool_call_id, None)
            self._chunk_bytes.pop(tool_call_id, None)
            self._streaming_ids.discard(tool_call_id)

    def accept_chunk(
        self,
        *,
        run_id: str,
        chunk: ToolChunk,
    ) -> str | None:
        """Verify and accumulate one streamed ``tool_chunk`` frame.

        Returns ``None`` on success, an :class:`agent_host.Error.code`
        otherwise. Guarantees:

        * The id must be in-flight (``unknown_tool_call_id`` if not).
        * Signature + nonce verified using the original call's binding
          (same triple as :meth:`resolve`).
        * Strict monotonic ``seq`` — out-of-order chunks are dropped
          (``chunk_out_of_order``) so a buggy or malicious client
          cannot corrupt the buffer with replays.
        * Per-call ``MAX_STREAM_BYTES`` and per-run
          ``MAX_CONCURRENT_STREAMS`` caps (``chunk_too_large`` /
          ``too_many_streams``) — DoS protection.
        """
        nonce_expected = self._nonces.get(chunk.tool_call_id)
        name = self._names.get(chunk.tool_call_id, "")
        if nonce_expected is None:
            return "unknown_tool_call_id"
        if chunk.nonce != nonce_expected:
            return "nonce_mismatch"
        if not verify_signature(
            run_id=run_id,
            tool_call_id=chunk.tool_call_id,
            nonce=nonce_expected,
            name=name,
            signature=chunk.signature,
            key=self._signing_key,
        ):
            return "signature_invalid"

        if chunk.tool_call_id not in self._streaming_ids:
            if len(self._streaming_ids) >= self.MAX_CONCURRENT_STREAMS:
                return "too_many_streams"
            self._streaming_ids.add(chunk.tool_call_id)

        expected_seq = self._chunk_seq.get(chunk.tool_call_id, 0)
        if chunk.seq != expected_seq:
            return "chunk_out_of_order"
        self._chunk_seq[chunk.tool_call_id] = expected_seq + 1

        total = self._chunk_bytes.get(chunk.tool_call_id, 0) + len(chunk.chunk)
        if total > self.MAX_STREAM_BYTES:
            return "chunk_too_large"
        self._chunk_bytes[chunk.tool_call_id] = total
        self._chunks.setdefault(chunk.tool_call_id, []).append(chunk.chunk)
        return None

    async def emit_cancel(
        self,
        *,
        ws: WebSocketLike,
        tool_call_id: str,
        reason: str = "server_cancel",
    ) -> None:
        """Send a CANCEL frame for an in-flight tool_call.

        The client is expected to stop work on the call and reply with a
        :class:`agent_host.ToolResult` carrying ``status="error"``,
        ``error_code="cancelled"``. The registry slot is reclaimed by
        the existing ``finally:`` in :meth:`issue` once the result lands
        or the TTL expires.
        """
        if tool_call_id not in self._futures:
            return
        await ws.send_json(Cancel(tool_call_id=tool_call_id, reason=reason).to_dict())

    def resolve(
        self,
        *,
        run_id: str,
        result: ToolResult,
    ) -> str | None:
        """Verify ``result`` and resolve the matching Future.

        Returns ``None`` on success; an :class:`agent_host.Error.code` on
        rejection so the caller can decide whether to send an Error
        frame or just drop the result.

        Rejected results never resolve the Future: a misbehaving client
        cannot resolve someone else's call by guessing the id. Replays
        land on the missing-id branch (``unknown_tool_call_id``) and
        signature failures on ``signature_invalid``.
        """
        future = self._futures.get(result.tool_call_id)
        nonce_expected = self._nonces.get(result.tool_call_id)
        name = self._names.get(result.tool_call_id, "")
        if future is None or nonce_expected is None:
            return "unknown_tool_call_id"
        if result.nonce != nonce_expected:
            return "nonce_mismatch"
        if not verify_signature(
            run_id=run_id,
            tool_call_id=result.tool_call_id,
            nonce=nonce_expected,
            name=name,
            signature=result.signature,
            key=self._signing_key,
        ):
            return "signature_invalid"
        if not future.done():
            future.set_result(result)
        return None


# ---------------------------------------------------------------------------
# Skill adapter
# ---------------------------------------------------------------------------


class RemoteSkillAdapter(Skill):
    """Adapts a single client-side tool name to the :class:`Skill` ABC.

    The agent loop sees this as an ordinary skill; ``execute`` proxies to
    the connected client via the :class:`PendingToolCallsRegistry`. A
    timeout translates to ``SkillResult(success=False, error="tool_timeout")``
    so the agent step closes cleanly with an actionable message in the
    LLM context.

    One adapter per tool name in the client's HELLO manifest. The
    parameters schema is supplied by the caller — typically the same
    schema the *local* skill of the same name exposes, so the agent's
    prompt and tool-list look identical regardless of where the tool
    actually runs.
    """

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
        registry: PendingToolCallsRegistry,
        ws: WebSocketLike,
        run_id: str,
        category: str = "general",
    ) -> None:
        self._name = name
        self._description = description
        self._parameters = parameters
        self._category = category
        self._registry = registry
        self._ws = ws
        self._run_id = run_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict:
        return self._parameters

    @property
    def category(self) -> str:
        return self._category

    async def execute(self, params: dict) -> SkillResult:
        try:
            result = await self._registry.issue(
                ws=self._ws, run_id=self._run_id, name=self._name, args=params
            )
        except TimeoutError:
            return SkillResult(
                success=False,
                output=None,
                error="tool_timeout",
                metadata={"tool": self._name, "delegated": True},
            )
        except AgentHostError as exc:
            return SkillResult(
                success=False,
                output=None,
                error=exc.code,
                metadata={"tool": self._name, "delegated": True},
            )
        return SkillResult(
            success=result.status == "ok",
            output=result.output,
            error=result.error_code or None if result.status != "ok" else None,
            metadata={"tool": self._name, "delegated": True},
        )


# ---------------------------------------------------------------------------
# Connection driver
# ---------------------------------------------------------------------------


PromptHandler = Callable[[str, "SkillRegistry", str, Hello], Awaitable[None]]
"""Async callable ``(prompt_text, skill_registry, run_id, hello) -> None``.

The handler owns: running the agent, streaming ``AssistantText`` /
``TurnEnd`` back over the WS (closure-captured), and any error reporting.
``serve_agent_host`` only routes frames — it intentionally does NOT
import :mod:`dashboard.agent_runner` so the import boundary holds.
"""


#: Minimal stand-in schema for tools whose real signature isn't known
#: at handshake time. Each agent's prompt-time tool list will be enriched
#: by the orchestrator's registry; the dispatcher only needs *some* JSON
#: Schema to satisfy the agent loop's tool-listing step.
_GENERIC_TOOL_PARAMS: dict[str, Any] = {"type": "object", "additionalProperties": True}


#: Canonical JSON Schemas for the project's standard client-side tools.
#: Without these the LLM sees only ``additionalProperties: true`` and
#: hallucinates parameter names (``path`` instead of ``file_path``,
#: ``cmd`` instead of ``argv``, …) — a tool call that then fails on
#: the client with ``KeyError``. Mirroring the schemas published by
#: :mod:`agent_orchestrator.skills.filesystem` and :mod:`...skills.shell`
#: keeps the agent's tool list identical regardless of where the tool
#: actually runs.
_DEFAULT_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "file_read": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "Path to the file to read, relative to the workspace root. "
                    "Absolute paths outside the workspace are refused with "
                    "path_outside_workspace."
                ),
            },
        },
        "required": ["file_path"],
    },
    "file_write": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to write (relative to the workspace). Parents auto-created.",
            },
            "content": {
                "type": "string",
                "description": "Text content to write. Overwrites if the file already exists.",
            },
        },
        "required": ["file_path", "content"],
    },
    "shell_exec": {
        "type": "object",
        "properties": {
            "argv": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    'Command + arguments as a list (e.g. ["pytest", "-q"]). '
                    "String form is rejected (shell=True would be unsafe). The "
                    "binary at argv[0] must be allow-listed in "
                    "~/.cache/ago/shell-allow.json."
                ),
            },
        },
        "required": ["argv"],
    },
    "file_list": {
        "type": "object",
        "properties": {
            "directory": {
                "type": "string",
                "description": "Directory path relative to the workspace (default: workspace root).",
            },
        },
    },
}


_DEFAULT_TOOL_DESCRIPTIONS: dict[str, str] = {
    "file_read": "Read the contents of a file inside the agent-host workspace.",
    "file_write": "Write content to a file inside the agent-host workspace (parents auto-created).",
    "shell_exec": "Execute a command (argv list) inside the agent-host workspace.",
    "file_list": "List entries in a workspace-relative directory.",
}


def build_remote_registry(
    *,
    hello: Hello,
    registry: PendingToolCallsRegistry,
    ws: WebSocketLike,
    run_id: str,
    parameter_schemas: dict[str, dict[str, Any]] | None = None,
    descriptions: dict[str, str] | None = None,
) -> SkillRegistry:
    """Construct a :class:`SkillRegistry` of :class:`RemoteSkillAdapter`s.

    One adapter per tool name in ``hello.tool_manifest``; every adapter
    shares the same pending-calls registry + WS + run_id. Callers can
    supply ``parameter_schemas`` / ``descriptions`` keyed by tool name to
    enrich the registry with project-specific tool metadata (typically
    looked up from the dashboard's existing skill registry).

    Returned registry is ready to be passed to
    ``agent_runner.run_agent(skill_registry_override=...)`` once that
    parameter lands (next commit). Keeping this helper here means the
    server-side WS endpoint never re-implements the adapter-loop logic.
    """
    skills = SkillRegistry()
    parameter_schemas = parameter_schemas or {}
    descriptions = descriptions or {}
    for name in hello.tool_manifest:
        # Resolution order: explicit override > project canonical schema
        # for the standard tools > permissive generic. Same order for
        # descriptions. This stops the LLM from inventing parameter
        # names (which used to crash the client with a KeyError) by
        # always handing it a precise JSON Schema for the well-known
        # tool catalogue.
        params = (
            parameter_schemas.get(name) or _DEFAULT_TOOL_SCHEMAS.get(name) or _GENERIC_TOOL_PARAMS
        )
        desc = (
            descriptions.get(name)
            or _DEFAULT_TOOL_DESCRIPTIONS.get(name)
            or f"client-side {name} delegated via agent-host"
        )
        adapter = RemoteSkillAdapter(
            name=name,
            description=desc,
            parameters=params,
            registry=registry,
            ws=ws,
            run_id=run_id,
        )
        skills.register(adapter)
    return skills


async def perform_handshake(
    ws: WebSocketLike,
    *,
    run_id_factory: Callable[[], str] = lambda: secrets.token_hex(8),
    session_key_factory: Callable[[], bytes] = new_session_key,
) -> tuple[Hello, str, bytes]:
    """Read HELLO, validate version, send ACK. Return ``(hello, run_id, key)``.

    Mints a fresh 32-byte session signing key, ships it inside the ACK
    frame (hex-encoded) so the client can verify subsequent ``tool_call``
    frames and sign its own ``tool_result`` / ``tool_chunk`` replies, and
    returns the raw bytes so the caller can hand them to
    :class:`PendingToolCallsRegistry`.

    Raises :class:`AgentHostError` on protocol violation; the caller emits
    an :class:`agent_host.Error` frame and closes the WS.
    """
    try:
        raw = await asyncio.wait_for(ws.receive_json(), timeout=HANDSHAKE_TIMEOUT_SECONDS)
    except TimeoutError as e:
        raise AgentHostError("handshake_timeout", "no HELLO within timeout") from e

    try:
        frame = parse_frame(raw)
    except UnknownFrameError as e:
        raise AgentHostError("malformed_frame", str(e)) from e

    if not isinstance(frame, Hello):
        raise AgentHostError(
            "handshake_expected_hello", f"first frame must be HELLO, got {frame.kind}"
        )

    if frame.version != PROTOCOL_VERSION:
        raise AgentHostError(
            "version_unsupported",
            f"protocol version {frame.version} not supported (need {PROTOCOL_VERSION})",
        )

    if not frame.tool_manifest:
        raise AgentHostError(
            "manifest_rejected",
            "tool_manifest must declare at least one tool to be useful",
        )

    run_id = run_id_factory()
    session_key = session_key_factory()
    ack = Ack(
        run_id=run_id,
        agent=frame.agent,
        model=frame.model,
        provider=frame.provider,
        capabilities=list(frame.tool_manifest),
        signing_key=session_key.hex(),
    )
    await ws.send_json(ack.to_dict())
    return frame, run_id, session_key


async def drive_session(
    ws: WebSocketLike,
    *,
    hello: Hello,
    run_id: str,
    registry: PendingToolCallsRegistry,
    on_prompt: PromptHandler | None = None,
) -> str:
    """Main read loop until the peer closes or a fatal error occurs.

    Returns a stable string reason describing why the loop exited:
    ``"closed_by_peer"`` on graceful disconnect, ``"protocol_error:<code>"``
    on a fatal :class:`AgentHostError`.

    ``on_prompt`` is the hook the dashboard wires to actually run the
    agent for a turn. Leaving it ``None`` makes the loop "echo-only" —
    useful for the unit tests that only exercise frame routing and
    signature verification. The handler receives a fresh
    :class:`SkillRegistry` of :class:`RemoteSkillAdapter` instances
    built once after handshake, so calling ``run_agent`` from inside
    the handler is a single call away.
    """
    skills = build_remote_registry(hello=hello, registry=registry, ws=ws, run_id=run_id)

    # Turns run as concurrent tasks, NOT inline. Awaiting ``on_prompt``
    # here would deadlock: a turn that delegates a tool blocks on a
    # ``ToolResult`` future that only THIS receive loop can resolve, so
    # blocking the loop inside the turn means the result never arrives and
    # the tool call hangs until its TTL expires. Spawning keeps the loop
    # free to receive ``ToolResult`` / ``ToolChunk`` frames mid-turn.
    turn_tasks: set[asyncio.Task] = set()

    def _spawn_turn(text: str) -> None:
        task = asyncio.create_task(on_prompt(text, skills, run_id, hello))
        turn_tasks.add(task)

        def _on_done(t: asyncio.Task) -> None:
            turn_tasks.discard(t)
            if not t.cancelled():
                exc = t.exception()
                if exc is not None:
                    logger.error("agent-host: turn task crashed: %s", exc, exc_info=exc)

        task.add_done_callback(_on_done)

    def _cleanup() -> None:
        for t in list(turn_tasks):
            t.cancel()

    while True:
        try:
            raw = await ws.receive_json()
        except (asyncio.CancelledError, RuntimeError):
            _cleanup()
            return "closed_by_peer"
        try:
            frame = parse_frame(raw)
        except UnknownFrameError:
            await _emit_error(ws, code="malformed_frame", message="unknown frame kind")
            _cleanup()
            return "protocol_error:malformed_frame"

        if isinstance(frame, ToolResult):
            err = registry.resolve(run_id=run_id, result=frame)
            if err:
                logger.warning(
                    "agent-host: dropped tool_result id=%s reason=%s",
                    frame.tool_call_id,
                    err,
                )
                # Soft drop — do not close the WS, just ignore. Closing
                # on a forged result would let an attacker DoS a real
                # session by injecting one bogus frame.
        elif isinstance(frame, ToolChunk):
            err = registry.accept_chunk(run_id=run_id, chunk=frame)
            if err:
                logger.warning(
                    "agent-host: dropped tool_chunk id=%s seq=%s reason=%s",
                    frame.tool_call_id,
                    frame.seq,
                    err,
                )
        elif isinstance(frame, Prompt):
            if on_prompt is None:
                continue
            # Spawn — do NOT await — so the loop keeps resolving the tool
            # results this turn's tool calls block on (see above).
            _spawn_turn(frame.text)
        elif isinstance(frame, Cancel):
            logger.info(
                "agent-host: cancel id=%s reason=%s (cancellation lands in commit #4)",
                frame.tool_call_id,
                frame.reason,
            )
        else:
            # Client should never send Ack/AssistantText/TurnEnd. Drop quietly.
            logger.debug("agent-host: unexpected frame kind=%s from client", frame.kind)


async def _emit_error(ws: WebSocketLike, *, code: str, message: str) -> None:
    try:
        await ws.send_json(Error(code=code, message=message).to_dict())
    except Exception as exc:  # noqa: BLE001
        logger.debug("agent-host: failed to emit error frame: %s", exc)


async def serve_agent_host(
    ws: WebSocketLike,
    *,
    on_prompt: PromptHandler | None = None,
    registry: PendingToolCallsRegistry | None = None,
    run_id_factory: Callable[[], str] = lambda: secrets.token_hex(8),
    metrics: object | None = None,
) -> str:
    """End-to-end driver: handshake then drive_session.

    Wraps :func:`perform_handshake` and :func:`drive_session` so the route
    handler in ``dashboard.cli_routes`` is a 5-line shim. Returns the same
    reason string as :func:`drive_session`; the route handler logs it.

    Catches :class:`AgentHostError` at the handshake step, emits the
    typed Error frame, and closes the WS with code 1008 (policy violation)
    — same as the rest of the dashboard for auth/policy errors.

    ``metrics`` is an opaque handle from :func:`agent_host.telemetry.bind`
    (``None`` if telemetry is disabled in this deployment). Emitted from
    here so every reason path is accounted for.
    """
    # Defer registry creation until after the handshake so we can hand it
    # the per-session signing key minted in `perform_handshake`. Callers
    # supplying their own registry (mostly tests) bypass that path.
    try:
        hello, run_id, session_key = await perform_handshake(ws, run_id_factory=run_id_factory)
    except AgentHostError as exc:
        await _emit_error(ws, code=exc.code, message=exc.message)
        await ws.close(code=1008, reason=exc.code)
        reason = f"protocol_error:{exc.code}"
        _bump_disconnect(metrics, reason)
        return reason

    if registry is None:
        registry = PendingToolCallsRegistry(signing_key=session_key)

    reason = await drive_session(
        ws,
        hello=hello,
        run_id=run_id,
        registry=registry,
        on_prompt=on_prompt,
    )
    _bump_disconnect(metrics, reason)
    return reason


def _bump_disconnect(metrics: object | None, reason: str) -> None:
    if metrics is None:
        return
    counter = getattr(metrics, "disconnect_total", None)
    if counter is None:
        return
    try:
        counter.inc(labels={"reason": reason})
    except TypeError:
        # ``inc()`` may accept a positional value, label-less form on
        # some backends — best-effort fallback so a stricter signature
        # never crashes the WS.
        counter.inc(1)
