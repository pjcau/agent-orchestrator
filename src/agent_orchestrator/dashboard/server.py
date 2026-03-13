"""Dashboard server entrypoint.

Usage:
    python -m agent_orchestrator.dashboard.server
    # or via Docker: docker compose up dashboard

HTTPS is always enabled. Self-signed certs are auto-generated if missing.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
from pathlib import Path

import uvicorn

from .app import create_dashboard_app
from .events import EventBus

logger = logging.getLogger(__name__)


def _ensure_certs(cert_dir: Path) -> None:
    """Generate self-signed certs if they don't exist."""
    cert_file = cert_dir / "cert.pem"
    key_file = cert_dir / "key.pem"
    if cert_file.exists() and key_file.exists():
        return

    cert_dir.mkdir(parents=True, exist_ok=True)
    logger.warning("SSL certs not found — generating self-signed certificate for localhost")
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key_file),
            "-out",
            str(cert_file),
            "-days",
            "365",
            "-nodes",
            "-subj",
            "/CN=localhost",
        ],
        check=True,
        capture_output=True,
    )
    logger.info("Self-signed cert created at %s", cert_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Orchestrator Dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=5005, help="Bind port")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    bus = EventBus.get()

    # Instrument core classes if available (optional, for agent/orchestrator monitoring)
    try:
        from .instrument import instrument_all

        instrument_all(bus)
    except Exception:
        pass  # Instrumentation is optional

    app = create_dashboard_app(bus)

    # Initialize OpenTelemetry tracing (no-op if otel packages not installed)
    try:
        from ..core.tracing import instrument_fastapi, setup_tracing

        setup_tracing()
        instrument_fastapi(app)
        logger.info("OpenTelemetry tracing initialized")
    except Exception:
        pass  # Tracing is optional

    # HTTPS always — auto-generate self-signed certs if missing
    cert_dir = Path("certs")
    _ensure_certs(cert_dir)

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        ssl_certfile=str(cert_dir / "cert.pem"),
        ssl_keyfile=str(cert_dir / "key.pem"),
    )


if __name__ == "__main__":
    main()
