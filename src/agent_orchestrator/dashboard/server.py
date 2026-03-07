"""Dashboard server entrypoint.

Usage:
    python -m agent_orchestrator.dashboard.server
    # or via Docker: docker compose up dashboard
"""

from __future__ import annotations

import argparse
import logging
import uvicorn

from .app import create_dashboard_app
from .events import EventBus


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

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
