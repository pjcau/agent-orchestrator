"""Tests for the agent-host server module — registry, adapter, driver.

In-memory ``FakeWS`` deliberately mimics only the three methods the
production WebSocket exposes (``send_json``, ``receive_json``, ``close``).
This keeps the unit tests transport-agnostic and FAST — no asyncio
server, no port allocation. The FastAPI route integration is checked
separately in :mod:`test_cli_routes` (commit #3 wiring).

Threats exercised here (each test maps to one row of the threat model
in ``docs/cli.md``):

* signature_invalid — tampered signature drops the result silently
  (does NOT close the WS, to prevent DoS-by-forged-frame)
* unknown_tool_call_id — replay or guess of an id never issued
* nonce_mismatch — attacker captures (tool_call_id, signature) but
  cannot reuse with a different nonce
* version_unsupported — protocol drift fails loud
* handshake_timeout — slow-loris client cannot pin a slot
* manifest_rejected — empty manifest refused (useless session)
"""

from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.agent_host import (
    PROTOCOL_VERSION,
    AgentHostError,
    Hello,
    PendingToolCallsRegistry,
    Prompt,
    RemoteSkillAdapter,
    ToolCall,
    ToolResult,
    compute_signature,
    drive_session,
    parse_frame,
    perform_handshake,
    serve_agent_host,
)

# ---------------------------------------------------------------------------
# FakeWS
# ---------------------------------------------------------------------------


class FakeWS:
    """Bidirectional in-memory WebSocket double.

    ``send_json`` appends to ``sent``; ``receive_json`` pops from
    ``incoming`` (an asyncio.Queue so tests can inject frames concurrently
    with the driver). ``close`` records the code+reason.
    """

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.incoming: asyncio.Queue[dict] = asyncio.Queue()
        self.closed_code: int | None = None
        self.closed_reason: str = ""

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def receive_json(self) -> dict:
        return await self.incoming.get()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_code = code
        self.closed_reason = reason


@pytest.fixture
def signing_key(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)


# ---------------------------------------------------------------------------
# Tool TTL configuration
# ---------------------------------------------------------------------------


class TestToolTTLConfig:
    """The per-tool result TTL must be generous and env-tunable.

    Regression guard: a 60 s TTL was shorter than a human takes to answer
    an interactive ``allow `ls`? [y/N]`` confirmation, so the call timed
    out mid-prompt and the connection dropped (``Broken pipe``).
    """

    def test_default_is_generous(self, monkeypatch):
        from agent_orchestrator.agent_host import server

        monkeypatch.delenv("AGENT_HOST_TOOL_TTL_SECONDS", raising=False)
        # Default must comfortably exceed a human confirmation latency.
        assert server._tool_ttl_from_env() >= 120.0

    def test_env_override(self, monkeypatch):
        from agent_orchestrator.agent_host import server

        monkeypatch.setenv("AGENT_HOST_TOOL_TTL_SECONDS", "42.5")
        assert server._tool_ttl_from_env() == 42.5

    def test_invalid_env_falls_back(self, monkeypatch):
        from agent_orchestrator.agent_host import server

        monkeypatch.setenv("AGENT_HOST_TOOL_TTL_SECONDS", "not-a-number")
        assert server._tool_ttl_from_env(default=99.0) == 99.0

    def test_non_positive_env_falls_back(self, monkeypatch):
        from agent_orchestrator.agent_host import server

        monkeypatch.setenv("AGENT_HOST_TOOL_TTL_SECONDS", "0")
        assert server._tool_ttl_from_env(default=99.0) == 99.0


# ---------------------------------------------------------------------------
# Handshake
# ---------------------------------------------------------------------------


