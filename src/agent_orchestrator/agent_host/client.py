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
    ToolResult,
    TurnEnd,
    UnknownFrameError,
    parse_frame,
)
from .shell_allowlist import ConfirmCallback, ShellAllowlist
from .signing import compute_signature, verify_signature

logger = logging.getLogger(__name__)


SHELL_DEFAULT_TIMEOUT = 60.0
SHELL_OUTPUT_CAP = 1_000_000  # 1 MB cap; streaming larger output is commit #4


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

    async def run(self, name: str, args: dict[str, Any]) -> SkillResult:
        try:
            if name == "file_read":
                return await self._do_file_read(args)
            if name == "file_write":
                return await self._do_file_write(args)
            if name == "shell_exec":
                return await self._do_shell_exec(args)
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

    async def _do_shell_exec(self, args: dict[str, Any]) -> SkillResult:
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
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(), timeout=self._shell_timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return SkillResult(
                success=False,
                output=None,
                error="shell_timeout",
                metadata={"argv0": argv[0], "timeout_s": self._shell_timeout},
            )
        stdout = out.decode("utf-8", errors="replace")[:SHELL_OUTPUT_CAP]
        stderr = err.decode("utf-8", errors="replace")[:SHELL_OUTPUT_CAP]
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
    """What the client learned during HELLO/ACK."""

    run_id: str
    agent: str = ""
    model: str = ""
    provider: str = ""
    capabilities: tuple[str, ...] = ()


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
    ) -> None:
        self._ws = ws
        self._runner = runner
        self._on_tool_call = on_tool_call
        self._session: SessionInfo | None = None
        self._events: asyncio.Queue[ServerEvent] = asyncio.Queue()
        self._receive_task: asyncio.Task | None = None
        self._closed = False

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
        self._session = SessionInfo(
            run_id=frame.run_id,
            agent=frame.agent,
            model=frame.model,
            provider=frame.provider,
            capabilities=tuple(frame.capabilities),
        )
        self._receive_task = asyncio.create_task(self._receive_loop())
        return self._session

    async def send_prompt(self, text: str) -> None:
        await self._ws.send_json(Prompt(text=text).to_dict())

    async def send_cancel(self, tool_call_id: str, reason: str = "user_ctrl_c") -> None:
        await self._ws.send_json(
            Cancel(tool_call_id=tool_call_id, reason=reason).to_dict()
        )

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
                    await self._handle_tool_call(run_id, frame)
                elif isinstance(frame, AssistantText | TurnEnd | Error):
                    await self._events.put(frame)
                    if isinstance(frame, Error):
                        return
                else:
                    logger.debug(
                        "agent-host client: ignoring frame kind=%s", frame.kind
                    )
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent-host client: receive loop exited: %s", exc)

    async def _handle_tool_call(self, run_id: str, frame: ToolCall) -> None:
        if not verify_signature(
            run_id=run_id,
            tool_call_id=frame.tool_call_id,
            nonce=frame.nonce,
            name=frame.name,
            signature=frame.signature,
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
        local = await self._runner.run(frame.name, frame.args)
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
            run_id=run_id, tool_call_id=tool_call_id, nonce=nonce, name=name
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
