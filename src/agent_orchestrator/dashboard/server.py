"""Dashboard server entrypoint.

Usage:
    python -m agent_orchestrator.dashboard.server
    # or via Docker: docker compose up dashboard

    # Split-process mode (gateway only):
    python -m agent_orchestrator.dashboard.server --mode gateway --port 5006

    # Split-process mode (runtime only):
    python -m agent_orchestrator.dashboard.server --mode runtime --port 5007

HTTPS is always enabled. Self-signed certs are auto-generated if missing.
"""

from __future__ import annotations

import argparse
import logging
import os
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


def _create_shared_state(bus: EventBus | None = None):
    """Instrument core classes and return (bus, app) ready for uvicorn."""
    bus = bus or EventBus.get()

    # Instrument core classes if available (optional, for agent/orchestrator monitoring)
    try:
        from .instrument import instrument_all

        instrument_all(bus)
    except Exception:
        pass  # Instrumentation is optional

    return bus


def run_gateway(host: str = "0.0.0.0", port: int = 5006) -> None:
    """Run only the gateway API (no agent runtime).

    Creates a minimal FastAPI app that includes only the gateway_router,
    health_router, and metrics_router. Suitable for horizontal scaling of
    the management plane independently of the compute-heavy runtime.
    """
    logging.basicConfig(level=logging.WARNING)

    bus = _create_shared_state()
    app = _create_gateway_only_app(bus)

    # Initialize OpenTelemetry tracing (no-op if otel packages not installed)
    try:
        from ..core.tracing import instrument_fastapi, setup_tracing

        setup_tracing()
        instrument_fastapi(app)
        logger.info("OpenTelemetry tracing initialized")
    except Exception:
        pass

    cert_dir = Path("certs")
    _ensure_certs(cert_dir)

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        ssl_certfile=str(cert_dir / "cert.pem"),
        ssl_keyfile=str(cert_dir / "key.pem"),
    )


def run_runtime(host: str = "0.0.0.0", port: int = 5007) -> None:
    """Run only the agent runtime (no management API).

    Creates a minimal FastAPI app that includes only the runtime_router.
    Suitable for horizontal scaling of the compute-heavy execution plane
    independently of the management API.
    """
    logging.basicConfig(level=logging.WARNING)

    bus = _create_shared_state()
    app = _create_runtime_only_app(bus)

    try:
        from ..core.tracing import instrument_fastapi, setup_tracing

        setup_tracing()
        instrument_fastapi(app)
        logger.info("OpenTelemetry tracing initialized")
    except Exception:
        pass

    cert_dir = Path("certs")
    _ensure_certs(cert_dir)

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        ssl_certfile=str(cert_dir / "cert.pem"),
        ssl_keyfile=str(cert_dir / "key.pem"),
    )