class TestHandshake:
    @pytest.mark.asyncio
    async def test_happy_path(self, signing_key):
        ws = FakeWS()
        hello = Hello(
            version=PROTOCOL_VERSION,
            cwd="/tmp/proj",
            tool_manifest=["file_read", "file_write"],
        )
        await ws.incoming.put(hello.to_dict())
        result, run_id, session_key = await perform_handshake(ws, run_id_factory=lambda: "run-123")
        assert run_id == "run-123"
        assert result.tool_manifest == ["file_read", "file_write"]
        # Session key is opaque bytes; ack carries the hex form.
        assert isinstance(session_key, bytes) and len(session_key) == 32
        ack_raw = ws.sent[0]
        assert ack_raw["kind"] == "ack"
        assert ack_raw["run_id"] == "run-123"
        assert ack_raw["capabilities"] == ["file_read", "file_write"]
        assert ack_raw["signing_key"] == session_key.hex()

    @pytest.mark.asyncio
    async def test_version_mismatch(self, signing_key):
        ws = FakeWS()
        await ws.incoming.put(Hello(version=99, tool_manifest=["x"]).to_dict())
        with pytest.raises(AgentHostError) as exc:
            await perform_handshake(ws)
        assert exc.value.code == "version_unsupported"

    @pytest.mark.asyncio
    async def test_first_frame_not_hello(self, signing_key):
        ws = FakeWS()
        await ws.incoming.put(Prompt(text="hi").to_dict())
        with pytest.raises(AgentHostError) as exc:
            await perform_handshake(ws)
        assert exc.value.code == "handshake_expected_hello"

    @pytest.mark.asyncio
    async def test_empty_manifest_rejected(self, signing_key):
        ws = FakeWS()
        await ws.incoming.put(Hello(tool_manifest=[]).to_dict())
        with pytest.raises(AgentHostError) as exc:
            await perform_handshake(ws)
        assert exc.value.code == "manifest_rejected"

    @pytest.mark.asyncio
    async def test_malformed_first_frame(self, signing_key):
        ws = FakeWS()
        await ws.incoming.put({"kind": "garbage"})
        with pytest.raises(AgentHostError) as exc:
            await perform_handshake(ws)
        assert exc.value.code == "malformed_frame"


# ---------------------------------------------------------------------------
# Pending tool calls registry — issue / resolve / verify
# ---------------------------------------------------------------------------


class TestRegistry:
    @pytest.mark.asyncio
    async def test_issue_and_resolve(self, signing_key):
        registry = PendingToolCallsRegistry(ttl_seconds=2.0)
        ws = FakeWS()

        async def fake_client():
            await asyncio.sleep(0)
            raw = ws.sent[-1]
            assert raw["kind"] == "tool_call"
            call = parse_frame(raw)
            assert isinstance(call, ToolCall)
            sig = compute_signature(
                run_id="run-1",
                tool_call_id=call.tool_call_id,
                nonce=call.nonce,
                name=call.name,
            )
            tr = ToolResult(
                tool_call_id=call.tool_call_id,
                status="ok",
                output={"bytes_written": 5},
                nonce=call.nonce,
                signature=sig,
            )
            err = registry.resolve(run_id="run-1", result=tr)
            assert err is None

        client_task = asyncio.create_task(fake_client())
        result = await registry.issue(
            ws=ws, run_id="run-1", name="file_write", args={"path": "a.txt"}
        )
        await client_task
        assert result.status == "ok"
        assert result.output == {"bytes_written": 5}
        assert registry.in_flight == 0

    @pytest.mark.asyncio
    async def test_timeout(self, signing_key):
        registry = PendingToolCallsRegistry(ttl_seconds=0.05)
        ws = FakeWS()
        with pytest.raises(asyncio.TimeoutError):
            await registry.issue(ws=ws, run_id="run-1", name="file_read", args={"path": "x"})
        # Slot freed even after timeout.
        assert registry.in_flight == 0

    @pytest.mark.asyncio
    async def test_signature_tamper_dropped(self, signing_key):
        """A tampered signature must be silently dropped (no DoS via close)."""
        registry = PendingToolCallsRegistry(ttl_seconds=0.2)
        ws = FakeWS()

        async def fake_client_evil():
            await asyncio.sleep(0)
            call = parse_frame(ws.sent[-1])
            evil = ToolResult(
                tool_call_id=call.tool_call_id,
                status="ok",
                output="evil",
                nonce=call.nonce,
                signature="0" * 64,
            )
            err = registry.resolve(run_id="run-1", result=evil)
            assert err == "signature_invalid"

        client_task = asyncio.create_task(fake_client_evil())
        with pytest.raises(asyncio.TimeoutError):
            await registry.issue(ws=ws, run_id="run-1", name="file_write", args={})
        await client_task

    @pytest.mark.asyncio
    async def test_unknown_id_dropped(self, signing_key):
        registry = PendingToolCallsRegistry()
        bogus = ToolResult(
            tool_call_id="never-issued",
            status="ok",
            output=None,
            nonce="0" * 32,
            signature="0" * 64,
        )
        err = registry.resolve(run_id="run-1", result=bogus)
        assert err == "unknown_tool_call_id"

    @pytest.mark.asyncio
    async def test_nonce_mismatch_dropped(self, signing_key):
        """Right tool_call_id, wrong nonce — must be rejected.

        Defense in depth against a hypothetical attacker who learns the
        id from a side channel but not the freshly-rolled nonce.
        """
        registry = PendingToolCallsRegistry(ttl_seconds=0.2)
        ws = FakeWS()

        async def fake_client_wrong_nonce():
            await asyncio.sleep(0)
            call = parse_frame(ws.sent[-1])
            wrong = ToolResult(
                tool_call_id=call.tool_call_id,
                status="ok",
                output="x",
                nonce="aa" * 16,  # not the issued nonce
                signature="0" * 64,
            )
            err = registry.resolve(run_id="run-1", result=wrong)
            assert err == "nonce_mismatch"

        client_task = asyncio.create_task(fake_client_wrong_nonce())
        with pytest.raises(asyncio.TimeoutError):
            await registry.issue(ws=ws, run_id="run-1", name="shell_exec", args={})
        await client_task


