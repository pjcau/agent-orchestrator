"""Per-provider rate limiting using a sliding window algorithm.

Optionally accelerated by Rust via PyO3 when _agent_orchestrator_rust is installed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# Rust acceleration (optional — falls back to pure Python)
try:
    from _agent_orchestrator_rust import RustRateLimiter as _RustRateLimiter

    _HAS_RUST_RL = True
except ImportError:
    _HAS_RUST_RL = False


@dataclass
class RateLimitConfig:
    requests_per_minute: int
    tokens_per_minute: int
    provider_key: str


@dataclass
class RateLimitStatus:
    provider_key: str
    requests_remaining: int
    tokens_remaining: int
    resets_at: float  # unix timestamp when the oldest entry exits the window
    is_limited: bool


@dataclass
class _ProviderState:
    """Internal sliding-window state for one provider."""

    request_timestamps: list[float] = field(default_factory=list)
    token_timestamps: list[tuple[float, int]] = field(default_factory=list)


class RateLimiter:
    """Token-bucket rate limiter with a 60-second sliding window per provider."""

    _WINDOW = 60.0  # seconds

    def __init__(self, configs: list[RateLimitConfig]) -> None:
        self._configs: dict[str, RateLimitConfig] = {c.provider_key: c for c in configs}
        self._states: dict[str, _ProviderState] = {
            c.provider_key: _ProviderState() for c in configs
        }
        self._rust: _RustRateLimiter | None = None
        if _HAS_RUST_RL:
            rust_configs = [
                (c.provider_key, c.requests_per_minute, c.tokens_per_minute) for c in configs
            ]
            self._rust = _RustRateLimiter(rust_configs)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def acquire(self, provider_key: str, estimated_tokens: int = 0) -> bool:
        """Return True if the request is allowed, False if rate-limited.

        Does NOT record the request — call record_usage() after success.
        """
        if self._rust:
            return self._rust.acquire(provider_key, estimated_tokens)

        config = self._configs.get(provider_key)
        if config is None:
            # Unknown provider — allow by default
            return True

        state = self._states[provider_key]
        now = time.time()
        self._evict(state, now)

        requests_used = len(state.request_timestamps)
        tokens_used = sum(t for _, t in state.token_timestamps)

        if requests_used >= config.requests_per_minute:
            return False
        if estimated_tokens > 0 and tokens_used + estimated_tokens > config.tokens_per_minute:
            return False

        return True

    def record_usage(self, provider_key: str, tokens: int) -> None:
        """Record that a request with the given token count was made now."""
        if self._rust:
            self._rust.record_usage(provider_key, tokens)
            return

        if provider_key not in self._states:
            self._states[provider_key] = _ProviderState()

        now = time.time()
        state = self._states[provider_key]
        state.request_timestamps.append(now)
        if tokens > 0:
            state.token_timestamps.append((now, tokens))

    def get_status(self, provider_key: str) -> RateLimitStatus:
        """Return current rate-limit status for a provider."""
        config = self._configs.get(provider_key)
        state = self._states.get(provider_key)
        now = time.time()

        if config is None or state is None:
            return RateLimitStatus(
                provider_key=provider_key,
                requests_remaining=0,
                tokens_remaining=0,
                resets_at=now,
                is_limited=False,
            )

        self._evict(state, now)

        requests_used = len(state.request_timestamps)
        tokens_used = sum(t for _, t in state.token_timestamps)

        requests_remaining = max(0, config.requests_per_minute - requests_used)
        tokens_remaining = max(0, config.tokens_per_minute - tokens_used)

        # Next reset: when the oldest entry in the window expires
        oldest = self._oldest_timestamp(state)
        resets_at = oldest + self._WINDOW if oldest is not None else now

        is_limited = requests_remaining == 0 or tokens_remaining == 0

        return RateLimitStatus(
            provider_key=provider_key,
            requests_remaining=requests_remaining,
            tokens_remaining=tokens_remaining,
            resets_at=resets_at,
            is_limited=is_limited,
        )

    def reset(self, provider_key: str) -> None:
        """Clear all recorded usage for a provider."""
        if provider_key in self._states:
            self._states[provider_key] = _ProviderState()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _evict(self, state: _ProviderState, now: float) -> None:
        """Remove entries older than the sliding window."""
        cutoff = now - self._WINDOW
        state.request_timestamps = [ts for ts in state.request_timestamps if ts > cutoff]
        state.token_timestamps = [(ts, t) for ts, t in state.token_timestamps if ts > cutoff]

    def _oldest_timestamp(self, state: _ProviderState) -> float | None:
        candidates: list[float] = []
        if state.request_timestamps:
            candidates.append(state.request_timestamps[0])
        if state.token_timestamps:
            candidates.append(state.token_timestamps[0][0])
        return min(candidates) if candidates else None
