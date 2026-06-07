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
import secrets
from typing import Any, Awaitable, Callable, Protocol

from ..core.skill import Skill, SkillResult
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
from .signing import compute_signature, new_nonce, verify_signature

logger = logging.getLogger(__name__)


DEFAULT_TOOL_TTL_SECONDS = 60.0
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

    def __init__(self, *, ttl_seconds: float = DEFAULT_TOOL_TTL_SECONDS) -> None:
        self._futures: dict[str, asyncio.Future[ToolResult]] = {}
        self._nonces: dict[str, str] = {}
        self._names: dict[str, str] = {}
        self._ttl = ttl_seconds

    def _new_id(self) -> str:
        # 16 hex chars is enough collision-resistance for a per-connection
        # registry (the secret-key signature is the actual integrity guard).
        return secrets.token_hex(8)

    @property
    def in_flight(self) -> int:
        return len(self._futures)

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
            run_id=run_id, tool_call_id=tool_call_id, nonce=nonce, name=name
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
        except asyncio.TimeoutError:
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


PromptHandler = Callable[[str, list[RemoteSkillAdapter], str], Awaitable[None]]


async def perform_handshake(
    ws: WebSocketLike,
    *,
    run_id_factory: Callable[[], str] = lambda: secrets.token_hex(8),
) -> tuple[Hello, str]:
    """Read HELLO, validate version, send ACK. Return ``(hello, run_id)``.

    Raises :class:`AgentHostError` on protocol violation; the caller emits
    an :class:`agent_host.Error` frame and closes the WS.
    """
    try:
        raw = await asyncio.wait_for(ws.receive_json(), timeout=HANDSHAKE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as e:
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
    ack = Ack(
        run_id=run_id,
        agent=frame.agent,
        model=frame.model,
        provider=frame.provider,
        capabilities=list(frame.tool_manifest),
    )
    await ws.send_json(ack.to_dict())
    return frame, run_id


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
    agent for a turn (commit #3 will wire it to ``agent_runner.run_agent``).
    Leaving it ``None`` makes the loop "echo-only" — useful for the unit
    tests that only exercise frame routing and signature verification.
    """
    while True:
        try:
            raw = await ws.receive_json()
        except (asyncio.CancelledError, RuntimeError):  # noqa: PERF203
            return "closed_by_peer"
        try:
            frame = parse_frame(raw)
        except UnknownFrameError:
            await _emit_error(ws, code="malformed_frame", message="unknown frame kind")
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
            # Chunked streaming lands in commit #4. For now we accept the
            # frame shape but no-op so a forward-compat client doesn't
            # see the connection drop just because it tried to stream.
            logger.debug(
                "agent-host: tool_chunk seq=%s id=%s eof=%s (no-op until commit #4)",
                frame.seq,
                frame.tool_call_id,
                frame.eof,
            )
        elif isinstance(frame, Prompt):
            if on_prompt is None:
                continue
            await on_prompt(frame.text, [], run_id)  # adapters wired in commit #3
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
) -> str:
    """End-to-end driver: handshake then drive_session.

    Wraps :func:`perform_handshake` and :func:`drive_session` so the route
    handler in ``dashboard.cli_routes`` is a 5-line shim. Returns the same
    reason string as :func:`drive_session`; the route handler logs it.

    Catches :class:`AgentHostError` at the handshake step, emits the
    typed Error frame, and closes the WS with code 1008 (policy violation)
    — same as the rest of the dashboard for auth/policy errors.
    """
    registry = registry or PendingToolCallsRegistry()
    try:
        hello, run_id = await perform_handshake(ws, run_id_factory=run_id_factory)
    except AgentHostError as exc:
        await _emit_error(ws, code=exc.code, message=exc.message)
        await ws.close(code=1008, reason=exc.code)
        return f"protocol_error:{exc.code}"

    return await drive_session(
        ws,
        hello=hello,
        run_id=run_id,
        registry=registry,
        on_prompt=on_prompt,
    )
