"""Tests for the agent-host client — sandbox, allowlist, runner, WS client.

Coverage matrix:

* Path sandbox — happy path, ``../`` escape rejected, absolute escape
  rejected, symlink rejected by default, ``follow_symlinks=True`` lets it
  through, workspace-must-exist guard.
* Shell allowlist — argv validation, basename-only key, atomic save +
  reload, gate calls confirm only on first use, high-risk detection.
* LocalToolRunner — file_read / file_write happy + escape, shell_exec
  honours allowlist, shell_timeout, refuses ``argv`` as a string.
* AgentHostClient — handshake happy + version-error, tool_call dispatch
  + signature verify + reply.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_orchestrator.agent_host import (
    Ack,
    AgentHostClient,
    Error,
    LocalToolRunner,
    PathOutsideWorkspaceError,
    PROTOCOL_VERSION,
    ShellAllowlist,
    ShellAllowlistError,
    ToolCall,
    ToolResult,
    compute_signature,
    enforce_workspace,
    is_high_risk,
    new_nonce,
    parse_frame,
)


# ---------------------------------------------------------------------------
# Path sandbox
# ---------------------------------------------------------------------------


class TestEnforceWorkspace:
    def test_relative_inside(self, tmp_path: Path):
        (tmp_path / "a").mkdir()
        out = enforce_workspace(tmp_path, "a/b.txt")
        assert out == (tmp_path / "a" / "b.txt").resolve()

    def test_relative_escape_rejected(self, tmp_path: Path):
        with pytest.raises(PathOutsideWorkspaceError):
            enforce_workspace(tmp_path, "../etc/passwd")

    def test_absolute_outside_rejected(self, tmp_path: Path):
        with pytest.raises(PathOutsideWorkspaceError):
            enforce_workspace(tmp_path, "/etc/passwd")

    def test_absolute_inside_accepted(self, tmp_path: Path):
        inside = tmp_path / "x.txt"
        out = enforce_workspace(tmp_path, str(inside))
        assert out == inside.resolve()

    def test_symlink_default_rejects(self, tmp_path: Path):
        target = tmp_path.parent / "outside.txt"
        target.write_text("x")
        link = tmp_path / "alias"
        link.symlink_to(target)
        with pytest.raises(PathOutsideWorkspaceError):
            enforce_workspace(tmp_path, "alias")

    def test_symlink_follow_allowed(self, tmp_path: Path):
        target = tmp_path / "real.txt"
        target.write_text("x")
        link = tmp_path / "alias"
        link.symlink_to(target)
        out = enforce_workspace(tmp_path, "alias", follow_symlinks=True)
        # Resolves under the workspace.
        assert out.resolve().parent == tmp_path.resolve()

    def test_workspace_does_not_exist(self, tmp_path: Path):
        with pytest.raises(PathOutsideWorkspaceError):
            enforce_workspace(tmp_path / "ghost", "x")


# ---------------------------------------------------------------------------
# Shell allowlist
# ---------------------------------------------------------------------------


class TestShellAllowlist:
    def test_path_in_argv0_rejected(self, tmp_path: Path):
        allow = ShellAllowlist(path=tmp_path / "a.json")
        with pytest.raises(ShellAllowlistError):
            allow.contains(["/usr/bin/pytest"])
        with pytest.raises(ShellAllowlistError):
            allow.allow(["/usr/bin/pytest"])
        with pytest.raises(ShellAllowlistError):
            allow.allow(["sub\\bin"])

    def test_empty_argv_rejected(self, tmp_path: Path):
        allow = ShellAllowlist(path=tmp_path / "a.json")
        with pytest.raises(ShellAllowlistError):
            allow.contains([])
        with pytest.raises(ShellAllowlistError):
            allow.contains([""])

    def test_allow_persists_atomically(self, tmp_path: Path):
        p = tmp_path / "allow.json"
        allow = ShellAllowlist(path=p)
        allow.allow(["pytest", "-q"])
        # New instance picks up the saved entry.
        again = ShellAllowlist(path=p)
        assert again.contains(["pytest", "-q"]) is True
        assert again.snapshot() == ["pytest"]

    def test_revoke(self, tmp_path: Path):
        p = tmp_path / "allow.json"
        allow = ShellAllowlist(path=p)
        allow.allow(["pytest"])
        assert allow.revoke("pytest") is True
        assert allow.contains(["pytest"]) is False
        # Idempotent: revoking absent returns False, no save needed.
        assert allow.revoke("pytest") is False

    @pytest.mark.asyncio
    async def test_gate_calls_confirm_once(self, tmp_path: Path):
        p = tmp_path / "allow.json"
        allow = ShellAllowlist(path=p)
        calls: list[tuple[str, bool]] = []

        async def confirm(binary: str, high_risk: bool) -> bool:
            calls.append((binary, high_risk))
            return True

        assert await allow.gate(["pytest"], confirm=confirm) is True
        # Second call hits the cache, confirm not called again.
        assert await allow.gate(["pytest", "tests/"], confirm=confirm) is True
        assert calls == [("pytest", False)]

    @pytest.mark.asyncio
    async def test_gate_denied(self, tmp_path: Path):
        allow = ShellAllowlist(path=tmp_path / "a.json")

        async def confirm(binary: str, high_risk: bool) -> bool:
            return False

        assert await allow.gate(["rm"], confirm=confirm) is False
        assert allow.contains(["rm"]) is False

    def test_high_risk_detection(self):
        for bad in ("bash", "sh", "zsh", "dash"):
            assert is_high_risk(bad)
        for good in ("pytest", "git", "npm", "python3"):
            assert not is_high_risk(good)

    def test_load_corrupted_file_starts_empty(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("{not-json")
        allow = ShellAllowlist(path=p)
        assert allow.snapshot() == []


# ---------------------------------------------------------------------------
# Local tool runner
# ---------------------------------------------------------------------------


class TestLocalToolRunner:
    @pytest.mark.asyncio
    async def test_file_write_and_read(self, tmp_path: Path):
        runner = LocalToolRunner(workspace=tmp_path)
        w = await runner.run("file_write", {"file_path": "a.txt", "content": "hi"})
        assert w.success
        r = await runner.run("file_read", {"file_path": "a.txt"})
        assert r.success
        assert r.output == "hi"

    @pytest.mark.asyncio
    async def test_file_read_escape_rejected(self, tmp_path: Path):
        runner = LocalToolRunner(workspace=tmp_path)
        result = await runner.run("file_read", {"file_path": "../escape.txt"})
        assert not result.success
        assert result.error == "path_outside_workspace"

    @pytest.mark.asyncio
    async def test_shell_argv_string_refused(self, tmp_path: Path):
        runner = LocalToolRunner(workspace=tmp_path)
        result = await runner.run("shell_exec", {"argv": "rm -rf /"})
        assert not result.success
        assert result.error == "shell_requires_argv_list"

    @pytest.mark.asyncio
    async def test_shell_non_interactive_refuses_unknown(self, tmp_path: Path):
        # No confirm callback → unknown binary must be refused, not allowed.
        runner = LocalToolRunner(
            workspace=tmp_path,
            allowlist=ShellAllowlist(path=tmp_path / "a.json"),
            confirm_shell=None,
        )
        result = await runner.run("shell_exec", {"argv": ["pytest", "-q"]})
        assert not result.success
        assert result.error == "shell_denied"

    @pytest.mark.asyncio
    async def test_shell_with_confirm_runs(self, tmp_path: Path):
        async def confirm(binary: str, high_risk: bool) -> bool:
            return True

        runner = LocalToolRunner(
            workspace=tmp_path,
            allowlist=ShellAllowlist(path=tmp_path / "a.json"),
            confirm_shell=confirm,
        )
        # ``true`` is in coreutils on Linux; exit code 0.
        result = await runner.run("shell_exec", {"argv": ["true"]})
        assert result.success
        assert result.output["returncode"] == 0

    @pytest.mark.asyncio
    async def test_shell_timeout(self, tmp_path: Path):
        async def confirm(binary: str, high_risk: bool) -> bool:
            return True

        runner = LocalToolRunner(
            workspace=tmp_path,
            allowlist=ShellAllowlist(path=tmp_path / "a.json"),
            confirm_shell=confirm,
            shell_timeout=0.1,
        )
        result = await runner.run("shell_exec", {"argv": ["sleep", "5"]})
        assert not result.success
        assert result.error == "shell_timeout"

    @pytest.mark.asyncio
    async def test_unknown_tool(self, tmp_path: Path):
        runner = LocalToolRunner(workspace=tmp_path)
        result = await runner.run("rm_rf_everything", {})
        assert not result.success
        assert result.error == "unknown_tool"

    def test_manifest_exposed(self, tmp_path: Path):
        runner = LocalToolRunner(workspace=tmp_path)
        assert set(runner.manifest) == {"file_read", "file_write", "shell_exec"}


# ---------------------------------------------------------------------------
# AgentHostClient — handshake + tool dispatch
# ---------------------------------------------------------------------------


class FakeWS:
    """Same shape as in test_agent_host_server.FakeWS; kept local to avoid
    cross-test-file imports."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.incoming: asyncio.Queue[dict] = asyncio.Queue()
        self.closed = False

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def receive_json(self) -> dict:
        return await self.incoming.get()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True


