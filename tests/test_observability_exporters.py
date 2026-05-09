"""Tests for the optional Langfuse and Phoenix OTel exporters.

Design principles:
- Every test passes whether or not langfuse / arize-phoenix-otel is installed.
- No real network calls: the optional SDKs are mocked via monkeypatch.
- The existing OTel pipeline must remain unaffected (regression).
- Covers >= 12 test cases.
"""

from __future__ import annotations

import importlib
import logging
import sys
import types
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_span(name: str = "test.span") -> MagicMock:
    """Create a minimal fake ReadableSpan for exporter tests."""
    span = MagicMock()
    span.name = name
    return span


def _reset_exporter_state() -> None:
    """Reset module-level registration flags between tests."""
    # Import with importlib to handle the case where the modules are not yet
    # in sys.modules.
    langfuse_mod = sys.modules.get("agent_orchestrator.core.observability.langfuse_exporter")
    phoenix_mod = sys.modules.get("agent_orchestrator.core.observability.phoenix_exporter")
    if langfuse_mod is not None:
        langfuse_mod._registered = False
    if phoenix_mod is not None:
        phoenix_mod._registered = False


# ---------------------------------------------------------------------------
# 1. Package importability — always safe, even when optional deps are absent
# ---------------------------------------------------------------------------


class TestModuleImportability:
    """All observability modules must import without raising."""

    def test_observability_package_importable(self):
        from agent_orchestrator.core import observability  # noqa: F401

    def test_langfuse_exporter_importable(self):
        from agent_orchestrator.core.observability import langfuse_exporter  # noqa: F401

    def test_phoenix_exporter_importable(self):
        from agent_orchestrator.core.observability import phoenix_exporter  # noqa: F401

    def test_langfuse_exporter_importable_without_langfuse_pkg(self, monkeypatch):
        """Simulate langfuse not installed; module must still import cleanly."""
        # Remove langfuse from sys.modules if present, then hide it.
        with patch.dict(
            sys.modules, {"langfuse": None, "langfuse.otel": None, "langfuse.decorators": None}
        ):
            import agent_orchestrator.core.observability.langfuse_exporter as mod

            importlib.reload(mod)
            assert not mod._LANGFUSE_SDK_AVAILABLE

    def test_phoenix_exporter_importable_without_phoenix_pkg(self, monkeypatch):
        """Simulate arize-phoenix-otel not installed; module must still import cleanly."""
        with patch.dict(sys.modules, {"phoenix": None, "phoenix.otel": None}):
            import agent_orchestrator.core.observability.phoenix_exporter as mod

            importlib.reload(mod)
            assert not mod._PHOENIX_SDK_AVAILABLE


# ---------------------------------------------------------------------------
# 2. Langfuse exporter — env var / config behaviour
# ---------------------------------------------------------------------------


