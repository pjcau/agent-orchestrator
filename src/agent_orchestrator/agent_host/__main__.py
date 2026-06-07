"""``python -m agent_orchestrator.agent_host`` entrypoint.

Launched by the Rust ``ago`` CLI when the user passes ``--client-tools``.
Opens the WebSocket, runs the REPL loop, prints assistant text to stdout
and reads user prompts from stdin. The Rust binary stays the front-end
(it owns the launcher / login / `ago config` UX); this entrypoint owns
the WebSocket and the local tool execution.

CLI:

    python -m agent_orchestrator.agent_host \
        --server https://agents-orchestrator.com \
        --token <jwt>                         \
        --cwd /home/user/proj                 \
        --agent team-lead                     \
        --model tencent/hy3-preview           \
        --provider openrouter

Reads ``JWT_SECRET_KEY`` from the env for HMAC. Failure modes return a
non-zero exit code so the Rust launcher can surface a clean error.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("agent_host.cli")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m agent_orchestrator.agent_host")
    p.add_argument("--server", required=True, help="Dashboard base URL")
    p.add_argument(
        "--token",
        default=os.environ.get("AGO_API_KEY", ""),
        help="JWT (defaults to AGO_API_KEY env var)",
    )
    p.add_argument(
        "--cwd",
        default=str(Path.cwd()),
        help="Workspace root for local tools (default: cwd)",
    )
    p.add_argument("--agent", default="")
    p.add_argument("--model", default="")
    p.add_argument("--provider", default="")
    p.add_argument("--log-level", default="WARNING", help="DEBUG|INFO|WARNING|ERROR")
    return p.parse_args(argv)


async def _confirm_shell(binary: str, high_risk: bool) -> bool:
    """Sync stdin prompt — agent-host REPL is single-user.

    The REPL is single-user and single-threaded, so blocking stdin while
    waiting for the answer is fine. Future work: when the Rust front-end
    owns the UI, this hook will instead emit a structured event the Rust
    launcher renders.
    """
    label = " (HIGH RISK: full shell access)" if high_risk else ""
    sys.stderr.write(f"\n[agent-host] allow `{binary}` for this session?{label} [y/N] ")
    sys.stderr.flush()
    loop = asyncio.get_event_loop()
    answer = await loop.run_in_executor(None, sys.stdin.readline)
    return answer.strip().lower() in {"y", "yes"}


async def _main(args: argparse.Namespace) -> int:
    # Import here so ``--help`` doesn't pay the websockets import cost.
    try:
        import websockets
    except ImportError:
        sys.stderr.write(
            "agent-host requires the `websockets` package. "
            "Install with: pip install 'agent-orchestrator[harness]'\n"
        )
        return 2

    from .client import AgentHostClient, LocalToolRunner

    workspace = Path(args.cwd).resolve()
    if not workspace.is_dir():
        sys.stderr.write(f"workspace not a directory: {workspace}\n")
        return 2

    ws_url = (
        args.server.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
        + "/api/cli/v1/agent-host"
    )

    headers = [("X-API-Key", args.token)] if args.token else []
    try:
        ws = await websockets.connect(ws_url, additional_headers=headers)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"connection failed: {exc}\n")
        return 3

    # websockets.WebSocketClientProtocol uses send/recv text; wrap so it
    # matches the protocol expected by AgentHostClient (send_json/recv_json).
    import json as _json

    class _Adapter:
        def __init__(self, inner):
            self.inner = inner

        async def send_json(self, data):
            await self.inner.send(_json.dumps(data))

        async def receive_json(self):
            raw = await self.inner.recv()
            return _json.loads(raw)

        async def close(self, code=1000, reason=""):
            await self.inner.close(code=code, reason=reason)

    runner = LocalToolRunner(workspace=workspace, confirm_shell=_confirm_shell)
    client = AgentHostClient(_Adapter(ws), runner)
    try:
        info = await client.handshake(agent=args.agent, model=args.model, provider=args.provider)
        sys.stderr.write(
            f"[agent-host] connected run_id={info.run_id} "
            f"agent={info.agent or '-'} model={info.model or '-'}\n"
        )

        from .client import ToolProgress
        from .protocol import AssistantText, Error as ErrorFrame, TurnEnd

        # ANSI sequences kept simple so non-TTY consumers (CI, |less) still
        # render readable lines after stripping. Override with NO_COLOR=1.
        use_color = sys.stderr.isatty() and not os.environ.get("NO_COLOR")
        DIM = "\x1b[2m" if use_color else ""
        GREEN = "\x1b[32m" if use_color else ""
        RED = "\x1b[31m" if use_color else ""
        BOLD = "\x1b[1m" if use_color else ""
        RESET = "\x1b[0m" if use_color else ""

        def render_progress(ev: "ToolProgress") -> str:
            if ev.status == "called":
                return f"{DIM}  ↳ {BOLD}{ev.name}{RESET}{DIM}({ev.args_summary}){RESET}\n"
            if ev.status == "ok":
                tail = f" {DIM}— {ev.output_summary}{RESET}" if ev.output_summary else ""
                return f"{DIM}  {GREEN}✓{RESET}{DIM} {ev.name} in {ev.elapsed_ms}ms{RESET}{tail}\n"
            # error
            tail = f" {DIM}— {ev.error}{RESET}" if ev.error else ""
            return f"{DIM}  {RED}✗{RESET}{DIM} {ev.name} in {ev.elapsed_ms}ms{RESET}{tail}\n"

        async def reader():
            async for event in client.events():
                if isinstance(event, AssistantText):
                    sys.stdout.write(event.chunk)
                    sys.stdout.flush()
                elif isinstance(event, TurnEnd):
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                elif isinstance(event, ToolProgress):
                    # Progress goes to stderr so a piped stdout (>file.md)
                    # stays clean.
                    sys.stderr.write(render_progress(event))
                    sys.stderr.flush()
                elif isinstance(event, ErrorFrame):
                    sys.stderr.write(
                        f"\n{RED}[agent-host] server error: {event.code} {event.message}{RESET}\n"
                    )
                    return

        async def writer():
            loop = asyncio.get_event_loop()
            while True:
                sys.stderr.write("> ")
                sys.stderr.flush()
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    return
                text = line.rstrip("\n")
                if text in {":quit", ":q", "exit", "quit"}:
                    return
                await client.send_prompt(text)

        await asyncio.gather(reader(), writer())
    finally:
        await client.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))
    return asyncio.run(_main(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
