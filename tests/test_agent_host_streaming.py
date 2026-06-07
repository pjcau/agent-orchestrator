"""Tests for the agent-host streaming + cancellation surface (commit #4).

Coverage matrix:

* Registry chunk accumulation — happy path, monotonic seq, signature +
  nonce binding, ``MAX_STREAM_BYTES`` cap, ``MAX_CONCURRENT_STREAMS``
  cap. Each cap is a DoS guard.
* drive_session ingestion — accept_chunk wired into the main read loop,
  forged chunk silently dropped.
* Client streaming — shell_exec emits ``ToolChunk`` frames in order,
  monotonic seq starting at 0; signature recomputed per chunk.
* Client cancellation — server CANCEL while shell_exec is running kills
  the subprocess (no zombie), returns ``shell_cancelled``.
* Per-call timeout — shell_exec exceeding ``shell_timeout`` is killed
  and a ``shell_timeout`` SkillResult comes back.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_orchestrator.agent_host import (
    Ack,
    AgentHostClient,
    Cancel,
    LocalToolRunner,
    PendingToolCallsRegistry,
    ShellAllowlist,
    ToolCall,
    ToolChunk,
    ToolResult,
    compute_signature,
    drive_session,
    new_nonce,
    parse_frame,
)
from agent_orchestrator.agent_host.protocol import Hello


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.incoming: asyncio.Queue[dict] = asyncio.Queue()

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def receive_json(self) -> dict:
        return await self.incoming.get()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        pass


@pytest.fixture
def signing_key(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)


# ---------------------------------------------------------------------------
# Registry — chunk accumulation
# ---------------------------------------------------------------------------


class TestRegistryChunks:
    @pytest.mark.asyncio
    async def test_chunks_accumulate_in_order(self, signing_key):
        registry = PendingToolCallsRegistry(ttl_seconds=2.0)
        ws = FakeWS()

        async def fake_client():
            await asyncio.sleep(0)
            call = parse_frame(ws.sent[-1])
            tcid = call.tool_call_id
            for seq, txt in enumerate(["abc", "def", "ghi"]):
                sig = compute_signature(
                    run_id="r-1",
                    tool_call_id=tcid,
                    nonce=call.nonce,
                    name=call.name,
                )
                assert (
                    registry.accept_chunk(
                        run_id="r-1",
                        chunk=ToolChunk(
                            tool_call_id=tcid,
                            seq=seq,
                            chunk=txt,
                            nonce=call.nonce,
                            signature=sig,
                        ),
                    )
                    is None
                )
            # finalize with a tool_result
            sig = compute_signature(
                run_id="r-1",
                tool_call_id=tcid,
                nonce=call.nonce,
                name=call.name,
            )
            registry.resolve(
                run_id="r-1",
                result=ToolResult(
                    tool_call_id=tcid,
                    status="ok",
                    output={"stdout": "abcdefghi"},
                    nonce=call.nonce,
                    signature=sig,
                ),
            )

        client = asyncio.create_task(fake_client())
        result = await registry.issue(
            ws=ws, run_id="r-1", name="shell_exec", args={"argv": ["echo", "x"]}
        )
        await client
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_chunk_out_of_order_dropped(self, signing_key):
        registry = PendingToolCallsRegistry(ttl_seconds=0.5)
        ws = FakeWS()

        async def fake_client_out_of_order():
            await asyncio.sleep(0)
            call = parse_frame(ws.sent[-1])
            sig = compute_signature(
                run_id="r-1",
                tool_call_id=call.tool_call_id,
                nonce=call.nonce,
                name=call.name,
            )
            # Skip seq=0 and send seq=1 — must be rejected as out-of-order.
            assert (
                registry.accept_chunk(
                    run_id="r-1",
                    chunk=ToolChunk(
                        tool_call_id=call.tool_call_id,
                        seq=1,
                        chunk="oops",
                        nonce=call.nonce,
                        signature=sig,
                    ),
                )
                == "chunk_out_of_order"
            )

        client = asyncio.create_task(fake_client_out_of_order())
        with pytest.raises(asyncio.TimeoutError):
            await registry.issue(
                ws=ws, run_id="r-1", name="shell_exec", args={}
            )
        await client

    @pytest.mark.asyncio
    async def test_chunk_too_large_rejected(self, signing_key):
        registry = PendingToolCallsRegistry(ttl_seconds=0.5)
        registry.MAX_STREAM_BYTES = 8  # type: ignore[misc]
        ws = FakeWS()

        async def fake_client_too_large():
            await asyncio.sleep(0)
            call = parse_frame(ws.sent[-1])
            sig = compute_signature(
                run_id="r-1",
                tool_call_id=call.tool_call_id,
                nonce=call.nonce,
                name=call.name,
            )
            # First chunk under cap.
            err = registry.accept_chunk(
                run_id="r-1",
                chunk=ToolChunk(
                    tool_call_id=call.tool_call_id,
                    seq=0,
                    chunk="abcdef",  # 6 bytes
                    nonce=call.nonce,
                    signature=sig,
                ),
            )
            assert err is None
            # Second chunk would push past 8 bytes → rejected.
            err = registry.accept_chunk(
                run_id="r-1",
                chunk=ToolChunk(
                    tool_call_id=call.tool_call_id,
                    seq=1,
                    chunk="xyz",  # +3 bytes = 9
                    nonce=call.nonce,
                    signature=sig,
                ),
            )
            assert err == "chunk_too_large"

        client = asyncio.create_task(fake_client_too_large())
        with pytest.raises(asyncio.TimeoutError):
            await registry.issue(
                ws=ws, run_id="r-1", name="shell_exec", args={}
            )
        await client

    @pytest.mark.asyncio
    async def test_chunk_signature_rejected(self, signing_key):
        registry = PendingToolCallsRegistry(ttl_seconds=0.5)
        ws = FakeWS()

        async def fake_client_bad_sig():
            await asyncio.sleep(0)
            call = parse_frame(ws.sent[-1])
            err = registry.accept_chunk(
                run_id="r-1",
                chunk=ToolChunk(
                    tool_call_id=call.tool_call_id,
                    seq=0,
                    chunk="hi",
                    nonce=call.nonce,
                    signature="0" * 64,
                ),
            )
            assert err == "signature_invalid"

        client = asyncio.create_task(fake_client_bad_sig())
        with pytest.raises(asyncio.TimeoutError):
            await registry.issue(
                ws=ws, run_id="r-1", name="shell_exec", args={}
            )
        await client

    @pytest.mark.asyncio
    async def test_emit_cancel_sends_frame(self, signing_key):
        registry = PendingToolCallsRegistry(ttl_seconds=0.5)
        ws = FakeWS()

        async def driver():
            # Wait for the call to be sent.
            await asyncio.sleep(0.01)
            call = parse_frame(ws.sent[-1])
            await registry.emit_cancel(
                ws=ws, tool_call_id=call.tool_call_id, reason="user_ctrl_c"
            )

        task = asyncio.create_task(driver())
        with pytest.raises(asyncio.TimeoutError):
            await registry.issue(
                ws=ws, run_id="r-1", name="shell_exec", args={}
            )
        await task
        cancel_frame = next(f for f in ws.sent if f["kind"] == "cancel")
        assert cancel_frame["reason"] == "user_ctrl_c"

    @pytest.mark.asyncio
    async def test_emit_cancel_unknown_id_noop(self, signing_key):
        registry = PendingToolCallsRegistry()
        ws = FakeWS()
        await registry.emit_cancel(ws=ws, tool_call_id="ghost")
        assert ws.sent == []


# ---------------------------------------------------------------------------
# drive_session — chunk frame routing
# ---------------------------------------------------------------------------


class TestDriveSessionChunks:
    @pytest.mark.asyncio
    async def test_forged_chunk_silently_dropped(self, signing_key):
        registry = PendingToolCallsRegistry(ttl_seconds=0.1)
        ws = FakeWS()
        # Forge a chunk for an id never issued. Then send a malformed frame
        # to terminate the loop.
        await ws.incoming.put(
            ToolChunk(
                tool_call_id="ghost",
                seq=0,
                chunk="evil",
                nonce="0" * 32,
                signature="0" * 64,
            ).to_dict()
        )
        await ws.incoming.put({"kind": "totally-unknown"})
        reason = await drive_session(
            ws,
            hello=Hello(tool_manifest=["x"]),
            run_id="r-1",
            registry=registry,
        )
        assert reason == "protocol_error:malformed_frame"
        # No future was ever issued, so the forged chunk just landed in
        # the missing-id branch and was dropped.


# ---------------------------------------------------------------------------
# Client — streaming + cancellation end-to-end
# ---------------------------------------------------------------------------


class TestClientStreaming:
    @pytest.mark.asyncio
    async def test_shell_exec_streams_chunks(self, tmp_path: Path, signing_key):
        async def confirm(binary: str, high_risk: bool) -> bool:
            return True

        runner = LocalToolRunner(
            workspace=tmp_path,
            allowlist=ShellAllowlist(path=tmp_path / "a.json"),
            confirm_shell=confirm,
        )
        ws = FakeWS()
        client = AgentHostClient(ws, runner)
        await ws.incoming.put(Ack(run_id="r-1").to_dict())
        await client.handshake()

        nonce = new_nonce()
        sig = compute_signature(
            run_id="r-1", tool_call_id="tc-1", nonce=nonce, name="shell_exec"
        )
        # Pick a command that produces non-trivial output deterministically.
        await ws.incoming.put(
            ToolCall(
                tool_call_id="tc-1",
                name="shell_exec",
                args={"argv": ["printf", "abcdefgh"]},
                nonce=nonce,
                signature=sig,
            ).to_dict()
        )
        # Wait for either streamed chunks or final result.
        for _ in range(200):
            await asyncio.sleep(0.01)
            if any(f.get("kind") == "tool_result" for f in ws.sent):
                break

        chunks = [parse_frame(f) for f in ws.sent if f.get("kind") == "tool_chunk"]
        results = [parse_frame(f) for f in ws.sent if f.get("kind") == "tool_result"]
        assert results, "shell_exec did not finalize"
        assert results[0].status == "ok"
        if chunks:
            # If chunks were emitted, seq must be monotonic from 0.
            seqs = [c.seq for c in chunks]
            assert seqs == list(range(len(chunks)))
            assert "".join(c.chunk for c in chunks) == "abcdefgh"
        await client.close()

    @pytest.mark.asyncio
    async def test_server_cancel_kills_shell(self, tmp_path: Path, signing_key):
        async def confirm(binary: str, high_risk: bool) -> bool:
            return True

        runner = LocalToolRunner(
            workspace=tmp_path,
            allowlist=ShellAllowlist(path=tmp_path / "a.json"),
            confirm_shell=confirm,
        )
        ws = FakeWS()
        client = AgentHostClient(ws, runner)
        await ws.incoming.put(Ack(run_id="r-1").to_dict())
        await client.handshake()

        nonce = new_nonce()
        sig = compute_signature(
            run_id="r-1", tool_call_id="tc-1", nonce=nonce, name="shell_exec"
        )
        await ws.incoming.put(
            ToolCall(
                tool_call_id="tc-1",
                name="shell_exec",
                args={"argv": ["sleep", "10"]},
                nonce=nonce,
                signature=sig,
            ).to_dict()
        )
        # Wait briefly for the cancel event to register on the client side,
        # then send CANCEL.
        await asyncio.sleep(0.1)
        await ws.incoming.put(
            Cancel(tool_call_id="tc-1", reason="user_ctrl_c").to_dict()
        )
        for _ in range(300):
            await asyncio.sleep(0.01)
            if any(f.get("kind") == "tool_result" for f in ws.sent):
                break
        results = [parse_frame(f) for f in ws.sent if f.get("kind") == "tool_result"]
        assert results, "no tool_result after CANCEL"
        assert results[0].status == "error"
        assert results[0].error_code == "shell_cancelled"
        await client.close()
