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

    def _ensure(self, provider_key: str) -> ProviderHealth:
        if provider_key not in self._health:
            self._health[provider_key] = ProviderHealth(provider_key=provider_key)
            self._latency_samples[provider_key] = deque(maxlen=self._latency_window)
            self._outcome_window[provider_key] = deque(maxlen=self._latency_window)
        return self._health[provider_key]

    def record_success(self, provider_key: str, latency_ms: float) -> None:
        """Log a successful provider call."""
        h = self._ensure(provider_key)
        h.total_requests += 1
        h.consecutive_errors = 0
        h.last_check = time.time()

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
        """True if the provider is considered healthy enough to use."""
        return self._ensure(provider_key).is_available

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