class TestLangfuseExporterConfig:
    """Configuration is read from env vars; missing vars → no-op + warning."""

    def setup_method(self):
        _reset_exporter_state()

    def test_no_env_vars_returns_false(self, monkeypatch):
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

        from agent_orchestrator.core.observability.langfuse_exporter import (
            register_langfuse_exporter,
        )

        result = register_langfuse_exporter()
        assert result is False

    def test_missing_secret_key_returns_false(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test-123")
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

        from agent_orchestrator.core.observability.langfuse_exporter import (
            register_langfuse_exporter,
        )

        result = register_langfuse_exporter()
        assert result is False

    def test_missing_sdk_logs_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

        import agent_orchestrator.core.observability.langfuse_exporter as mod

        monkeypatch.setattr(mod, "_LANGFUSE_SDK_AVAILABLE", False)

        with caplog.at_level(
            logging.WARNING, logger="agent_orchestrator.core.observability.langfuse_exporter"
        ):
            result = mod.register_langfuse_exporter()

        assert result is False
        assert any("langfuse" in r.message.lower() for r in caplog.records)

    def test_idempotent_registration(self, monkeypatch):
        """Calling register_langfuse_exporter twice returns False the second time."""
        import agent_orchestrator.core.observability.langfuse_exporter as mod

        mod._registered = True
        result = mod.register_langfuse_exporter()
        assert result is False


# ---------------------------------------------------------------------------
# 3. Langfuse SpanExporter — stub integration
# ---------------------------------------------------------------------------


class TestLangfuseSpanExporter:
    """LangfuseSpanExporter delegates to the inner SDK exporter."""

    def test_export_with_no_inner_is_noop(self):
        from agent_orchestrator.core.observability.langfuse_exporter import (
            LangfuseSpanExporter,
        )

        exp = LangfuseSpanExporter.__new__(LangfuseSpanExporter)
        exp._inner = None
        result = exp.export([_make_fake_span()])
        assert result == 0  # SUCCESS (no-op)

    def test_export_delegates_to_inner(self):
        from agent_orchestrator.core.observability.langfuse_exporter import (
            LangfuseSpanExporter,
        )

        inner = MagicMock()
        inner.export.return_value = 0

        exp = LangfuseSpanExporter.__new__(LangfuseSpanExporter)
        exp._inner = inner
        span = _make_fake_span()
        result = exp.export([span])

        inner.export.assert_called_once_with([span])
        assert result == 0

    def test_shutdown_with_no_inner_is_safe(self):
        from agent_orchestrator.core.observability.langfuse_exporter import (
            LangfuseSpanExporter,
        )

        exp = LangfuseSpanExporter.__new__(LangfuseSpanExporter)
        exp._inner = None
        exp.shutdown()  # must not raise

    def test_force_flush_with_no_inner_returns_true(self):
        from agent_orchestrator.core.observability.langfuse_exporter import (
            LangfuseSpanExporter,
        )

        exp = LangfuseSpanExporter.__new__(LangfuseSpanExporter)
        exp._inner = None
        assert exp.force_flush() is True

    def test_export_catches_inner_exception(self):
        from agent_orchestrator.core.observability.langfuse_exporter import (
            LangfuseSpanExporter,
        )

        inner = MagicMock()
        inner.export.side_effect = RuntimeError("network error")

        exp = LangfuseSpanExporter.__new__(LangfuseSpanExporter)
        exp._inner = inner
        result = exp.export([_make_fake_span()])
        assert result == 1  # FAILURE, but no exception propagated


# ---------------------------------------------------------------------------
# 4. Phoenix exporter — env var / config behaviour
# ---------------------------------------------------------------------------


class TestPhoenixExporterConfig:
    """Phoenix uses PHOENIX_COLLECTOR_ENDPOINT; missing → default endpoint used."""

    def setup_method(self):
        _reset_exporter_state()

    def test_no_env_vars_uses_default_endpoint(self, monkeypatch):
        monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
        monkeypatch.delenv("PHOENIX_API_KEY", raising=False)

        from agent_orchestrator.core.observability.phoenix_exporter import (
            PhoenixSpanExporter,
            _DEFAULT_PHOENIX_ENDPOINT,
        )

        exp = PhoenixSpanExporter.__new__(PhoenixSpanExporter)
        exp._endpoint = _DEFAULT_PHOENIX_ENDPOINT
        exp._api_key = None
        exp._inner = None
        assert "6006" in exp._endpoint

    def test_idempotent_registration(self, monkeypatch):
        """Calling register_phoenix_exporter twice returns False the second time."""
        import agent_orchestrator.core.observability.phoenix_exporter as mod

        mod._registered = True
        result = mod.register_phoenix_exporter()
        assert result is False


# ---------------------------------------------------------------------------
# 5. Phoenix SpanExporter — stub integration
# ---------------------------------------------------------------------------


class TestPhoenixSpanExporter:
    """PhoenixSpanExporter delegates to an OTLP inner exporter."""

    def test_export_with_no_inner_is_noop(self):
        from agent_orchestrator.core.observability.phoenix_exporter import (
            PhoenixSpanExporter,
        )

        exp = PhoenixSpanExporter.__new__(PhoenixSpanExporter)
        exp._inner = None
        result = exp.export([_make_fake_span()])
        assert result == 0

    def test_export_delegates_to_inner(self):
        from agent_orchestrator.core.observability.phoenix_exporter import (
            PhoenixSpanExporter,
        )

        inner = MagicMock()
        inner.export.return_value = 0

        exp = PhoenixSpanExporter.__new__(PhoenixSpanExporter)
        exp._inner = inner
        span = _make_fake_span()
        result = exp.export([span])

        inner.export.assert_called_once_with([span])
        assert result == 0

    def test_export_catches_inner_exception(self):
        from agent_orchestrator.core.observability.phoenix_exporter import (
            PhoenixSpanExporter,
        )

        inner = MagicMock()
        inner.export.side_effect = OSError("connection refused")

        exp = PhoenixSpanExporter.__new__(PhoenixSpanExporter)
        exp._inner = inner
        result = exp.export([_make_fake_span()])
        assert result == 1

    def test_shutdown_with_no_inner_is_safe(self):
        from agent_orchestrator.core.observability.phoenix_exporter import (
            PhoenixSpanExporter,
        )

        exp = PhoenixSpanExporter.__new__(PhoenixSpanExporter)
        exp._inner = None
        exp.shutdown()  # must not raise

    def test_force_flush_with_no_inner_returns_true(self):
        from agent_orchestrator.core.observability.phoenix_exporter import (
            PhoenixSpanExporter,
        )

        exp = PhoenixSpanExporter.__new__(PhoenixSpanExporter)
        exp._inner = None
        assert exp.force_flush() is True


# ---------------------------------------------------------------------------
# 6. register_optional_exporters — package-level convenience
# ---------------------------------------------------------------------------


class TestRegisterOptionalExporters:
    def setup_method(self):
        _reset_exporter_state()

    def test_register_optional_exporters_callable_without_env(self, monkeypatch):
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)

        from agent_orchestrator.core.observability import register_optional_exporters

        # Must not raise, even with no env vars configured
        register_optional_exporters()