def _create_gateway_only_app(bus: EventBus):
    """Build a minimal FastAPI app exposing only gateway endpoints."""

    from fastapi import FastAPI

    from ..core.conversation import ConversationManager, SummarizationConfig, SummarizationTrigger
    from ..core.checkpoint import InMemoryCheckpointer
    from ..core.checkpoint_postgres import PostgresCheckpointer
    from ..core.memory_filter import MemoryFilter
    from ..core.store import InMemoryStore
    from ..core.mcp_client import MCPClientManager
    from .job_logger import JobLogger
    from .usage_db import UsageDB
    from .alert_webhook import AlertHandler
    from .auth import APIKeyMiddleware
    from .oauth_routes import router as oauth_router
    from .user_store import setup_db as setup_user_db
    from .sse import RunManager
    from .gateway_api import gateway_router, health_router, metrics_router
    from starlette.middleware.cors import CORSMiddleware

    app = FastAPI(title="Agent Orchestrator Gateway", version="0.2.0")

    api_keys_raw = os.environ.get("DASHBOARD_API_KEYS", "")
    api_keys = [k.strip() for k in api_keys_raw.split(",") if k.strip()] if api_keys_raw else []
    app.add_middleware(APIKeyMiddleware, api_keys=api_keys or None)

    allowed_origins = os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
    allowed_origins = [o.strip() for o in allowed_origins if o.strip()]
    if not allowed_origins:
        base = os.environ.get("BASE_URL", "https://localhost:5006")
        allowed_origins = [base]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["X-API-Key", "Content-Type", "Authorization"],
    )
    app.include_router(oauth_router)

    job_logger = JobLogger()
    usage_db = UsageDB()
    alert_handler = AlertHandler(usage_db=usage_db)
    frontend_error_count: list[int] = [0]
    ws_api_keys: set = set(api_keys) if api_keys else set()
    active_ws: dict = {}
    active_jobs: dict = {}

    _db_url = os.environ.get("DATABASE_URL", "")
    _conv_checkpointer = (
        PostgresCheckpointer(_db_url, table_name="conversation_checkpoints")
        if _db_url
        else InMemoryCheckpointer()
    )
    _memory_filter = MemoryFilter()
    store_holder: list = [None]
    if not _db_url:
        store_holder[0] = InMemoryStore(memory_filter=_memory_filter)

    _summarization_config = SummarizationConfig(
        trigger=SummarizationTrigger.MESSAGE_COUNT,
        threshold=50,
        retain_last=10,
        enabled=True,
    )

    async def _llm_summarize(messages: list[dict]) -> str:
        lines = [f"{m.get('role', '?')}: {str(m.get('content', ''))[:200]}" for m in messages]
        return "Summary of earlier conversation:\n" + "\n".join(lines[:20])

    conv_manager = ConversationManager(
        checkpointer=_conv_checkpointer,
        summarization_config=_summarization_config,
        summarize_func=_llm_summarize,
    )

    sandbox_manager = None  # Gateway does not need sandbox

    run_manager = RunManager(event_bus=bus)
    mcp_client_manager = MCPClientManager()

    app.state.bus = bus
    app.state.usage_db = usage_db
    app.state.job_logger = job_logger
    app.state.conv_manager = conv_manager
    app.state.alert_handler = alert_handler
    app.state.frontend_error_count = frontend_error_count
    app.state.active_ws = active_ws
    app.state.active_jobs = active_jobs
    app.state.ws_api_keys = ws_api_keys
    app.state.store_holder = store_holder
    app.state.sandbox_manager = sandbox_manager
    app.state.run_manager = run_manager
    app.state.mcp_client_manager = mcp_client_manager

    @app.on_event("startup")
    async def _startup():
        await usage_db.setup()
        await setup_user_db()
        if hasattr(_conv_checkpointer, "setup"):
            try:
                await _conv_checkpointer.setup()
            except Exception:
                pass
        if not _db_url:
            app.state.store = store_holder[0]
        else:
            app.state.store = store_holder[0]

    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(gateway_router)
    return app