@pytest.fixture
def signing_key(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)


class TestAgentHostClient:
    @pytest.mark.asyncio
    async def test_handshake_happy(self, tmp_path: Path, signing_key):
        ws = FakeWS()
        runner = LocalToolRunner(workspace=tmp_path)
        client = AgentHostClient(ws, runner)
        await ws.incoming.put(
            Ack(
                run_id="r-1",
                agent="backend",
                model="tencent/hy3-preview",
                provider="openrouter",
                capabilities=["file_read", "file_write", "shell_exec"],
            ).to_dict()
        )
        info = await client.handshake(
            agent="backend", model="tencent/hy3-preview", provider="openrouter"
        )
        assert info.run_id == "r-1"
        assert info.capabilities == ("file_read", "file_write", "shell_exec")
        sent_hello = parse_frame(ws.sent[0])
        assert sent_hello.tool_manifest == ["file_read", "file_write", "shell_exec"]
        assert sent_hello.cwd == str(tmp_path.resolve())
        assert sent_hello.version == PROTOCOL_VERSION
        await client.close()

    @pytest.mark.asyncio
    async def test_handshake_server_error(self, tmp_path: Path, signing_key):
        ws = FakeWS()
        client = AgentHostClient(ws, LocalToolRunner(workspace=tmp_path))
        await ws.incoming.put(
            Error(code="version_unsupported", message="need v2").to_dict()
        )
        with pytest.raises(RuntimeError, match="version_unsupported"):
            await client.handshake()

    @pytest.mark.asyncio
    async def test_tool_call_dispatch(self, tmp_path: Path, signing_key):
        """Server issues file_write; client executes locally and replies signed."""
        ws = FakeWS()
        runner = LocalToolRunner(workspace=tmp_path)
        client = AgentHostClient(ws, runner)
        await ws.incoming.put(Ack(run_id="r-1").to_dict())
        await client.handshake()

        nonce = new_nonce()
        sig = compute_signature(
            run_id="r-1", tool_call_id="tc-1", nonce=nonce, name="file_write"
        )
        await ws.incoming.put(
            ToolCall(
                tool_call_id="tc-1",
                name="file_write",
                args={"file_path": "note.md", "content": "hello"},
                nonce=nonce,
                signature=sig,
            ).to_dict()
        )

        # Give the receive loop a chance to consume the frame.
        for _ in range(40):
            await asyncio.sleep(0.01)
            if any(f.get("kind") == "tool_result" for f in ws.sent):
                break
        results = [f for f in ws.sent if f.get("kind") == "tool_result"]
        assert results, f"no tool_result sent; sent={ws.sent}"
        reply = parse_frame(results[0])
        assert isinstance(reply, ToolResult)
        assert reply.status == "ok"
        # File actually landed in the workspace.
        assert (tmp_path / "note.md").read_text() == "hello"
        # Echoed nonce; signature recomputed by the client.
        assert reply.nonce == nonce
        assert reply.signature  # 64 hex chars
        await client.close()

    @pytest.mark.asyncio
    async def test_tool_call_bad_signature_rejected(
        self, tmp_path: Path, signing_key
    ):
        ws = FakeWS()
        runner = LocalToolRunner(workspace=tmp_path)
        client = AgentHostClient(ws, runner)
        await ws.incoming.put(Ack(run_id="r-1").to_dict())
        await client.handshake()

        await ws.incoming.put(
            ToolCall(
                tool_call_id="tc-1",
                name="file_write",
                args={"file_path": "evil.md", "content": "owned"},
                nonce=new_nonce(),
                signature="0" * 64,
            ).to_dict()
        )

        for _ in range(40):
            await asyncio.sleep(0.01)
            if any(f.get("kind") == "tool_result" for f in ws.sent):
                break
        results = [f for f in ws.sent if f.get("kind") == "tool_result"]
        assert results
        reply = parse_frame(results[0])
        assert reply.status == "error"
        assert reply.error_code == "signature_invalid"
        # And critically: the file was NOT written.
        assert not (tmp_path / "evil.md").exists()
        await client.close()