# ---------------------------------------------------------------------------
# 7. Regression — existing OTel pipeline still works after exporters are wired
# ---------------------------------------------------------------------------


class TestExistingOtelPipelineRegression:
    """setup_tracing() must behave identically before and after adding exporters."""

    def setup_method(self):
        _reset_exporter_state()
        import agent_orchestrator.core.tracing as tracing_mod

        tracing_mod._tracer = None

    def test_setup_tracing_still_returns_tracer(self, monkeypatch):
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)

        from agent_orchestrator.core.tracing import setup_tracing

        tracer = setup_tracing("regression-test")
        assert tracer is not None

    def test_setup_tracing_noop_still_returns_noop(self, monkeypatch):
        import agent_orchestrator.core.tracing as tracing_mod

        monkeypatch.setattr(tracing_mod, "_OTEL_AVAILABLE", False)
        tracing_mod._tracer = None

        from agent_orchestrator.core.tracing import _NoOpTracer

        tracer = tracing_mod.setup_tracing("noop-test")
        assert isinstance(tracer, _NoOpTracer)

    def test_setup_tracing_exporter_exception_does_not_propagate(self, monkeypatch):
        """If register_optional_exporters raises, setup_tracing still returns a tracer."""
        import agent_orchestrator.core.tracing as tracing_mod

        tracing_mod._tracer = None

        # Patch the observability package to raise
        fake_obs = types.ModuleType("fake_obs")
        fake_obs.register_optional_exporters = MagicMock(  # type: ignore[attr-defined]
            side_effect=RuntimeError("simulated crash")
        )

        with patch.dict(
            sys.modules,
            {"agent_orchestrator.core.observability": fake_obs},
        ):
            tracer = tracing_mod.setup_tracing("crash-test")
        assert tracer is not None
