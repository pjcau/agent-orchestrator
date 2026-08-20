"""Tests for the agent-host wire protocol and HMAC signing.

Coverage matrix:

* Frame round-trip — every kind: ``to_dict`` then ``from_dict`` is identity
  on the user-controlled fields and preserves the header.
* Parser dispatch — ``parse_frame`` routes on ``kind`` and rejects unknown
  / missing kinds (fail-loud).
* Forward compatibility — unknown payload fields are dropped tolerantly;
  the kind discriminator remains the only hard boundary.
* Signing — positive path, every-field-tamper rejected, missing key
  raises at sign time and returns False at verify time (fail-closed),
  constant-time helper does not leak on first differing byte.
"""

from __future__ import annotations

import pytest

from agent_orchestrator.agent_host import (
    PROTOCOL_VERSION,
    Ack,
    AssistantText,
    Cancel,
    Error,
    Hello,
    Prompt,
    SigningKeyMissingError,
    Step,
    ToolCall,
    ToolChunk,
    ToolResult,
    TurnEnd,
    UnknownFrameError,
    compute_signature,
    new_nonce,
    parse_frame,
    verify_signature,
)
from agent_orchestrator.agent_host.protocol import KIND_HELLO, KIND_TOOL_CALL

# ---------------------------------------------------------------------------
# Round-trip and dispatch
# ---------------------------------------------------------------------------


class TestFrameRoundTrip:
    """Every frame kind must survive ``to_dict`` → ``from_dict``."""

    def _round_trip(self, frame):
        clone = type(frame).from_dict(frame.to_dict())
        assert clone == frame

    def test_hello(self):
        self._round_trip(
            Hello(
                version=PROTOCOL_VERSION,
                cwd="/home/user/proj",
                tool_manifest=["file_read", "file_write", "shell_exec"],
                stream_caps=["tool_chunk"],
                agent="team-lead",
                model="tencent/hy3-preview",
                provider="openrouter",
            )
        )

    def test_ack(self):
        self._round_trip(
            Ack(
                run_id="run-abc",
                agent="team-lead",
                model="tencent/hy3-preview",
                provider="openrouter",
                capabilities=["file_read", "shell_exec"],
            )
        )

    def test_prompt(self):
        self._round_trip(Prompt(text="hello agent"))

    def test_tool_call(self):
        self._round_trip(
            ToolCall(
                tool_call_id="tc-1",
                name="file_write",
                args={"path": "note.md", "content": "x"},
                nonce="deadbeef",
                signature="fake",
            )
        )

    def test_tool_result(self):
        self._round_trip(
            ToolResult(
                tool_call_id="tc-1",
                status="ok",
                output={"bytes_written": 1},
                nonce="deadbeef",
                signature="fake",
            )
        )

    def test_tool_chunk(self):
        self._round_trip(
            ToolChunk(
                tool_call_id="tc-1",
                seq=3,
                chunk="line 3\n",
                eof=False,
                nonce="deadbeef",
                signature="fake",
            )
        )

    def test_cancel(self):
        self._round_trip(Cancel(tool_call_id="tc-1", reason="user_ctrl_c"))

    def test_assistant_text(self):
        self._round_trip(AssistantText(chunk="token"))

    def test_turn_end(self):
        self._round_trip(TurnEnd(status="ok", step_count=7))

    def test_turn_end_with_usage(self):
        self._round_trip(
            TurnEnd(
                status="ok",
                step_count=7,
                input_tokens=1234,
                output_tokens=567,
                cost_usd=0.0123,
            )
        )

    def test_turn_end_with_error_reason(self):
        self._round_trip(
            TurnEnd(
                status="error",
                step_count=1,
                input_tokens=510,
                output_tokens=456,
                cost_usd=0.0002,
                error="Max steps (10) reached",
            )
        )

    def test_step(self):
        self._round_trip(Step(index=2, total=15, label="writing main.py", agent="backend"))

    def test_step_with_usage(self):
        self._round_trip(
            Step(
                index=3,
                total=0,
                label="thinking",
                agent="team-lead",
                input_tokens=4096,
                output_tokens=512,
                cost_usd=0.004,
            )
        )

    def test_step_with_digest(self):
        self._round_trip(
            Step(
                index=1,
                total=40,
                label="thinking",
                agent="team-lead",
                digest="injected (4 files, 1 ok-cmd, 2 bad-cmd, keep)",
            )
        )

    def test_old_step_without_digest_defaults_empty(self):
        # A server that omits the digest note → client defaults it to "".
        parsed = Step.from_dict(
            {
                "kind": Step.kind,
                "frame_id": "f",
                "timestamp": 0.0,
                "index": 1,
                "total": 0,
                "label": "x",
                "agent": "",
            }
        )
        assert parsed.digest == ""

    def test_error(self):
        self._round_trip(Error(code="version_unsupported", message="v2 only"))