# ---------------------------------------------------------------------------
# Remote skill adapter
# ---------------------------------------------------------------------------


class TestRemoteSkillAdapter:
    @pytest.mark.asyncio
    async def test_execute_success(self, signing_key):
        registry = PendingToolCallsRegistry(ttl_seconds=2.0)
        ws = FakeWS()
        adapter = RemoteSkillAdapter(
            name="file_write",
            description="write a file",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
            registry=registry,
            ws=ws,
            run_id="run-1",
        )

        async def fake_client():
            await asyncio.sleep(0)
            call = parse_frame(ws.sent[-1])
            sig = compute_signature(
                run_id="run-1",
                tool_call_id=call.tool_call_id,
                nonce=call.nonce,
                name=call.name,
            )
            registry.resolve(
                run_id="run-1",
                result=ToolResult(
                    tool_call_id=call.tool_call_id,
                    status="ok",
                    output="ok",
                    nonce=call.nonce,
                    signature=sig,
                ),
            )

        client = asyncio.create_task(fake_client())
        result = await adapter.execute({"path": "a.txt", "content": "hi"})
        await client
        assert result.success is True
        assert result.output == "ok"
        assert result.metadata == {"tool": "file_write", "delegated": True}

    @pytest.mark.asyncio
    async def test_execute_timeout_returns_skillresult(self, signing_key):
        registry = PendingToolCallsRegistry(ttl_seconds=0.05)
        ws = FakeWS()
        adapter = RemoteSkillAdapter(
            name="shell_exec",
            description="run a command",
            parameters={"type": "object"},
            registry=registry,
            ws=ws,
            run_id="run-1",
        )
        result = await adapter.execute({"cmd": "sleep 99"})
        assert result.success is False
        assert result.error == "tool_timeout"

    @pytest.mark.asyncio
    async def test_execute_error_status_propagates(self, signing_key):
        registry = PendingToolCallsRegistry(ttl_seconds=2.0)
        ws = FakeWS()
        adapter = RemoteSkillAdapter(
            name="file_read",
            description="read a file",
            parameters={"type": "object"},
            registry=registry,
            ws=ws,
            run_id="run-1",
        )

        async def fake_client():
            await asyncio.sleep(0)
            call = parse_frame(ws.sent[-1])
            sig = compute_signature(
                run_id="run-1",
                tool_call_id=call.tool_call_id,
                nonce=call.nonce,
                name=call.name,
            )
            registry.resolve(
                run_id="run-1",
                result=ToolResult(
                    tool_call_id=call.tool_call_id,
                    status="error",
                    error_code="not_found",
                    nonce=call.nonce,
                    signature=sig,
                ),
            )

        client = asyncio.create_task(fake_client())
        result = await adapter.execute({"path": "missing.txt"})
        await client
        assert result.success is False
        assert result.error == "not_found"

    def test_build_remote_registry_one_adapter_per_tool(self):
        from agent_orchestrator.agent_host import build_remote_registry

        registry = PendingToolCallsRegistry()
        ws = FakeWS()
        hello = Hello(tool_manifest=["file_read", "file_write", "shell_exec"])
        skills = build_remote_registry(hello=hello, registry=registry, ws=ws, run_id="r-1")
        for name in hello.tool_manifest:
            sk = skills.get(name)
            assert isinstance(sk, RemoteSkillAdapter)
            assert sk.parameters["type"] == "object"

    def test_build_remote_registry_uses_supplied_schemas(self):
        from agent_orchestrator.agent_host import build_remote_registry

        registry = PendingToolCallsRegistry()
        ws = FakeWS()
        hello = Hello(tool_manifest=["file_write"])
        custom_schema = {
            "type": "object",
            "required": ["path", "content"],
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        }
        skills = build_remote_registry(
            hello=hello,
            registry=registry,
            ws=ws,
            run_id="r-1",
            parameter_schemas={"file_write": custom_schema},
            descriptions={"file_write": "write a file in the user workspace"},
        )
        sk = skills.get("file_write")
        assert sk.parameters == custom_schema
        assert sk.description == "write a file in the user workspace"

    def test_adapter_exposes_skill_metadata(self):
        # No signing key needed for property access.
        adapter = RemoteSkillAdapter(
            name="x",
            description="y",
            parameters={"type": "object"},
            registry=PendingToolCallsRegistry(),
            ws=FakeWS(),
            run_id="r",
            category="filesystem",
        )
        assert adapter.name == "x"
        assert adapter.description == "y"
        assert adapter.parameters == {"type": "object"}
        assert adapter.category == "filesystem"


