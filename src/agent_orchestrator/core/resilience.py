"""Retry with exponential backoff and circuit breaker for provider calls.

Standalone, side-effect free module. Opt-in: providers call
``resilient_call()`` (or wrap themselves) rather than being forced through it.

Two primitives:

* ``RetryPolicy`` — exponential backoff with jitter. Decides when to retry.
* ``CircuitBreaker`` — fails fast after N consecutive failures. Half-opens
  after a cooldown to probe recovery.

Use them together via :func:`resilient_call` or independently.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised when a call is rejected because the circuit is open."""


@dataclass
class RetryPolicy:
    """Exponential backoff retry policy."""

    max_attempts: int = 3
    initial_delay: float = 0.5
    max_delay: float = 30.0
    multiplier: float = 2.0
    jitter: float = 0.1
    retryable: Callable[[BaseException], bool] = field(
        default=lambda exc: not isinstance(exc, (ValueError, TypeError, CircuitOpenError))
    )

    def delay_for(self, attempt: int) -> float:
        """Compute delay before the *next* attempt (1-indexed)."""
        base = min(self.initial_delay * (self.multiplier ** (attempt - 1)), self.max_delay)
        if self.jitter > 0:
            base += random.uniform(0, self.jitter * base)
        return base


@dataclass
class CircuitBreaker:
    """Per-resource circuit breaker.

    ``failure_threshold`` consecutive failures open the circuit. After
    ``reset_timeout`` seconds, the next call is allowed through in
    ``HALF_OPEN`` state; success closes the circuit, failure re-opens it.
    """

    failure_threshold: int = 5
    reset_timeout: float = 30.0
    half_open_max_calls: int = 1

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failures: int = field(default=0, init=False)
    _opened_at: float = field(default=0.0, init=False)
    _half_open_in_flight: int = field(default=0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def state(self) -> CircuitState:
        return self._state

    async def before_call(self) -> None:
        """Raise :class:`CircuitOpenError` if the circuit forbids this call."""
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if time.monotonic() - self._opened_at >= self.reset_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_in_flight = 0
                else:
                    raise CircuitOpenError("circuit breaker is open")
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_in_flight >= self.half_open_max_calls:
                    raise CircuitOpenError("circuit breaker half-open quota exhausted")
                self._half_open_in_flight += 1

    async def on_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._half_open_in_flight = max(0, self._half_open_in_flight - 1)
            self._failures = 0
            self._state = CircuitState.CLOSED

    async def on_failure(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_in_flight = 0
                return
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()


async def resilient_call(
    func: Callable[[], Awaitable[T]],
    retry: RetryPolicy | None = None,
    breaker: CircuitBreaker | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run ``func`` with retry and optional circuit breaker.

    The breaker (if any) is checked before every attempt. Non-retryable
    exceptions propagate immediately; retryable ones are retried up to
    ``retry.max_attempts`` with exponential backoff.
    """
    policy = retry or RetryPolicy()
    last_exc: BaseException | None = None

    for attempt in range(1, policy.max_attempts + 1):
        if breaker is not None:
            await breaker.before_call()
        try:
            result = await func()
        except BaseException as exc:
            last_exc = exc
            if breaker is not None:
                await breaker.on_failure()
            if not policy.retryable(exc) or attempt == policy.max_attempts:
                raise
            await sleep(policy.delay_for(attempt))
            continue
        if breaker is not None:
            await breaker.on_success()
        return result

    assert last_exc is not None
    raise last_exc
