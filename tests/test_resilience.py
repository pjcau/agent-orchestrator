"""Tests for core/resilience.py — retry policy and circuit breaker."""

from __future__ import annotations

import pytest

from agent_orchestrator.core.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    RetryPolicy,
    resilient_call,
)


class _FakeClock:
    """Monotonic clock we can advance manually for circuit breaker tests."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_retry_policy_delay_grows_exponentially():
    policy = RetryPolicy(initial_delay=1.0, multiplier=2.0, max_delay=100.0, jitter=0.0)
    assert policy.delay_for(1) == 1.0
    assert policy.delay_for(2) == 2.0
    assert policy.delay_for(3) == 4.0
    assert policy.delay_for(4) == 8.0


def test_retry_policy_capped_by_max_delay():
    policy = RetryPolicy(initial_delay=1.0, multiplier=10.0, max_delay=5.0, jitter=0.0)
    assert policy.delay_for(10) == 5.0


def test_retry_policy_jitter_is_non_negative():
    policy = RetryPolicy(initial_delay=1.0, multiplier=1.0, jitter=0.5)
    for _ in range(20):
        d = policy.delay_for(1)
        assert d >= 1.0
        assert d <= 1.5 + 1e-9


@pytest.mark.asyncio
async def test_resilient_call_success_first_try():
    calls = {"n": 0}

    async def func() -> str:
        calls["n"] += 1
        return "ok"

    result = await resilient_call(func, retry=RetryPolicy(max_attempts=3))
    assert result == "ok"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_resilient_call_retries_then_succeeds():
    calls = {"n": 0}

    async def func() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        return "ok"

    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    result = await resilient_call(
        func,
        retry=RetryPolicy(max_attempts=5, initial_delay=0.01, jitter=0.0),
        sleep=fake_sleep,
    )
    assert result == "ok"
    assert calls["n"] == 3
    assert len(sleeps) == 2  # slept before attempts 2 and 3


@pytest.mark.asyncio
async def test_resilient_call_exhausts_attempts():
    async def func() -> None:
        raise ConnectionError("down")

    async def fake_sleep(d: float) -> None:
        pass

    with pytest.raises(ConnectionError):
        await resilient_call(
            func,
            retry=RetryPolicy(max_attempts=3, initial_delay=0.001, jitter=0.0),
            sleep=fake_sleep,
        )


@pytest.mark.asyncio
async def test_resilient_call_non_retryable_raises_immediately():
    calls = {"n": 0}

    async def func() -> None:
        calls["n"] += 1
        raise ValueError("bad input")  # non-retryable by default

    with pytest.raises(ValueError):
        await resilient_call(func, retry=RetryPolicy(max_attempts=5))
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_threshold(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr("agent_orchestrator.core.resilience.time.monotonic", clock)
    breaker = CircuitBreaker(failure_threshold=3, reset_timeout=10.0)

    for _ in range(3):
        await breaker.before_call()
        await breaker.on_failure()

    assert breaker.state == CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        await breaker.before_call()


@pytest.mark.asyncio
async def test_circuit_breaker_half_opens_after_timeout(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr("agent_orchestrator.core.resilience.time.monotonic", clock)
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout=5.0)

    await breaker.before_call()
    await breaker.on_failure()
    assert breaker.state == CircuitState.OPEN

    clock.now = 10.0  # past reset_timeout
    await breaker.before_call()
    assert breaker.state == CircuitState.HALF_OPEN

    await breaker.on_success()
    assert breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_circuit_breaker_reopens_on_half_open_failure(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr("agent_orchestrator.core.resilience.time.monotonic", clock)
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout=5.0)

    await breaker.before_call()
    await breaker.on_failure()
    clock.now = 10.0

    await breaker.before_call()  # transitions to HALF_OPEN
    await breaker.on_failure()
    assert breaker.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_resilient_call_with_breaker_open_is_not_retried():
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout=999.0)

    async def func() -> None:
        raise ConnectionError("boom")

    async def fake_sleep(d: float) -> None:
        pass

    # First call opens the breaker.
    with pytest.raises(ConnectionError):
        await resilient_call(
            func,
            retry=RetryPolicy(max_attempts=1, initial_delay=0.001, jitter=0.0),
            breaker=breaker,
            sleep=fake_sleep,
        )
    assert breaker.state == CircuitState.OPEN

    # Subsequent call fails fast with CircuitOpenError (not ConnectionError).
    # CircuitOpenError is non-retryable by default so we get 1 attempt only.
    with pytest.raises(CircuitOpenError):
        await resilient_call(
            func,
            retry=RetryPolicy(max_attempts=5, initial_delay=0.001, jitter=0.0),
            breaker=breaker,
            sleep=fake_sleep,
        )


@pytest.mark.asyncio
async def test_circuit_breaker_success_resets_failures(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr("agent_orchestrator.core.resilience.time.monotonic", clock)
    breaker = CircuitBreaker(failure_threshold=3, reset_timeout=5.0)

    await breaker.before_call()
    await breaker.on_failure()
    await breaker.before_call()
    await breaker.on_failure()
    assert breaker.state == CircuitState.CLOSED

    await breaker.before_call()
    await breaker.on_success()

    # Need 3 consecutive failures again to open.
    for _ in range(2):
        await breaker.before_call()
        await breaker.on_failure()
    assert breaker.state == CircuitState.CLOSED