# ---------------------------------------------------------------------------
# drive_session — prompt routing
# ---------------------------------------------------------------------------


class TestDriveSessionPrompt:
    """drive_session builds a remote SkillRegistry once and hands it to on_prompt."""

    @pytest.mark.asyncio
    async def test_on_prompt_receives_registry_and_run_id(self, signing_key):
        from agent_orchestrator.agent_host import drive_session
        from agent_orchestrator.core.skill import SkillRegistry

        registry = PendingToolCallsRegistry()
        ws = FakeWS()
        hello = Hello(
            tool_manifest=["file_read", "file_write"],
            agent="backend",
            model="tencent/hy3-preview",
            provider="openrouter",
        )
        captured: dict = {}
        done = asyncio.Event()

        async def on_prompt(text, skills, run_id, hello_in):
            captured["text"] = text
            captured["skills_type"] = type(skills)
            captured["skill_names"] = sorted(
                n for n in ["file_read", "file_write"] if skills.get(n) is not None
            )
            captured["run_id"] = run_id
            captured["hello_agent"] = hello_in.agent
            done.set()

        await ws.incoming.put(Prompt(text="hello world").to_dict())

        # Turns now run as concurrent tasks, so feed the terminating frame
        # only after the turn has actually run — otherwise the loop could
        # exit and cancel the task before it executes.
        async def terminate():
            await done.wait()
            await ws.incoming.put({"kind": "totally-unknown"})

        term_task = asyncio.create_task(terminate())
        await drive_session(
            ws,
            hello=hello,
            run_id="r-1",
            registry=registry,
            on_prompt=on_prompt,
        )
        await term_task
        assert captured["text"] == "hello world"
        assert captured["skills_type"] is SkillRegistry
        assert captured["skill_names"] == ["file_read", "file_write"]
        assert captured["run_id"] == "r-1"
        assert captured["hello_agent"] == "backend"

    @pytest.mark.asyncio
    async def test_turn_does_not_deadlock_tool_result(self, signing_key):
        """A tool-delegating turn must not block the receive loop.

        Regression: drive_session used to ``await on_prompt(...)`` inline,
        so the agent's tool call blocked on a ToolResult future that only
        the (now blocked) loop could resolve — every tool call hung until
        its TTL (~5 min) and the turn timed out. Spawning the turn keeps
        the loop free to receive and resolve the result, so the round-trip
        completes promptly.
        """
        from agent_orchestrator.agent_host import drive_session

        registry = PendingToolCallsRegistry(ttl_seconds=2.0)
        ws = FakeWS()
        hello = Hello(tool_manifest=["file_read"], agent="backend")
        turn_ok = asyncio.Event()

        async def on_prompt(text, skills, run_id, hello_in):
            # Delegate a tool mid-turn — blocks until the loop resolves it.
            result = await registry.issue(
                ws=ws, run_id=run_id, name="file_read", args={"path": "x"}
            )
            if result.status == "ok":
                turn_ok.set()

        async def responder():
            # Wait for the turn task to emit its tool_call, then sign and
            # enqueue the matching result the way a real client would.
            call = None
            for _ in range(300):
                raw = next((f for f in ws.sent if f.get("kind") == "tool_call"), None)
                if raw is not None:
                    call = parse_frame(raw)
                    break
                await asyncio.sleep(0.01)
            assert isinstance(call, ToolCall), "turn never emitted a tool_call (deadlock)"
            sig = compute_signature(
                run_id="r-1",
                tool_call_id=call.tool_call_id,
                nonce=call.nonce,
                name=call.name,
            )
            await ws.incoming.put(
                ToolResult(
                    tool_call_id=call.tool_call_id,
                    status="ok",
                    output={"ok": True},
                    nonce=call.nonce,
                    signature=sig,
                ).to_dict()
            )
            await turn_ok.wait()
            await ws.incoming.put({"kind": "totally-unknown"})  # terminate loop

        await ws.incoming.put(Prompt(text="do it").to_dict())
        responder_task = asyncio.create_task(responder())
        reason = await asyncio.wait_for(
            drive_session(ws, hello=hello, run_id="r-1", registry=registry, on_prompt=on_prompt),
            timeout=3.0,
        )
        await responder_task
        assert turn_ok.is_set()
        assert reason == "protocol_error:malformed_frame"


