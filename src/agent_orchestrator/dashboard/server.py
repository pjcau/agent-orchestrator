"""Dashboard server entrypoint.

Usage:
    python -m agent_orchestrator.dashboard.server
    # or via Docker: docker compose up dashboard
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import uvicorn

from .app import create_dashboard_app
from .events import EventBus


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Orchestrator Dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=5005, help="Bind port")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    parser.add_argument("--no-ssl", action="store_true", help="Disable SSL (HTTP only)")
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

    # SSL: use certs/ if present (self-signed for local, Let's Encrypt for prod)
    ssl_kwargs: dict = {}
    cert_dir = Path("certs")
    if not args.no_ssl and (cert_dir / "cert.pem").exists() and (cert_dir / "key.pem").exists():
        ssl_kwargs["ssl_certfile"] = str(cert_dir / "cert.pem")
        ssl_kwargs["ssl_keyfile"] = str(cert_dir / "key.pem")
        logging.getLogger(__name__).info("SSL enabled (certs/cert.pem)")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info", **ssl_kwargs)


if __name__ == "__main__":
    main()
