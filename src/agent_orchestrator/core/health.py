"""Provider health monitoring — tracks latency, errors, and availability."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class ProviderHealth:
    provider_key: str
    is_available: bool = True
    avg_latency_ms: float = 0.0
    error_rate: float = 0.0  # 0-1
    last_check: float = field(default_factory=time.time)
    consecutive_errors: int = 0
    total_requests: int = 0
    total_errors: int = 0


@dataclass
class QuotaConfig:
    """Per-provider quota over a sliding window (scout finding #64).

    None disables that dimension. ``window_seconds`` defaults to a day —
    matching typical provider daily quotas.
    """

    max_calls: int | None = None
    max_tokens: int | None = None
    window_seconds: float = 86_400.0


@dataclass
class QuotaStatus:
    """The 'fuel gauge': how much of a provider's quota window is left."""

    provider_key: str
    calls_used: int
    tokens_used: int
    calls_remaining: int | None  # None when no call quota configured
    tokens_remaining: int | None
    remaining_fraction: float  # min across configured dimensions; 1.0 = full tank
    exhausted: bool


class HealthMonitor:
    """Track provider health using a sliding window for latency and a rolling error rate.

    Config:
        max_consecutive_errors: mark unavailable after this many errors in a row
        error_rate_threshold: mark unavailable if error rate exceeds this (0-1)
        latency_window: number of recent latency samples to average over
    """

    def __init__(
        self,
        max_consecutive_errors: int = 5,
        error_rate_threshold: float = 0.5,
        latency_window: int = 100,
    ) -> None:
        self._max_consecutive_errors = max_consecutive_errors
        self._error_rate_threshold = error_rate_threshold
        self._latency_window = latency_window

        # provider_key -> ProviderHealth
        self._health: dict[str, ProviderHealth] = {}
        # provider_key -> deque of recent latency samples (ms)
        self._latency_samples: dict[str, deque[float]] = {}
        # provider_key -> deque of recent outcomes (True=success, False=error)
        self._outcome_window: dict[str, deque[bool]] = {}
        # provider_key -> QuotaConfig (fuel gauge)
        self._quotas: dict[str, QuotaConfig] = {}
        # provider_key -> deque of (timestamp, tokens) usage events
        self._usage_events: dict[str, deque[tuple[float, int]]] = {}

    def _ensure(self, provider_key: str) -> ProviderHealth:
        if provider_key not in self._health:
            self._health[provider_key] = ProviderHealth(provider_key=provider_key)
            self._latency_samples[provider_key] = deque(maxlen=self._latency_window)
            self._outcome_window[provider_key] = deque(maxlen=self._latency_window)
        return self._health[provider_key]

    def record_success(self, provider_key: str, latency_ms: float, tokens: int = 0) -> None:
        """Log a successful provider call (optionally with its token usage)."""
        h = self._ensure(provider_key)
        h.total_requests += 1
        h.consecutive_errors = 0
        h.last_check = time.time()
        if provider_key in self._quotas:
            self._usage_events.setdefault(provider_key, deque()).append((time.time(), tokens))

        self._latency_samples[provider_key].append(latency_ms)
        self._outcome_window[provider_key].append(True)

        samples = self._latency_samples[provider_key]
        h.avg_latency_ms = sum(samples) / len(samples)

        outcomes = self._outcome_window[provider_key]
        h.error_rate = outcomes.count(False) / len(outcomes)
        h.is_available = self._compute_availability(h)

    def record_error(self, provider_key: str, error: str) -> None:
        """Log a failed provider call."""
        h = self._ensure(provider_key)
        h.total_requests += 1
        h.total_errors += 1
        h.consecutive_errors += 1
        h.last_check = time.time()

        self._outcome_window[provider_key].append(False)

        outcomes = self._outcome_window[provider_key]
        h.error_rate = outcomes.count(False) / len(outcomes)
        h.is_available = self._compute_availability(h)

    def _compute_availability(self, h: ProviderHealth) -> bool:
        if h.consecutive_errors >= self._max_consecutive_errors:
            return False
        if h.error_rate > self._error_rate_threshold:
            return False
        return True

    def get_health(self, provider_key: str) -> ProviderHealth:
        """Return current health for a provider (initialises if unseen)."""
        return self._ensure(provider_key)

    def get_all_health(self) -> dict[str, ProviderHealth]:
        """Return health for every tracked provider."""
        return dict(self._health)

    def is_available(self, provider_key: str) -> bool:
        """True if the provider is healthy enough to use and not quota-exhausted."""
        if provider_key in self._quotas and self.get_quota_status(provider_key).exhausted:
            return False
        return self._ensure(provider_key).is_available

    # --- Quota gauge (scout finding #64) -------------------------------

    def set_quota(self, provider_key: str, quota: QuotaConfig) -> None:
        """Configure the sliding-window quota for a provider."""
        self._quotas[provider_key] = quota
        self._usage_events.setdefault(provider_key, deque())

    def get_quota_status(self, provider_key: str) -> QuotaStatus:
        """Return the provider's fuel gauge; a full tank when no quota is set."""
        quota = self._quotas.get(provider_key)
        events = self._usage_events.get(provider_key, deque())
        if quota is not None:
            cutoff = time.time() - quota.window_seconds
            while events and events[0][0] < cutoff:
                events.popleft()
        calls_used = len(events)
        tokens_used = sum(t for _, t in events)

        fractions: list[float] = []
        calls_remaining = tokens_remaining = None
        if quota is not None and quota.max_calls is not None:
            calls_remaining = max(0, quota.max_calls - calls_used)
            fractions.append(calls_remaining / quota.max_calls if quota.max_calls else 0.0)
        if quota is not None and quota.max_tokens is not None:
            tokens_remaining = max(0, quota.max_tokens - tokens_used)
            fractions.append(tokens_remaining / quota.max_tokens if quota.max_tokens else 0.0)

        remaining_fraction = min(fractions) if fractions else 1.0
        return QuotaStatus(
            provider_key=provider_key,
            calls_used=calls_used,
            tokens_used=tokens_used,
            calls_remaining=calls_remaining,
            tokens_remaining=tokens_remaining,
            remaining_fraction=remaining_fraction,
            exhausted=bool(fractions) and remaining_fraction <= 0.0,
        )

    def get_best_provider(self, provider_keys: list[str]) -> str | None:
        """Return the healthiest provider from the given list.

        Selection criteria (in order):
        1. Must be available
        2. Lowest error rate
        3. Lowest average latency
        """
        available = [k for k in provider_keys if self.is_available(k)]
        if not available:
            return None

        def _score(key: str) -> tuple[float, float]:
            h = self._ensure(key)
            return (h.error_rate, h.avg_latency_ms)

        return min(available, key=_score)
