"""Optional LLM-native trace exporters for the agent orchestrator.

This package extends the existing OTel pipeline (Tempo, Prometheus, Grafana) with
two additional opt-in sinks:

- :mod:`langfuse_exporter` — Langfuse (prompt/completion pairs, eval scores)
- :mod:`phoenix_exporter`  — Arize Phoenix (LLM-native OTel collector)

Both exporters are purely additive: enabling one does NOT disable Tempo or any
other configured exporter.  Import this package in harness code is safe; the
concrete exporter classes are available here for convenience.

Typical usage (called from dashboard/server.py after setup_tracing())::

    from agent_orchestrator.core.observability import register_optional_exporters
    register_optional_exporters()
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def register_optional_exporters() -> None:
    """Register Langfuse and/or Phoenix exporters when their env vars are set.

    This function is a no-op if neither exporter's required env vars are
    present.  It is safe to call multiple times (idempotent via the internal
    guards inside each exporter module).

    Call this *after* :func:`agent_orchestrator.core.tracing.setup_tracing` so
    that the global TracerProvider is already configured.
    """
    from agent_orchestrator.core.observability.langfuse_exporter import (
        register_langfuse_exporter,
    )
    from agent_orchestrator.core.observability.phoenix_exporter import (
        register_phoenix_exporter,
    )

    register_langfuse_exporter()
    register_phoenix_exporter()


__all__ = ["register_optional_exporters"]