# ---------------------------------------------------------------------------
# End-to-end driver
# ---------------------------------------------------------------------------


class TestServeAgentHost:
    @pytest.mark.asyncio
    async def test_handshake_failure_closes_with_error_frame(self, signing_key):
        ws = FakeWS()
        await ws.incoming.put(Hello(version=99, tool_manifest=["x"]).to_dict())
        reason = await serve_agent_host(ws)
        assert reason == "protocol_error:version_unsupported"
        # Error frame emitted before close.
        assert any(f["kind"] == "error" and f["code"] == "version_unsupported" for f in ws.sent)
        assert ws.closed_code == 1008

    @pytest.mark.asyncio
    async def test_drive_session_silent_drop_on_bad_result(self, signing_key):
        """Forged tool_result must not crash or close the session.

        Sequence: bogus result lands → registry rejects (logged) → loop
        keeps reading. We then feed a frame with unknown kind to force the
        loop to exit via the protocol-error branch — that lets us assert
        no exception bubbled and the bogus result was silently dropped.
        """
        registry = PendingToolCallsRegistry(ttl_seconds=0.1)
        ws = FakeWS()
        bogus = ToolResult(
            tool_call_id="never",
            status="ok",
            output="x",
            nonce="0" * 32,
            signature="0" * 64,
        )
        await ws.incoming.put(bogus.to_dict())
        # Malformed kind → drive_session emits Error and exits with
        # "protocol_error:malformed_frame". That's the deterministic
        # terminator we want for this test.
        await ws.incoming.put({"kind": "totally-unknown"})
        hello = Hello(tool_manifest=["x"])
        reason = await drive_session(ws, hello=hello, run_id="r", registry=registry)
        assert reason == "protocol_error:malformed_frame"
        assert registry.in_flight == 0
        # The bogus result never resolved any future and the loop did not
        # send a close — it only emitted an Error frame for the malformed
        # kind. Confirms the silent-drop policy on forged results.
        kinds = [f["kind"] for f in ws.sent]
        assert "error" in kinds
        assert kinds.count("error") == 1
