"""HMAC signing for the agent-host channel.

Each ``tool_call`` issued by the server is bound to a fresh random nonce
and an HMAC-SHA-256 signature over the tuple
``(run_id, tool_call_id, nonce, name)``. The client echoes the same nonce
in ``tool_result`` / ``tool_chunk`` and signs the same tuple — the server
verifies before resolving the pending Future.

Threat model:

* **Cross-WS tool-result injection.** Without signatures, another
  connection holding a valid CLI JWT could POST ``tool_result`` for a
  tool_call_id it never received. The server already routes results to
  the WS that issued the call, but the HMAC adds tamper-evident defence
  in depth and a non-repudiation trail for audit logs.
* **Replay.** ``tool_call_id`` is single-use — once a ``tool_result``
  resolves the pending Future, the call is purged. Replays land on a
  missing-id branch and are rejected.
* **Frame tampering in transit.** Handled by TLS at the transport layer;
  signing is not a substitute.

The signing key is the existing ``JWT_SECRET_KEY`` environment variable
(also used by ``dashboard.auth``). Reusing it keeps secret management to
a single source — no new key to rotate.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets


class SigningKeyMissingError(RuntimeError):
    """Raised when ``JWT_SECRET_KEY`` is unset.

    The agent-host endpoint requires it because the protocol is unusable
    without a stable shared secret: signatures cannot be computed and the
    server cannot prove a tool_result is the legitimate answer to its
    tool_call. Fail-closed rather than degrade silently to unsigned mode.
    """


def _secret_bytes() -> bytes:
    key = os.environ.get("JWT_SECRET_KEY", "")
    if not key:
        raise SigningKeyMissingError(
            "JWT_SECRET_KEY is required for agent-host signing"
        )
    return key.encode("utf-8")


def new_nonce() -> str:
    """16-byte cryptographically-random hex string (32 chars).

    Used once per tool_call; the client must echo it verbatim in the
    matching tool_result / tool_chunk. Reusing the OS CSPRNG keeps the
    project away from custom RNG concerns.
    """
    return secrets.token_hex(16)


def compute_signature(
    *,
    run_id: str,
    tool_call_id: str,
    nonce: str,
    name: str,
) -> str:
    """HMAC-SHA-256 of ``run_id|tool_call_id|nonce|name`` as hex string.

    Pipe-separation is safe because the inputs are server-controlled
    identifiers (UUID hex / opaque names) — no user-supplied content can
    smuggle a pipe into the message. The name is included so an attacker
    who somehow captures a nonce can't repurpose it to a different tool.
    """
    msg = f"{run_id}|{tool_call_id}|{nonce}|{name}".encode("utf-8")
    return hmac.new(_secret_bytes(), msg, hashlib.sha256).hexdigest()


def verify_signature(
    *,
    run_id: str,
    tool_call_id: str,
    nonce: str,
    name: str,
    signature: str,
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
            run_id=run_id, tool_call_id=tool_call_id, nonce=nonce, name=name
        )
    except SigningKeyMissingError:
        # Caller already enforces presence at handshake; treat a late miss
        # as a verification failure (fail-closed) rather than crashing the
        # connection.
        return False
    return hmac.compare_digest(expected, signature)
