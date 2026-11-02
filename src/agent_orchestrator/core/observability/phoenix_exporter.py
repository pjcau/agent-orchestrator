"""Arize Phoenix span exporter — optional OTel sink for LLM-native tracing.

Arize Phoenix (https://phoenix.arize.com) offers an OTel-native LLM trace UI
with prompt/response inspection, hallucination detection, and latency analysis.
It accepts standard OTLP over HTTP so this module wires up an
``OTLPSpanExporter`` pointed at the Phoenix collector endpoint.

Installation (optional extra)::

    pip install "agent-orchestrator[phoenix]"

Configuration (environment variables):

    PHOENIX_COLLECTOR_ENDPOINT — optional, defaults to http://localhost:6006
    PHOENIX_API_KEY            — optional, required only for Arize cloud

When the ``arize-phoenix-otel`` package is missing a warning is logged and the
exporter is silently skipped.  The existing Tempo/Prometheus pipeline is
unaffected in all cases.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

# Track whether we have already registered to avoid double-registration.
_registered: bool = False

# ── optional SDK import ───────────────────────────────────────────────────────

_PHOENIX_SDK_AVAILABLE = False

try:
    # arize-phoenix-otel re-exports the standard OTLPSpanExporter with extra
    # Phoenix-aware headers and semantic conventions.
    from phoenix.otel import register as _phoenix_register  # noqa: F401

    _PHOENIX_SDK_AVAILABLE = True
except ImportError:
    pass

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import ReadableSpan

# Default endpoint — Phoenix local dev server.
_DEFAULT_PHOENIX_ENDPOINT = "http://localhost:6006"


# ── SpanExporter wrapper ──────────────────────────────────────────────────────


class PhoenixSpanExporter:
    """OTel ``SpanExporter`` that forwards spans to an Arize Phoenix collector.

    Phoenix uses the standard OTLP/HTTP protocol so the inner exporter is an
    ``OTLPSpanExporter`` with the Phoenix endpoint and optional API-key header.

    Use :func:`register_phoenix_exporter` rather than instantiating this class
    directly.
    """

    def __init__(
        self,
        endpoint: str = _DEFAULT_PHOENIX_ENDPOINT,
        api_key: str | None = None,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._inner: object | None = None

        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter as _OTLPSpanExporter,
            )
        except ImportError:
            logger.warning(
                "Phoenix exporter: opentelemetry-exporter-otlp-proto-http is not installed."
            )
            return

        headers: dict[str, str] = {}
        if api_key:
            headers["api_key"] = api_key

        otlp_endpoint = f"{self._endpoint}/v1/traces"
        try:
            self._inner = _OTLPSpanExporter(
                endpoint=otlp_endpoint,
                headers=headers if headers else None,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("Phoenix exporter: failed to initialise OTLP exporter: %s", exc)

    # --- SpanExporter protocol -------------------------------------------------

    def export(self, spans: Sequence[ReadableSpan]) -> int:
        """Forward spans to the Phoenix OTLP endpoint.

        Returns:
            0 on success, 1 on failure.
        """
        if self._inner is None:
            return 0
        try:
            return self._inner.export(spans)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("Phoenix exporter: export failed: %s", exc)
            return 1

    def shutdown(self) -> None:
        """Flush and close the underlying OTLP exporter."""
        if self._inner is not None:
            try:
                self._inner.shutdown()  # type: ignore[attr-defined]
            except Exception as exc:  # pragma: no cover
                logger.warning("Phoenix exporter: shutdown failed: %s", exc)

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """Flush pending spans within the given timeout."""
        if self._inner is None:
            return True
        try:
            return self._inner.force_flush(timeout_millis)  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover
            logger.warning("Phoenix exporter: force_flush failed: %s", exc)
            return False


# ── registration helper ───────────────────────────────────────────────────────


def register_phoenix_exporter() -> bool:
    """Add a Phoenix ``BatchSpanProcessor`` to the current global TracerProvider.

    This function is idempotent: calling it a second time is a no-op.

    The exporter is skipped (but not an error) when:
    - ``opentelemetry-sdk`` is not installed
    - No SDK ``TracerProvider`` is active (call ``setup_tracing()`` first)

    Returns:
        ``True`` if the exporter was registered, ``False`` if it was skipped.
    """
    global _registered
    if _registered:
        return False

    endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT", _DEFAULT_PHOENIX_ENDPOINT)
    api_key = os.environ.get("PHOENIX_API_KEY") or None

    try:
        from opentelemetry import trace as _trace
        from opentelemetry.sdk.trace import TracerProvider as _TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor as _BatchSpanProcessor
    except ImportError:
        logger.warning(
            "Phoenix exporter not registered: opentelemetry-sdk is not installed. "
            "Install it with: pip install 'agent-orchestrator[otel]'"
        )
        return False

    provider = _trace.get_tracer_provider()
    if not isinstance(provider, _TracerProvider):
        logger.debug(
            "Phoenix exporter not registered: no SDK TracerProvider is active. "
            "Call setup_tracing() before register_phoenix_exporter()."
        )
        return False

    exporter = PhoenixSpanExporter(endpoint=endpoint, api_key=api_key)
    provider.add_span_processor(_BatchSpanProcessor(exporter))
    _registered = True
    logger.info("Phoenix exporter registered (endpoint=%s).", endpoint)
    return True


def _reset_registration() -> None:
    """Reset the registration flag — test helper only."""
    global _registered
    _registered = False
