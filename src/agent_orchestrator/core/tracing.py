"""OpenTelemetry tracing integration for the agent orchestrator.

All OTel imports are wrapped in try/except ImportError so this module is
always importable even when the `otel` optional dependency group is not
installed.  When the packages are absent every public function is a no-op
and the module-level tracer silently does nothing.

Opt-in installation:
    pip install "agent-orchestrator[otel]"

Configuration (environment variables):
    OTEL_EXPORTER_OTLP_ENDPOINT  — e.g. http://localhost:4318
                                    Empty or unset → tracing disabled (no-op)
    OTEL_SERVICE_NAME            — overrides the default service name
"""

from __future__ import annotations

import functools
import os
from typing import Any

# ── optional OTel imports ────────────────────────────────────────────────────

_OTEL_AVAILABLE = False

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    _OTEL_AVAILABLE = True
except ImportError:
    pass

try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    _OTLP_AVAILABLE = True
except ImportError:
    _OTLP_AVAILABLE = False

try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    _FASTAPI_INSTR_AVAILABLE = True
except ImportError:
    _FASTAPI_INSTR_AVAILABLE = False

# ── module-level singleton ───────────────────────────────────────────────────

_tracer: Any = None  # real Tracer or None (resolved lazily via get_tracer())

# ── public API ───────────────────────────────────────────────────────────────


def setup_tracing(
    service_name: str = "agent-orchestrator",
    otlp_endpoint: str | None = None,
) -> Any:
    """Configure a TracerProvider and return a tracer instance.

    When the OTel SDK is not installed or no endpoint is provided, returns a
    lightweight no-op tracer so callers need not guard every usage site.

    Args:
        service_name: Logical name reported to the backend (e.g. Jaeger, Tempo).
        otlp_endpoint: OTLP HTTP endpoint URL.  Falls back to the environment
            variable ``OTEL_EXPORTER_OTLP_ENDPOINT``.  When empty or None the
            provider is configured without an exporter (no data is sent).

    Returns:
        A ``opentelemetry.trace.Tracer`` when the SDK is available, otherwise
        a ``_NoOpTracer`` that silently ignores all calls.
    """
    global _tracer

    endpoint = otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")

    if not _OTEL_AVAILABLE:
        _tracer = _NoOpTracer()
        return _tracer

    resource = Resource(attributes={SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    if endpoint and _OTLP_AVAILABLE:
        exporter = OTLPSpanExporter(endpoint=endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)
    return _tracer


def instrument_fastapi(app: Any) -> None:
    """Attach OTel auto-instrumentation to a FastAPI application.

    No-op when ``opentelemetry-instrumentation-fastapi`` is not installed or
    when a tracer provider has not been configured via :func:`setup_tracing`.

    Args:
        app: A ``fastapi.FastAPI`` instance.
    """
    if not _FASTAPI_INSTR_AVAILABLE or not _OTEL_AVAILABLE:
        return
    FastAPIInstrumentor.instrument_app(app)


def get_tracer() -> Any:
    """Return the module-level tracer singleton.

    Initialises with default settings on first call if :func:`setup_tracing`
    has not been called explicitly.

    Returns:
        The configured tracer or a no-op tracer when OTel is unavailable.
    """
    global _tracer
    if _tracer is None:
        setup_tracing()
    return _tracer


def traced(span_name: str | None = None, attributes: dict[str, Any] | None = None):
    """Decorator that wraps an async function in an OTel span.

    When OTel is not installed the decorator is a transparent pass-through with
    zero overhead.

    Args:
        span_name: Span name reported to the backend.  Defaults to the
            decorated function's qualified name.
        attributes: Static key-value attributes attached to every span.

    Usage::

        @traced("agent.execute", attributes={"agent": "backend"})
        async def execute(task: str) -> str:
            ...
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            t = get_tracer()
            if isinstance(t, _NoOpTracer):
                return await func(*args, **kwargs)

            name = span_name or func.__qualname__
            with t.start_as_current_span(name) as span:
                if attributes:
                    for k, v in attributes.items():
                        span.set_attribute(k, v)
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    if _OTEL_AVAILABLE:
                        span.record_exception(exc)
                        span.set_status(trace.StatusCode.ERROR, str(exc))
                    raise

        return wrapper

    return decorator


# ── no-op fallback ───────────────────────────────────────────────────────────


class _NoOpSpan:
    """Minimal span that satisfies the context-manager protocol."""

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: ARG002
        pass

    def record_exception(self, exc: Exception) -> None:  # noqa: ARG002
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        pass

    def end(self) -> None:
        pass

    def __enter__(self) -> "_NoOpSpan":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class _NoOpTracer:
    """No-op tracer returned when OTel packages are not installed."""

    def start_as_current_span(self, name: str, **kwargs: Any) -> _NoOpSpan:  # noqa: ARG002
        return _NoOpSpan()

    def start_span(self, name: str, **kwargs: Any) -> _NoOpSpan:  # noqa: ARG002
        return _NoOpSpan()
