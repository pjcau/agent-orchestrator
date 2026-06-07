"""Agent-host client — Python implementation invoked by the Rust CLI.

The Rust ``ago`` binary spawns this module as a subprocess on
``ago chat --client-tools`` (and ``ago run --client-tools``). It opens a
single WebSocket to the dashboard's ``/api/cli/v1/agent-host`` endpoint,
sends a HELLO with the local workspace + tool manifest, and then runs
two cooperating loops:

* a *receive* loop that reads server frames and dispatches them:
  - :class:`agent_host.AssistantText` and :class:`agent_host.TurnEnd`
    bubble up to the caller (printed by the Rust UI or by the local REPL);
  - :class:`agent_host.ToolCall` is verified, executed by a local
    :class:`LocalToolRunner`, and answered with a signed
    :class:`agent_host.ToolResult`;
  - :class:`agent_host.Error` triggers a clean disconnect.
* a *send* loop that the embedder pushes :class:`agent_host.Prompt`
  frames onto for each user turn.

No tool execution logic lives here — the runner delegates to the
existing :mod:`agent_orchestrator.skills` package so the file/shell
behaviour stays single-source. The client only adds the
agent-host-specific guards (strict workspace sandbox, shell allowlist,
HMAC verify-on-receive / sign-on-send).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..core.skill import SkillResult
from ..skills.filesystem import FileReadSkill, FileWriteSkill
from .path_sandbox import PathOutsideWorkspaceError, enforce_workspace
from .protocol import (
    PROTOCOL_VERSION,
    Ack,
    AssistantText,
    Cancel,
    Error,
    Hello,
    Prompt,
    ToolCall,
    ToolChunk,
    ToolResult,
    TurnEnd,
    UnknownFrameError,
    parse_frame,
)
from .shell_allowlist import ConfirmCallback, ShellAllowlist
from .signing import compute_signature, verify_signature

logger = logging.getLogger(__name__)


def _suppress():
    """Context manager that swallows asyncio.CancelledError + generic exc.

    Used in finally-blocks during cancel/timeout reaping where we want to
    keep going regardless of why a sub-task ended.
    """
    return contextlib.suppress(Exception, asyncio.CancelledError)


SHELL_DEFAULT_TIMEOUT = 60.0
SHELL_OUTPUT_CAP = 10 * 1024 * 1024  # 10 MB hard cap aligned with server registry
SHELL_CHUNK_BYTES = 4096  # streamed when an emitter is provided


class _WebSocketLike(Protocol):
    async def send_json(self, data: dict) -> None: ...
    async def receive_json(self) -> dict: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...


# ---------------------------------------------------------------------------
# Local tool runner
# ---------------------------------------------------------------------------


@dataclass
class ClientToolError:
    """Internal carrier for typed errors returned from the runner."""

    code: str
    message: str = ""


class LocalToolRunner:
    """Executes a single tool call locally.

    Wraps the existing project skills (:class:`FileReadSkill`,
    :class:`FileWriteSkill`) — no duplicate filesystem logic. Shell exec
    is built on ``asyncio.create_subprocess_exec`` (argv list, never
    ``shell=True``) and gated by :class:`ShellAllowlist`.

    The runner is stateless across tool calls; it owns only the workspace
    root and the shell allowlist.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        allowlist: ShellAllowlist | None = None,
        confirm_shell: ConfirmCallback | None = None,
        shell_timeout: float = SHELL_DEFAULT_TIMEOUT,
    ) -> None:
        self._workspace = workspace.resolve()
        self._file_read = FileReadSkill(working_directory=self._workspace)
        self._file_write = FileWriteSkill(working_directory=self._workspace)
        self._allowlist = allowlist or ShellAllowlist()
        self._confirm = confirm_shell
        self._shell_timeout = shell_timeout

    async def run(
        self,
        name: str,
        args: dict[str, Any],
        *,
        emit_chunk: Callable[[str], Awaitable[None]] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> SkillResult:
        """Execute one tool.

        ``emit_chunk`` and ``cancel_event`` are optional. When the caller
        provides them, ``shell_exec`` streams stdout incrementally and
        respects the cancel event by SIGKILLing the subprocess. Other
        tools currently ignore the streaming hooks (their outputs fit in
        a single response); they still honour ``cancel_event`` for
        symmetry.
        """
        try:
            if name == "file_read":
                return await self._do_file_read(args)
            if name == "file_write":
                return await self._do_file_write(args)
            if name == "shell_exec":
                return await self._do_shell_exec(
                    args, emit_chunk=emit_chunk, cancel_event=cancel_event
                )
            return SkillResult(
                success=False,
                output=None,
                error="unknown_tool",
                metadata={"tool": name},
            )
        except PathOutsideWorkspaceError as exc:
            return SkillResult(
                success=False,
                output=None,
                error="path_outside_workspace",
                metadata={"tool": name, "detail": str(exc)},
            )

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def manifest(self) -> list[str]:
        """Names exposed to the server's HELLO ack."""
        return ["file_read", "file_write", "shell_exec"]

    async def _do_file_read(self, args: dict[str, Any]) -> SkillResult:
        # Enforce the strict sandbox before delegating to the skill (which
        # applies the permissive ``_confine``). Strict-first ensures we
        # *reject* escapes rather than silently remapping them.
        enforce_workspace(self._workspace, args.get("file_path", ""))
        return await self._file_read.execute(args)

    async def _do_file_write(self, args: dict[str, Any]) -> SkillResult:
        enforce_workspace(self._workspace, args.get("file_path", ""))
        return await self._file_write.execute(args)

    async def _do_shell_exec(
        self,
        args: dict[str, Any],
        *,
        emit_chunk: Callable[[str], Awaitable[None]] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> SkillResult:
        argv = args.get("argv") or []
        if isinstance(argv, str):
            return SkillResult(
                success=False,
                output=None,
                error="shell_requires_argv_list",
                metadata={
                    "detail": (
                        "shell_exec via agent-host expects argv as a list "
                        "(security: shell=True is refused). Got a string."
                    )
                },
            )
        if not argv:
            return SkillResult(
                success=False,
                output=None,
                error="shell_empty_argv",
            )
        if self._confirm is None:
            # No interactive confirm available — only allow already-known
            # commands. Refuse first-time invocations rather than allow.
            if not self._allowlist.contains(argv):
                return SkillResult(
                    success=False,
                    output=None,
                    error="shell_denied",
                    metadata={"detail": "non-interactive client; binary not pre-allowed"},
                )
        else:
            allowed = await self._allowlist.gate(argv, confirm=self._confirm)
            if not allowed:
                return SkillResult(
                    success=False,
                    output=None,
                    error="shell_denied",
                )
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(self._workspace),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "AGENT_HOST": "1"},
        )

        # Two code paths share the per-call timeout + cancel handling:
        #   buffered: legacy (no emit_chunk), returns aggregated output
        #   streaming: emit_chunk is awaited per 4 KB chunk
        async def _drain(stream, sink: bytearray, *, label: str) -> None:
            total = 0
            while True:
                buf = await stream.read(SHELL_CHUNK_BYTES)
                if not buf:
                    return
                total += len(buf)
                if total > SHELL_OUTPUT_CAP:
                    # Cap reached — stop reading so we don't blow up memory.
                    # The subprocess may still be writing; we'll kill below.
                    return
                sink.extend(buf)
                if emit_chunk is not None:
                    await emit_chunk(buf.decode("utf-8", errors="replace"))

        out_buf = bytearray()
        err_buf = bytearray()

        async def _drain_streams() -> None:
            await asyncio.gather(
                _drain(proc.stdout, out_buf, label="stdout"),
                _drain(proc.stderr, err_buf, label="stderr"),
            )

        async def _watch_cancel() -> None:
            if cancel_event is None:
                # Never resolves; gather() finishes on the others.
                await asyncio.Event().wait()
            else:
                await cancel_event.wait()

        drain_task = asyncio.create_task(_drain_streams())
        cancel_task = asyncio.create_task(_watch_cancel())
        wait_task = asyncio.create_task(proc.wait())

        try:
            done, _ = await asyncio.wait(
                {drain_task, cancel_task, wait_task},
                timeout=self._shell_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            cancelled_via_event = cancel_task in done and not cancel_task.cancelled()
            timed_out = not done
            if cancelled_via_event or timed_out:
                proc.kill()
                # Reap the dead process and drain anything queued.
                with _suppress():
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                with _suppress():
                    await asyncio.wait_for(drain_task, timeout=2.0)
                error = "shell_cancelled" if cancelled_via_event else "shell_timeout"
                return SkillResult(
                    success=False,
                    output={
                        "stdout": out_buf.decode("utf-8", errors="replace"),
                        "stderr": err_buf.decode("utf-8", errors="replace"),
                        "returncode": -1,
                    },
                    error=error,
                    metadata={"argv0": argv[0]},
                )
            # Either drain or wait completed first; finish both so we have
            # the final returncode and the complete buffers before reporting.
            await drain_task
            await wait_task
        finally:
            for t in (cancel_task,):
                if not t.done():
                    t.cancel()
                    with _suppress():
                        await t

        stdout = out_buf.decode("utf-8", errors="replace")
        stderr = err_buf.decode("utf-8", errors="replace")
        return SkillResult(
            success=proc.returncode == 0,
            output={"stdout": stdout, "stderr": stderr, "returncode": proc.returncode},
            error=None if proc.returncode == 0 else "shell_nonzero_exit",
            metadata={"argv0": argv[0]},
        )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@dataclass
class SessionInfo:
    """What the client learned during HELLO/ACK.

    ``signing_key`` is the raw bytes the server minted for this session
    (decoded from the hex string the ACK ships). Kept in-memory only;
    never persisted to disk.
    """

    run_id: str
    agent: str = ""
    model: str = ""
    provider: str = ""
    capabilities: tuple[str, ...] = ()
    signing_key: bytes = b""


ServerEvent = AssistantText | TurnEnd | Error
"""Frames the client emits up to the embedder for display / control."""


class AgentHostClient:
    """Single-connection client.

    Lifecycle:

        client = AgentHostClient(ws, runner)
        info = await client.handshake(agent=…, model=…, provider=…)
        async for event in client.events():
            ...
            await client.send_prompt("hi")
    """

    def __init__(
        self,
        ws: _WebSocketLike,
        runner: LocalToolRunner,
        *,
        on_tool_call: Callable[[ToolCall, SkillResult], Awaitable[None]] | None = None,
        stream_shell: bool = True,
    ) -> None:
        self._ws = ws
        self._runner = runner
        self._on_tool_call = on_tool_call
        self._stream_shell = stream_shell
        self._session: SessionInfo | None = None
        self._events: asyncio.Queue[ServerEvent] = asyncio.Queue()
        self._receive_task: asyncio.Task | None = None
        self._closed = False
        # Cancel events keyed by tool_call_id — set when the server sends
        # a CANCEL frame for an in-flight call. The runner inspects it
        # mid-call and short-circuits with a `shell_cancelled` result.
        self._cancel_events: dict[str, asyncio.Event] = {}
        # Tool-call dispatch tasks kept alive so the receive loop can read
        # the next frame while the previous tool is still running.
        self._tool_tasks: set[asyncio.Task] = set()

    @property
    def session(self) -> SessionInfo:
        if self._session is None:
            raise RuntimeError("call handshake() first")
        return self._session

    async def handshake(
        self,
        *,
        agent: str = "",
        model: str = "",
        provider: str = "",
    ) -> SessionInfo:
        hello = Hello(
            version=PROTOCOL_VERSION,
            cwd=str(self._runner.workspace),
            tool_manifest=self._runner.manifest,
            stream_caps=[],  # commit #4 will add tool_chunk
            agent=agent,
            model=model,
            provider=provider,
        )
        await self._ws.send_json(hello.to_dict())
        raw = await self._ws.receive_json()
        try:
            frame = parse_frame(raw)
        except UnknownFrameError as e:
            raise RuntimeError(f"server returned non-frame on handshake: {raw}") from e
        if isinstance(frame, Error):
            raise RuntimeError(f"server rejected HELLO: {frame.code} — {frame.message}")
        if not isinstance(frame, Ack):
            raise RuntimeError(f"server returned unexpected kind on handshake: {frame.kind}")
        try:
            session_key = bytes.fromhex(frame.signing_key) if frame.signing_key else b""
        except ValueError as exc:
            raise RuntimeError(f"server returned non-hex signing_key in ACK: {exc}") from exc
        self._session = SessionInfo(
            run_id=frame.run_id,
            agent=frame.agent,
            model=frame.model,
            provider=frame.provider,
            capabilities=tuple(frame.capabilities),
            signing_key=session_key,
        )
        self._receive_task = asyncio.create_task(self._receive_loop())
        return self._session

    async def send_prompt(self, text: str) -> None:
        await self._ws.send_json(Prompt(text=text).to_dict())

    async def send_cancel(self, tool_call_id: str, reason: str = "user_ctrl_c") -> None:
        await self._ws.send_json(Cancel(tool_call_id=tool_call_id, reason=reason).to_dict())

    async def events(self) -> AsyncIterator[ServerEvent]:
        """Async iterator of server-side events the embedder should react to.

        The iterator ends after a :class:`agent_host.Error` or when the
        receive loop sees the WS close.
        """
        while True:
            event = await self._events.get()
            yield event
            if isinstance(event, Error):
                return

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
        # Wait briefly for in-flight tool tasks so kills propagate before
        # the event loop tears down — keeps the resource warning clean.
        for task in list(self._tool_tasks):
            if not task.done():
                task.cancel()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await task
        try:
            await self._ws.close(code=1000, reason="client_done")
        except Exception:  # noqa: BLE001
            pass

    async def _receive_loop(self) -> None:
        assert self._session is not None  # noqa: S101
        run_id = self._session.run_id
        try:
            while not self._closed:
                raw = await self._ws.receive_json()
                try:
                    frame = parse_frame(raw)
                except UnknownFrameError:
                    logger.warning("agent-host client: dropping unknown frame: %s", raw)
                    continue

                if isinstance(frame, ToolCall):
                    # Dispatch in a background task so the receive loop keeps
                    # reading subsequent frames (especially the matching CANCEL).
                    # Without this, a long-running shell pins the loop and
                    # cancellation never lands.
                    task = asyncio.create_task(self._handle_tool_call(run_id, frame))
                    self._tool_tasks.add(task)
                    task.add_done_callback(self._tool_tasks.discard)
                elif isinstance(frame, Cancel):
                    ev = self._cancel_events.get(frame.tool_call_id)
                    if ev and not ev.is_set():
                        logger.info(
                            "agent-host client: server cancel id=%s reason=%s",
                            frame.tool_call_id,
                            frame.reason,
                        )
                        ev.set()
                elif isinstance(frame, AssistantText | TurnEnd | Error):
                    await self._events.put(frame)
                    if isinstance(frame, Error):
                        return
                else:
                    logger.debug("agent-host client: ignoring frame kind=%s", frame.kind)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent-host client: receive loop exited: %s", exc)

    async def _handle_tool_call(self, run_id: str, frame: ToolCall) -> None:
        session_key = self.session.signing_key or None
        if not verify_signature(
            run_id=run_id,
            tool_call_id=frame.tool_call_id,
            nonce=frame.nonce,
            name=frame.name,
            signature=frame.signature,
            key=session_key,
        ):
            logger.warning(
                "agent-host client: tool_call signature invalid id=%s",
                frame.tool_call_id,
            )
            await self._send_tool_result(
                run_id=run_id,
                tool_call_id=frame.tool_call_id,
                nonce=frame.nonce,
                name=frame.name,
                result=SkillResult(
                    success=False,
                    output=None,
                    error="signature_invalid",
                ),
            )
            return

        # Register a cancel event so a server-side CANCEL during the call
        # short-circuits the runner.
        cancel_event = asyncio.Event()
        self._cancel_events[frame.tool_call_id] = cancel_event

        # Streaming emitter — only enabled for shell_exec by default. We
        # send a `seq`-monotonic ToolChunk per 4 KB and let the server
        # accumulate.
        emit_chunk = None
        if self._stream_shell and frame.name == "shell_exec":
            seq_counter = [0]

            async def emit_chunk(chunk: str) -> None:  # noqa: E306
                seq = seq_counter[0]
                seq_counter[0] += 1
                tc_sig = compute_signature(
                    run_id=run_id,
                    tool_call_id=frame.tool_call_id,
                    nonce=frame.nonce,
                    name=frame.name,
                    key=session_key,
                )
                tc = ToolChunk(
                    tool_call_id=frame.tool_call_id,
                    seq=seq,
                    chunk=chunk,
                    eof=False,
                    nonce=frame.nonce,
                    signature=tc_sig,
                )
                await self._ws.send_json(tc.to_dict())

        try:
            local = await self._runner.run(
                frame.name,
                frame.args,
                emit_chunk=emit_chunk,
                cancel_event=cancel_event,
            )
        finally:
            self._cancel_events.pop(frame.tool_call_id, None)

        if self._on_tool_call:
            await self._on_tool_call(frame, local)
        await self._send_tool_result(
            run_id=run_id,
            tool_call_id=frame.tool_call_id,
            nonce=frame.nonce,
            name=frame.name,
            result=local,
        )

    async def _send_tool_result(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        nonce: str,
        name: str,
        result: SkillResult,
    ) -> None:
        sig = compute_signature(
            run_id=run_id,
            tool_call_id=tool_call_id,
            nonce=nonce,
            name=name,
            key=(self.session.signing_key or None),
        )
        frame = ToolResult(
            tool_call_id=tool_call_id,
            status="ok" if result.success else "error",
            output=result.output,
            error_code=result.error or "",
            nonce=nonce,
            signature=sig,
        )
        await self._ws.send_json(frame.to_dict())
