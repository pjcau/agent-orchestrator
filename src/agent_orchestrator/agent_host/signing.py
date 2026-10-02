"""HMAC signing for the agent-host channel.

Each ``tool_call`` issued by the server is bound to a fresh random nonce
and an HMAC-SHA-256 signature over the tuple
``(run_id, tool_call_id, nonce, name)``. The client echoes the same nonce
in ``tool_result`` / ``tool_chunk`` and signs the same tuple — the server
verifies before resolving the pending Future.

Key model
---------

The signing key is **per-session**, minted by the server on accept and
shipped to the client inside the :class:`agent_host.Ack` frame
(``signing_key`` field, 32-byte CSPRNG, hex-encoded). The server stores
the same bytes in :class:`PendingToolCallsRegistry` for the lifetime of
the connection; both sides use the session key for sign and verify.

The dashboard's stable ``JWT_SECRET_KEY`` is **not** sent to the client
(it's a server secret) but is retained as a fallback when no explicit
``key`` is passed — useful for unit tests and for server-internal
signatures that never leave the process.

Threat model
------------

* **Cross-WS tool-result injection.** Without signatures, another
  connection holding a valid CLI JWT could POST ``tool_result`` for a
  tool_call_id it never received. The server already routes results to
  the WS that issued the call; the per-session HMAC adds tamper-evident
  defence in depth and an audit trail.
* **Replay.** ``tool_call_id`` is single-use — once a ``tool_result``
  resolves the pending Future, the call is purged. Replays land on the
  missing-id branch and are rejected.
* **Frame tampering in transit.** Handled by TLS at the transport layer;
  signing is not a substitute.
* **Session-key compromise.** The key dies when the connection dies. A
  client that leaks its key only compromises that one chat session.
  Re-connecting mints a fresh key.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets


class SigningKeyMissingError(RuntimeError):
    """Raised when no signing key can be resolved.

    Triggered when ``compute_signature``/``verify_signature`` is called
    without an explicit ``key`` *and* ``JWT_SECRET_KEY`` is also unset.
    Fail-closed rather than degrade silently to unsigned mode.
    """


def _secret_bytes() -> bytes:
    """Fallback key bytes from ``JWT_SECRET_KEY`` env.

    Used only when the caller does not pass an explicit ``key=`` argument
    (tests and server-internal signatures).
    """
    key = os.environ.get("JWT_SECRET_KEY", "")
    if not key:
        raise SigningKeyMissingError(
            "JWT_SECRET_KEY is required for agent-host signing (or pass an explicit session key)"
        )
    return key.encode("utf-8")


def new_nonce() -> str:
    """16-byte cryptographically-random hex string (32 chars).

    Used once per tool_call; the client must echo it verbatim in the
    matching tool_result / tool_chunk. Reusing the OS CSPRNG keeps the
    project away from custom RNG concerns.
    """
    return secrets.token_hex(16)


def new_session_key() -> bytes:
    """32-byte random session signing key.

    Minted by the server on every agent-host WS accept, shipped to the
    client inside the :class:`agent_host.Ack` frame, and used for HMAC
    sign/verify by both peers for the lifetime of that connection.
    """
    return secrets.token_bytes(32)


def compute_signature(
    *,
    run_id: str,
    tool_call_id: str,
    nonce: str,
    name: str,
    key: bytes | None = None,
) -> str:
    """HMAC-SHA-256 of ``run_id|tool_call_id|nonce|name`` as hex string.

    Pipe-separation is safe because the inputs are server-controlled
    identifiers (UUID hex / opaque names) — no user-supplied content can
    smuggle a pipe into the message. The name is included so an attacker
    who somehow captures a nonce can't repurpose it to a different tool.

    Pass ``key`` to use a session-scoped key (the recommended path —
    the server mints it per connection and ships it inside the ACK
    frame). Omit ``key`` to fall back to ``JWT_SECRET_KEY`` — used by
    server-internal flows and unit tests.
    """
    secret = key if key is not None else _secret_bytes()
    msg = f"{run_id}|{tool_call_id}|{nonce}|{name}".encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def verify_signature(
    *,
    run_id: str,
    tool_call_id: str,
    nonce: str,
    name: str,
    signature: str,
    key: bytes | None = None,
) -> bool:
    """Constant-time verification.

    Returns ``False`` on any mismatch and on signatures that fail length
    parsing — never raises. ``hmac.compare_digest`` is mandatory here:
    a naive ``==`` would leak a timing oracle on the first differing byte.
    """
    if not signature:
        return False
    try:
        expected = compute_signature(
            run_id=run_id,
            tool_call_id=tool_call_id,
            nonce=nonce,
            name=name,
            key=key,
        )
    except SigningKeyMissingError:
        # Caller already enforces presence at handshake; treat a late miss
        # as a verification failure (fail-closed) rather than crashing the
        # connection.
        return False
    return hmac.compare_digest(expected, signature)
