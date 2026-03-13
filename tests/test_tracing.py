"""Tests for the OpenTelemetry tracing module.

All tests must pass whether or not the `otel` optional dependencies are
installed.  The suite validates:
- No-op behaviour when OTel packages are absent (simulated via monkeypatch)
- setup_tracing() returns a usable tracer (real or no-op)
- get_tracer() lazy-initialises and caches the singleton
- @traced decorator is a transparent pass-through for async functions
- instrument_fastapi() does not raise even when instrumentation is unavailable
"""

from __future__ import annotations

import pytest

import agent_orchestrator.core.tracing as tracing_mod
from agent_orchestrator.core.tracing import (
    _NoOpSpan,
    _NoOpTracer,
    get_tracer,
    instrument_fastapi,
    setup_tracing,
    traced,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _reset_tracer_singleton() -> None:
    """Reset the module-level _tracer so each test starts clean."""
    tracing_mod._tracer = None


# ── _NoOpSpan ─────────────────────────────────────────────────────────────────


class TestNoOpSpan:
    def test_set_attribute_is_silent(self):
        span = _NoOpSpan()
        span.set_attribute("key", "value")  # must not raise

    def test_record_exception_is_silent(self):
        span = _NoOpSpan()
        span.record_exception(ValueError("boom"))  # must not raise

    def test_set_status_is_silent(self):
        span = _NoOpSpan()
        span.set_status("ERROR", "something went wrong")

    def test_context_manager_protocol(self):
        span = _NoOpSpan()
        with span as s:
            assert s is span


# ── _NoOpTracer ───────────────────────────────────────────────────────────────


class TestNoOpTracer:
    def test_start_as_current_span_returns_noop_span(self):
        t = _NoOpTracer()
        span = t.start_as_current_span("my-span")
        assert isinstance(span, _NoOpSpan)

    def test_start_span_returns_noop_span(self):
        t = _NoOpTracer()
        span = t.start_span("my-span")
        assert isinstance(span, _NoOpSpan)

    def test_usable_as_context_manager(self):
        t = _NoOpTracer()
        with t.start_as_current_span("op") as span:
            span.set_attribute("k", 1)


# ── setup_tracing ─────────────────────────────────────────────────────────────


class TestSetupTracing:
    def setup_method(self):
        _reset_tracer_singleton()

    def test_returns_something(self):
        result = setup_tracing("test-service")
        assert result is not None

    def test_no_endpoint_does_not_raise(self):
        # No endpoint, no exporter — should still succeed
        setup_tracing("test-service", otlp_endpoint="")

    def test_env_var_fallback(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        setup_tracing("test-service")  # must not raise

    def test_sets_module_tracer_singleton(self):
        setup_tracing("test-service")
        assert tracing_mod._tracer is not None

    def test_otel_unavailable_returns_noop(self, monkeypatch):
        # Simulate OTel packages not installed
        monkeypatch.setattr(tracing_mod, "_OTEL_AVAILABLE", False)
        result = setup_tracing("test-service")
        assert isinstance(result, _NoOpTracer)


# ── get_tracer ────────────────────────────────────────────────────────────────


class TestGetTracer:
    def setup_method(self):
        _reset_tracer_singleton()

    def test_returns_tracer_on_first_call(self):
        t = get_tracer()
        assert t is not None

    def test_returns_same_instance_on_repeated_calls(self):
        t1 = get_tracer()
        t2 = get_tracer()
        assert t1 is t2

    def test_noop_when_otel_unavailable(self, monkeypatch):
        monkeypatch.setattr(tracing_mod, "_OTEL_AVAILABLE", False)
        t = get_tracer()
        assert isinstance(t, _NoOpTracer)


# ── instrument_fastapi ────────────────────────────────────────────────────────


class TestInstrumentFastapi:
    def test_no_crash_when_instrumentation_unavailable(self, monkeypatch):
        monkeypatch.setattr(tracing_mod, "_FASTAPI_INSTR_AVAILABLE", False)
        monkeypatch.setattr(tracing_mod, "_OTEL_AVAILABLE", False)
        instrument_fastapi(object())  # must not raise

    def test_no_crash_with_none_app(self, monkeypatch):
        monkeypatch.setattr(tracing_mod, "_FASTAPI_INSTR_AVAILABLE", False)
        instrument_fastapi(None)  # must not raise


# ── @traced decorator ─────────────────────────────────────────────────────────


class TestTracedDecorator:
    def setup_method(self):
        _reset_tracer_singleton()

    @pytest.mark.asyncio
    async def test_passthrough_when_noop(self, monkeypatch):
        monkeypatch.setattr(tracing_mod, "_OTEL_AVAILABLE", False)
        _reset_tracer_singleton()

        @traced("test.span")
        async def my_func(x: int) -> int:
            return x * 2

        result = await my_func(21)
        assert result == 42

    @pytest.mark.asyncio
    async def test_preserves_return_value(self, monkeypatch):
        monkeypatch.setattr(tracing_mod, "_OTEL_AVAILABLE", False)
        _reset_tracer_singleton()

        @traced()
        async def greet(name: str) -> str:
            return f"hello {name}"

        assert await greet("world") == "hello world"

    @pytest.mark.asyncio
    async def test_propagates_exception(self, monkeypatch):
        monkeypatch.setattr(tracing_mod, "_OTEL_AVAILABLE", False)
        _reset_tracer_singleton()

        @traced("fail.span")
        async def explode() -> None:
            raise RuntimeError("intentional")

        with pytest.raises(RuntimeError, match="intentional"):
            await explode()

    @pytest.mark.asyncio
    async def test_with_attributes(self, monkeypatch):
        monkeypatch.setattr(tracing_mod, "_OTEL_AVAILABLE", False)
        _reset_tracer_singleton()

        @traced("attr.span", attributes={"agent": "backend", "version": "1"})
        async def work() -> str:
            return "done"

        assert await work() == "done"

    @pytest.mark.asyncio
    async def test_with_real_noop_tracer(self):
        """End-to-end: traced() with a _NoOpTracer installed as singleton."""
        tracing_mod._tracer = _NoOpTracer()

        @traced("real.noop.span", attributes={"x": "y"})
        async def task() -> int:
            return 7

        assert await task() == 7

    def test_preserves_function_metadata(self):
        @traced("meta.span")
        async def documented_func() -> None:
            """My docstring."""

        assert documented_func.__name__ == "documented_func"
        assert documented_func.__doc__ == "My docstring."
