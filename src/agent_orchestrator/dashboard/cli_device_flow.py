"""Device-flow OAuth state store (RFC 8628).

Used by the Rust ``ago`` CLI's ``ago login --device`` to authenticate a
local device against the dashboard without ever exposing the API key in
the user's terminal history or clipboard.

Flow at a glance::

    CLI                                 Server                     Browser
    ---                                 ------                     -------
    POST /api/cli/v1/auth/device       -> create(device, user)
    <- {device_code, user_code, ...}
    print "Visit URL + paste code"
    poll POST /token (every interval)
    <- 400 authorization_pending
                                                                    open URL
                                       <- GET /auth/device/approve?user_code=...
                                          (auth-gated via JWT session)
                                       approve(user_code, user_info)
                                          -> ephemeral API key
    poll POST /token
    <- 200 {access_token}

State is kept in process memory only. A server restart invalidates pending
flows; that is acceptable for v0.1 — the CLI displays an explicit
``expired_token`` and the user simply re-runs ``ago login --device``.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# RFC 8628 §3.2 reference values; chosen to be safe defaults.
DEFAULT_EXPIRES_IN = 600  # 10 minutes
DEFAULT_INTERVAL = 5  # seconds between polls

# User-code alphabet excludes look-alikes (0/O, 1/I/L) — humans copy it by hand.
_USER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_USER_CODE_GROUPS = 2
_USER_CODE_GROUP_LEN = 4

# Status enum kept as strings to match RFC 8628 error codes verbatim.
STATUS_PENDING = "authorization_pending"
STATUS_APPROVED = "approved"
STATUS_DENIED = "access_denied"
STATUS_EXPIRED = "expired_token"

# Ephemeral API-key prefix — easy to distinguish in logs and audits.
EPHEMERAL_KEY_PREFIX = "ago_eph_"


def _gen_user_code() -> str:
    parts = [
        "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(_USER_CODE_GROUP_LEN))
        for _ in range(_USER_CODE_GROUPS)
    ]
    return "-".join(parts)


def _gen_device_code() -> str:
    return secrets.token_urlsafe(32)


def _gen_ephemeral_key() -> str:
    return EPHEMERAL_KEY_PREFIX + secrets.token_urlsafe(32)


@dataclass
class DeviceFlow:
    device_code: str
    user_code: str
    created_at: float
    expires_at: float
    interval: int
    status: str = STATUS_PENDING
    # Populated on approval:
    access_token: str | None = None
    user_info: dict[str, Any] | None = None
    last_poll_at: float = 0.0

    def is_expired(self, now: float) -> bool:
        return now >= self.expires_at

    def public_dict(self, verification_uri: str) -> dict[str, Any]:
        """The payload returned by ``POST /api/cli/v1/auth/device``."""
        return {
            "device_code": self.device_code,
            "user_code": self.user_code,
            "verification_uri": verification_uri,
            "verification_uri_complete": f"{verification_uri}?user_code={self.user_code}",
            "expires_in": int(self.expires_at - self.created_at),
            "interval": self.interval,
        }


class DeviceFlowStore:
    """Thread-safe in-process store of pending and approved device flows.

    Concurrent CLI logins are isolated by random ``device_code`` / ``user_code``
    pairs; the lock only serializes mutations so the index by ``user_code``
    stays consistent with the index by ``device_code``.
    """

    def __init__(self) -> None:
        self._by_device: dict[str, DeviceFlow] = {}
        self._by_user: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        expires_in: int = DEFAULT_EXPIRES_IN,
        interval: int = DEFAULT_INTERVAL,
    ) -> DeviceFlow:
        now = time.time()
        # Retry user-code generation in the rare collision case.
        for _ in range(10):
            user_code = _gen_user_code()
            if user_code not in self._by_user:
                break
        else:  # pragma: no cover — alphabet is large enough
            raise RuntimeError("could not generate a unique user_code")
        flow = DeviceFlow(
            device_code=_gen_device_code(),
            user_code=user_code,
            created_at=now,
            expires_at=now + expires_in,
            interval=interval,
        )
        async with self._lock:
            self._by_device[flow.device_code] = flow
            self._by_user[flow.user_code] = flow.device_code
        return flow

    async def lookup_by_user_code(self, user_code: str) -> DeviceFlow | None:
        async with self._lock:
            device_code = self._by_user.get(user_code.upper())
            return self._by_device.get(device_code) if device_code else None

    async def lookup_by_device_code(self, device_code: str) -> DeviceFlow | None:
        async with self._lock:
            return self._by_device.get(device_code)

    async def approve(self, user_code: str, user_info: dict[str, Any]) -> tuple[DeviceFlow, str]:
        """Mark approved and return (flow, access_token).

        Raises ``KeyError`` if the user_code is unknown or has already been
        consumed (denied/approved/expired). The caller is responsible for
        returning the appropriate HTTP error to the browser.
        """
        async with self._lock:
            device_code = self._by_user.get(user_code.upper())
            if not device_code:
                raise KeyError("user_code not found")
            flow = self._by_device[device_code]
            if flow.is_expired(time.time()):
                flow.status = STATUS_EXPIRED
                raise KeyError("user_code expired")
            if flow.status != STATUS_PENDING:
                raise KeyError(f"user_code already {flow.status}")
            token = _gen_ephemeral_key()
            flow.status = STATUS_APPROVED
            flow.access_token = token
            flow.user_info = dict(user_info)
            return flow, token

    async def deny(self, user_code: str) -> DeviceFlow:
        async with self._lock:
            device_code = self._by_user.get(user_code.upper())
            if not device_code:
                raise KeyError("user_code not found")
            flow = self._by_device[device_code]
            if flow.status == STATUS_PENDING:
                flow.status = STATUS_DENIED
            return flow

    async def consume_token(self, device_code: str) -> DeviceFlow | None:
        """Mark a device_code as used and return the flow snapshot.

        After a successful poll the device_code MUST not be reusable — the
        access_token has been delivered. This method clears the access_token
        from the store while keeping the row for the brief moment so the
        token endpoint can echo it.
        """
        async with self._lock:
            flow = self._by_device.get(device_code)
            if flow is None:
                return None
            if flow.status != STATUS_APPROVED or flow.access_token is None:
                return flow
            # Capture token, then remove the flow so the same device_code is
            # not reusable. The user_code is also released.
            self._by_user.pop(flow.user_code, None)
            self._by_device.pop(device_code, None)
            return flow

    async def cleanup(self) -> int:
        """Remove expired entries. Returns the number removed."""
        now = time.time()
        removed = 0
        async with self._lock:
            stale_device_codes = [dc for dc, f in self._by_device.items() if f.is_expired(now)]
            for dc in stale_device_codes:
                flow = self._by_device.pop(dc)
                self._by_user.pop(flow.user_code, None)
                removed += 1
        if removed:
            logger.debug("device-flow store: cleaned %d expired entries", removed)
        return removed

    # Diagnostics-only — never expose this over HTTP.
    def _debug_size(self) -> int:
        return len(self._by_device)


# ---------------------------------------------------------------------------
# Allowed user-code charset / normalization
# ---------------------------------------------------------------------------


def normalize_user_code(raw: str) -> str | None:
    """Strip whitespace + dashes and split into uppercase groups.

    Returns ``None`` if the result does not match ``XXXX-XXXX`` of allowed
    characters. The user-facing form is dash-separated; we accept both
    dashed and undashed input so the user can paste either.
    """
    cleaned = "".join(c for c in raw.upper() if c in _USER_CODE_ALPHABET)
    expected = _USER_CODE_GROUPS * _USER_CODE_GROUP_LEN
    if len(cleaned) != expected:
        return None
    chunks = [
        cleaned[i : i + _USER_CODE_GROUP_LEN] for i in range(0, expected, _USER_CODE_GROUP_LEN)
    ]
    return "-".join(chunks)