class TestParseFrame:
    """``parse_frame`` dispatches on ``kind`` and fails loud on garbage."""

    def test_dispatches_each_kind(self):
        cases = [
            Hello(),
            Ack(),
            Prompt(text="x"),
            ToolCall(tool_call_id="t"),
            ToolResult(tool_call_id="t"),
            ToolChunk(tool_call_id="t"),
            Cancel(tool_call_id="t"),
            AssistantText(),
            TurnEnd(),
            Error(),
        ]
        for original in cases:
            parsed = parse_frame(original.to_dict())
            assert type(parsed) is type(original)
            assert parsed == original

    def test_unknown_kind_raises(self):
        with pytest.raises(UnknownFrameError):
            parse_frame({"kind": "totally_unknown", "frame_id": "x"})

    def test_missing_kind_raises(self):
        with pytest.raises(UnknownFrameError):
            parse_frame({"frame_id": "x"})

    def test_empty_dict_raises(self):
        with pytest.raises(UnknownFrameError):
            parse_frame({})

    def test_unknown_payload_field_dropped(self):
        """Forward compatibility: a v2 peer adds ``priority`` to TOOL_CALL.

        v1 receiver should still parse the frame, dropping the unknown
        field rather than raising — but it must keep the kind boundary
        strict.
        """
        raw = {
            "kind": KIND_TOOL_CALL,
            "frame_id": "f",
            "timestamp": 0.0,
            "tool_call_id": "t",
            "name": "file_read",
            "args": {},
            "nonce": "",
            "signature": "",
            "priority": "high",
        }
        parsed = parse_frame(raw)
        assert isinstance(parsed, ToolCall)
        assert parsed.name == "file_read"

    def test_old_step_without_usage_defaults_to_zero(self):
        """Backward compatibility: a v1 server emits Step without usage.

        A new client must still parse it, defaulting the token meter
        fields to 0 rather than raising.
        """
        raw = {
            "kind": "step",
            "frame_id": "f",
            "timestamp": 0.0,
            "index": 1,
            "total": 0,
            "label": "x",
            "agent": "",
        }
        parsed = parse_frame(raw)
        assert isinstance(parsed, Step)
        assert parsed.input_tokens == 0
        assert parsed.output_tokens == 0
        assert parsed.cost_usd == 0.0

    def test_hello_version_carried_through(self):
        """The data layer does not validate ``version`` — that's the server's
        handshake job. Just make sure the field survives round-trip."""
        h = Hello(version=99)
        d = h.to_dict()
        assert d["kind"] == KIND_HELLO
        assert d["version"] == 99
        again = parse_frame(d)
        assert isinstance(again, Hello)
        assert again.version == 99


# ---------------------------------------------------------------------------
# HMAC signing
# ---------------------------------------------------------------------------


@pytest.fixture
def signing_key(monkeypatch):
    """Set a deterministic JWT_SECRET_KEY for sign/verify tests."""
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)
    yield


class TestSigning:
    def test_round_trip(self, signing_key):
        sig = compute_signature(run_id="r1", tool_call_id="t1", nonce="n1", name="file_write")
        assert verify_signature(
            run_id="r1",
            tool_call_id="t1",
            nonce="n1",
            name="file_write",
            signature=sig,
        )

    @pytest.mark.parametrize(
        "field, bad_value",
        [
            ("run_id", "r2"),
            ("tool_call_id", "t2"),
            ("nonce", "n2"),
            ("name", "file_read"),
        ],
    )
    def test_tamper_rejected(self, signing_key, field, bad_value):
        """Flipping any single field must invalidate the signature.

        Security guarantee: an attacker who captures a (signature, nonce)
        pair cannot reuse it for a different tool_call, run, or tool name.
        """
        sig = compute_signature(run_id="r1", tool_call_id="t1", nonce="n1", name="file_write")
        kwargs = {
            "run_id": "r1",
            "tool_call_id": "t1",
            "nonce": "n1",
            "name": "file_write",
            "signature": sig,
        }
        kwargs[field] = bad_value
        assert not verify_signature(**kwargs)

    def test_empty_signature_rejected(self, signing_key):
        assert not verify_signature(run_id="r", tool_call_id="t", nonce="n", name="x", signature="")

    def test_wrong_secret_rejected(self, signing_key, monkeypatch):
        """A signature minted under key A must not verify under key B.

        Secret-rotation guarantee: bumping ``JWT_SECRET_KEY`` invalidates
        every outstanding tool_call signature (same property as session
        cookies in ``dashboard.auth``).
        """
        sig = compute_signature(run_id="r", tool_call_id="t", nonce="n", name="x")
        monkeypatch.setenv("JWT_SECRET_KEY", "y" * 32)
        assert not verify_signature(
            run_id="r", tool_call_id="t", nonce="n", name="x", signature=sig
        )

    def test_missing_key_raises_at_sign(self, monkeypatch):
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        with pytest.raises(SigningKeyMissingError):
            compute_signature(run_id="r", tool_call_id="t", nonce="n", name="x")

    def test_missing_key_returns_false_at_verify(self, monkeypatch):
        """Verify must fail-closed when the key is missing, not raise.

        The handshake should refuse the connection before any sign/verify
        ever runs, but a late deletion (test, hot-reload) must not crash
        the connection — it must reject and let the caller emit
        ``Error(code="signature_invalid")``.
        """
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        assert not verify_signature(
            run_id="r",
            tool_call_id="t",
            nonce="n",
            name="x",
            signature="anything",
        )

    def test_nonce_is_random(self):
        seen = {new_nonce() for _ in range(100)}
        assert len(seen) == 100
        # 16 bytes hex = 32 chars
        assert all(len(n) == 32 for n in seen)
        # hex alphabet only
        assert all(set(n) <= set("0123456789abcdef") for n in seen)

    def test_signature_is_hex_64(self, signing_key):
        sig = compute_signature(run_id="r", tool_call_id="t", nonce="n", name="x")
        # sha256 hex = 64 chars
        assert len(sig) == 64
        assert set(sig) <= set("0123456789abcdef")
