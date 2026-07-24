"""Langfuse span exporter — optional OTel sink for LLM-native tracing.

Langfuse (https://langfuse.com) provides a prompt/completion-aware trace UI
with eval scores, prompt versioning, and cost tracking.  This module hooks into
the existing OTel pipeline via a ``SpanExporter`` and is purely additive: the
Tempo OTLP exporter, Prometheus metrics, and Grafana keep working unchanged.

Installation (optional extra)::

    pip install "agent-orchestrator[langfuse]"

Configuration (environment variables):

    LANGFUSE_PUBLIC_KEY   — required to enable the exporter
    LANGFUSE_SECRET_KEY   — required to enable the exporter
    LANGFUSE_HOST         — optional, defaults to https://cloud.langfuse.com

When ``LANGFUSE_PUBLIC_KEY`` or ``LANGFUSE_SECRET_KEY`` is not set the exporter
is silently skipped (no-op).  When the ``langfuse`` package is missing a
warning is logged and the exporter is skipped.
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

_LANGFUSE_SDK_AVAILABLE = False

try:
    from langfuse.decorators import observe as _langfuse_observe  # noqa: F401
    from langfuse.otel import LangfuseExporter as _LangfuseSDKExporter

    _LANGFUSE_SDK_AVAILABLE = True
except ImportError:
    pass

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import ReadableSpan


# ── SpanExporter wrapper ──────────────────────────────────────────────────────


class LangfuseSpanExporter:
    """OTel ``SpanExporter`` that forwards spans to Langfuse.

    The class deliberately does NOT inherit from ``opentelemetry.sdk.trace.export.SpanExporter``
    when the SDK is unavailable.  When the SDK *is* available (``[otel]`` extra
    installed) the class registers itself via a ``BatchSpanProcessor`` on the
    current global ``TracerProvider``.

    This class is meant to be used through :func:`register_langfuse_exporter`,
    not instantiated directly.
    """

    def __init__(
        self,
        public_key: str,
        secret_key: str,
        host: str = "https://cloud.langfuse.com",
    ) -> None:
        self._public_key = public_key
        self._secret_key = secret_key
        self._host = host
        self._inner: object | None = None  # _LangfuseSDKExporter instance

        if _LANGFUSE_SDK_AVAILABLE:
            try:
                self._inner = _LangfuseSDKExporter(  # type: ignore[name-defined]
                    public_key=public_key,
                    secret_key=secret_key,
                    host=host,
                )
            except Exception as exc:  # pragma: no cover
                logger.warning("Langfuse: failed to initialise SDK exporter: %s", exc)

    # --- SpanExporter protocol -------------------------------------------------

    def export(self, spans: Sequence[ReadableSpan]) -> int:
        """Forward spans to the Langfuse backend.

        Returns:
            0 on success (SpanExportResult.SUCCESS), 1 on failure
            (SpanExportResult.FAILURE).  Integers are used so this works even
            when the OTel SDK is not installed and the enum is unavailable.
        """
        if self._inner is None:
            return 0  # no-op, treated as success
        try:
            return self._inner.export(spans)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("Langfuse exporter: export failed: %s", exc)
            return 1  # FAILURE

    def shutdown(self) -> None:
        """Flush and close the underlying SDK exporter."""
        if self._inner is not None:
            try:
                self._inner.shutdown()  # type: ignore[attr-defined]
            except Exception as exc:  # pragma: no cover
                logger.warning("Langfuse exporter: shutdown failed: %s", exc)

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """Flush pending spans within the given timeout."""
        if self._inner is None:
            return True
        try:
            return self._inner.force_flush(timeout_millis)  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover
            logger.warning("Langfuse exporter: force_flush failed: %s", exc)
            return False


# ── registration helper ───────────────────────────────────────────────────────


def register_langfuse_exporter() -> bool:
    """Add a Langfuse ``BatchSpanProcessor`` to the current global TracerProvider.

    This function is idempotent: calling it a second time is a no-op.

    Returns:
        ``True`` if the exporter was registered, ``False`` if it was skipped
        (missing env vars, missing package, or already registered).
    """
    global _registered
    if _registered:
        return False

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")

    if not public_key or not secret_key:
        logger.debug(
            "Langfuse exporter not registered: "
            "LANGFUSE_PUBLIC_KEY and/or LANGFUSE_SECRET_KEY not set."
        )
        return False

    if not _LANGFUSE_SDK_AVAILABLE:
        logger.warning(
            "Langfuse exporter not registered: 'langfuse' package is not installed. "
            "Install it with: pip install 'agent-orchestrator[langfuse]'"
        )
        return False

    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")

    try:
        from opentelemetry import trace as _trace
        from opentelemetry.sdk.trace import TracerProvider as _TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor as _BatchSpanProcessor
    except ImportError:
        logger.warning(
            "Langfuse exporter not registered: opentelemetry-sdk is not installed. "
            "Install it with: pip install 'agent-orchestrator[otel]'"
        )
        return False

    provider = _trace.get_tracer_provider()
    if not isinstance(provider, _TracerProvider):
        logger.warning(
            "Langfuse exporter not registered: no SDK TracerProvider is active. "
            "Call setup_tracing() before register_langfuse_exporter()."
        )
        return False

    exporter = LangfuseSpanExporter(
        public_key=public_key,
        secret_key=secret_key,
        host=host,
    )
    provider.add_span_processor(_BatchSpanProcessor(exporter))
    _registered = True
    logger.info("Langfuse exporter registered (host=%s).", host)
    return True


def _reset_registration() -> None:
    """Reset the registration flag — test helper only."""
    global _registered
    _registered = False