def _create_runtime_only_app(bus: EventBus):
    """Build a minimal FastAPI app exposing only runtime endpoints."""

    from fastapi import FastAPI
    from ..core.conversation import ConversationManager, SummarizationConfig, SummarizationTrigger
    from ..core.checkpoint import InMemoryCheckpointer
    from ..core.checkpoint_postgres import PostgresCheckpointer
    from ..core.sandbox import SandboxConfig, SandboxType
    from .job_logger import JobLogger
    from .sandbox_manager import SandboxManager
    from .usage_db import UsageDB
    from .auth import APIKeyMiddleware
    from .agent_runtime_router import runtime_router
    from starlette.middleware.cors import CORSMiddleware

    app = FastAPI(title="Agent Orchestrator Runtime", version="0.2.0")

    api_keys_raw = os.environ.get("DASHBOARD_API_KEYS", "")
    api_keys = [k.strip() for k in api_keys_raw.split(",") if k.strip()] if api_keys_raw else []
    app.add_middleware(APIKeyMiddleware, api_keys=api_keys or None)

    allowed_origins = os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
    allowed_origins = [o.strip() for o in allowed_origins if o.strip()]
    if not allowed_origins:
        base = os.environ.get("BASE_URL", "https://localhost:5007")
        allowed_origins = [base]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["X-API-Key", "Content-Type", "Authorization"],
    )

    job_logger = JobLogger()
    usage_db = UsageDB()
    active_ws: dict = {}
    active_jobs: dict = {}
    ws_api_keys: set = set(api_keys) if api_keys else set()

    _db_url = os.environ.get("DATABASE_URL", "")
    _conv_checkpointer = (
        PostgresCheckpointer(_db_url, table_name="conversation_checkpoints")
        if _db_url
        else InMemoryCheckpointer()
    )

    _summarization_config = SummarizationConfig(
        trigger=SummarizationTrigger.MESSAGE_COUNT,
        threshold=50,
        retain_last=10,
        enabled=True,
    )

    async def _llm_summarize(messages: list[dict]) -> str:
        lines = [f"{m.get('role', '?')}: {str(m.get('content', ''))[:200]}" for m in messages]
        return "Summary of earlier conversation:\n" + "\n".join(lines[:20])

    conv_manager = ConversationManager(
        checkpointer=_conv_checkpointer,
        summarization_config=_summarization_config,
        summarize_func=_llm_summarize,
    )

    _sandbox_enabled = os.environ.get("SANDBOX_ENABLED", "false").lower() == "true"
    _sandbox_config = SandboxConfig(
        type=SandboxType.LOCAL,
        timeout_seconds=30,
        memory_limit="512m",
        writable_paths=["/workspace"],
    )
    sandbox_manager: SandboxManager | None = (
        SandboxManager(default_config=_sandbox_config) if _sandbox_enabled else None
    )

    store_holder: list = [None]

    # Stub state entries that runtime router doesn't need but may reference
    # via app.state (defensive)
    app.state.bus = bus
    app.state.usage_db = usage_db
    app.state.job_logger = job_logger
    app.state.conv_manager = conv_manager
    app.state.active_ws = active_ws
    app.state.active_jobs = active_jobs
    app.state.ws_api_keys = ws_api_keys
    app.state.sandbox_manager = sandbox_manager
    app.state.store_holder = store_holder
    app.state.run_manager = None
    app.state.mcp_client_manager = None

    @app.on_event("startup")
    async def _startup():
        await usage_db.setup()
        if hasattr(_conv_checkpointer, "setup"):
            try:
                await _conv_checkpointer.setup()
            except Exception:
                pass

    @app.on_event("shutdown")
    async def _shutdown():
        if sandbox_manager is not None:
            await sandbox_manager.cleanup_all()

    app.include_router(runtime_router)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Orchestrator Dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=5005, help="Bind port")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    parser.add_argument(
        "--mode",
        choices=["full", "gateway", "runtime"],
        default="full",
        help="Process mode: full (default), gateway (management API only), "
        "runtime (agent execution only)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    bus = EventBus.get()

    # Instrument core classes if available (optional, for agent/orchestrator monitoring)
    try:
        from .instrument import instrument_all

        instrument_all(bus)
    except Exception:
        pass  # Instrumentation is optional

    if args.mode == "gateway":
        app = _create_gateway_only_app(bus)
    elif args.mode == "runtime":
        app = _create_runtime_only_app(bus)
    else:
        app = create_dashboard_app(bus)

    # Initialize OpenTelemetry tracing (no-op if otel packages not installed)
    try:
        from ..core.tracing import instrument_fastapi, setup_tracing

        setup_tracing()
        instrument_fastapi(app)
        logger.info("OpenTelemetry tracing initialized")
    except Exception:
        pass  # Tracing is optional

    # HTTPS always -- auto-generate self-signed certs if missing
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
