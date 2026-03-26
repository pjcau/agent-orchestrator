"""Dashboard application — composes gateway and runtime routers.

Single-process mode: includes both routers.
Split-process mode: each router runs as a separate FastAPI app
    (see server.py run_gateway() / run_runtime()).

Serves:
- WebSocket at /ws for real-time events
- WebSocket at /ws/stream for streaming LLM responses
- REST APIs for models, agents, prompt, files, conversations, presets
- Static files for the dashboard UI
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from ..core.conversation import (
    ConversationManager,
    SummarizationConfig,
    SummarizationTrigger,
)
from ..core.checkpoint import InMemoryCheckpointer
from ..core.checkpoint_postgres import PostgresCheckpointer
from ..core.memory_filter import MemoryFilter
from ..core.store import InMemoryStore
from ..core.sandbox import SandboxConfig, SandboxType
from .events import EventBus
from .job_logger import JobLogger
from .sandbox_manager import SandboxManager
from .auth import APIKeyMiddleware
from .oauth_routes import router as oauth_router
from .user_store import setup_db as setup_user_db
from .usage_db import UsageDB
from .alert_webhook import AlertHandler
from .gateway_api import gateway_router, health_router, metrics_router
from .agent_runtime_router import runtime_router
from .sse import RunManager

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Module-level counter kept for backward compatibility — tests that import
# _frontend_error_count directly from app will get the counter used by the
# default (first) created app instance.  Each app instance still owns its own
# mutable cell on app.state.frontend_error_count.
_frontend_error_count: list[int] = [0]


# ── Backward-compatible module-level helpers (originally in monolithic app.py) ──


def _sanitize_log(value: str) -> str:
    """Sanitize user-controlled values for safe logging (prevent log injection)."""
    return value.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


# Allowed Ollama URL prefixes (SSRF protection)
_OLLAMA_ALLOWED_PREFIXES = (
    "http://localhost",
    "http://127.0.0.1",
    "http://host.docker.internal",
    "http://ollama",  # Docker service name
)


def _get_ollama_url() -> str:
    """Get and validate the Ollama base URL (SSRF-safe)."""
    import os as _os

    url = _os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    if not any(url.startswith(p) for p in _OLLAMA_ALLOWED_PREFIXES):
        raise ValueError(
            f"OLLAMA_BASE_URL must start with one of {_OLLAMA_ALLOWED_PREFIXES}, got: {url}"
        )
    return url


def create_dashboard_app(event_bus: EventBus | None = None) -> FastAPI:
    """Create and return the composed dashboard FastAPI application.

    Sets up all shared state on ``app.state`` so both gateway_router and
    runtime_router can access it via ``request.app.state``.
    """
    bus = event_bus or EventBus.get()

    app = FastAPI(title="Agent Orchestrator Dashboard", version="0.2.0")

    # -----------------------------------------------------------------------
    # Middleware order: Starlette runs LAST-added FIRST.
    # We want: Request -> CORS -> Auth -> Route handler
    # So register Auth FIRST, then CORS (CORS wraps Auth).
    # -----------------------------------------------------------------------

    # 1) Auth middleware (innermost -- runs after CORS)
    api_keys_raw = os.environ.get("DASHBOARD_API_KEYS", "")
    api_keys = [k.strip() for k in api_keys_raw.split(",") if k.strip()] if api_keys_raw else []
    app.add_middleware(APIKeyMiddleware, api_keys=api_keys or None)

    # 2) CORS middleware (outermost -- runs first, handles OPTIONS preflight)
    from starlette.middleware.cors import CORSMiddleware

    allowed_origins = os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
    allowed_origins = [o.strip() for o in allowed_origins if o.strip()]
    if not allowed_origins:
        base = os.environ.get("BASE_URL", "https://localhost:5005")
        allowed_origins = [base]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["X-API-Key", "Content-Type", "Authorization"],
    )

    # Starlette session middleware (required for authlib OAuth2 state)
    try:
        from starlette.middleware.sessions import SessionMiddleware

        session_secret = os.environ.get("JWT_SECRET_KEY", "")
        if not session_secret:
            import secrets

            logger.warning(
                "JWT_SECRET_KEY not set. Sessions will use a random key (not persistent across restarts)."
            )
            session_secret = secrets.token_hex(32)
        app.add_middleware(SessionMiddleware, secret_key=session_secret, same_site="lax")
    except ImportError:
        pass  # itsdangerous not installed, sessions disabled

    # OAuth2 routes (Google/GitHub login)
    app.include_router(oauth_router)

    # -----------------------------------------------------------------------
    # Shared state -- accessible from any router via request.app.state
    # -----------------------------------------------------------------------

    # Job logger -- persists all task results to jobs/<session_id>/
    job_logger = JobLogger()

    # Usage DB -- persistent cumulative stats (Postgres)
    usage_db = UsageDB()

    # Alert handler -- receives Grafana webhook payloads and creates GitHub issues
    alert_handler = AlertHandler(usage_db=usage_db)

    # Frontend error counter for Prometheus (shared mutable cell).
    # Uses the module-level _frontend_error_count so tests importing it directly
    # from this module observe the same counter as the app instance.
    frontend_error_count: list[int] = _frontend_error_count

    # Active WebSocket connections -- only one per path at a time
    active_ws: dict = {}

    # Active background team jobs
    active_jobs: dict = {}

    # WebSocket API key set (pre-computed for fast auth checks)
    ws_api_keys: set = set(api_keys) if api_keys else set()

    # Conversation memory -- thread-based multi-turn for agents, graphs, prompts
    _db_url = os.environ.get("DATABASE_URL", "")
    _conv_checkpointer = (
        PostgresCheckpointer(_db_url, table_name="conversation_checkpoints")
        if _db_url
        else InMemoryCheckpointer()
    )

    # Cross-thread persistent store -- agent long-term memory and shared facts.
    _memory_filter = MemoryFilter()
    store_holder: list = [None]  # mutable cell so _startup can update it

    if not _db_url:
        store_holder[0] = InMemoryStore(memory_filter=_memory_filter)

    # Summarization: fire at 50 messages, keep the 10 most recent verbatim.
    _summarization_config = SummarizationConfig(
        trigger=SummarizationTrigger.MESSAGE_COUNT,
        threshold=50,
        retain_last=10,
        enabled=True,
    )

    async def _llm_summarize(messages: list[dict]) -> str:
        """Summarise older conversation messages using lightweight concatenation.

        In production this could delegate to the active provider; this fallback
        preserves key context without an extra LLM call.
        """
        lines = [f"{m.get('role', '?')}: {str(m.get('content', ''))[:200]}" for m in messages]
        return "Summary of earlier conversation:\n" + "\n".join(lines[:20])

    conv_manager = ConversationManager(
        checkpointer=_conv_checkpointer,
        summarization_config=_summarization_config,
        summarize_func=_llm_summarize,
    )

    # Sandbox manager -- session-scoped isolated execution environments.
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

    # SSE run manager
    run_manager = RunManager(event_bus=bus)

    # MCP client manager for external MCP server connections
    from ..core.mcp_client import MCPClientManager

    mcp_client_manager = MCPClientManager()

    # Expose all shared state on app.state so routers can access it
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
    # Set store eagerly so tests that don't trigger startup can access it.
    # Startup will replace it with PostgresStore when DATABASE_URL is set.
    app.state.store = store_holder[0]
    app.state.sandbox_manager = sandbox_manager
    app.state.run_manager = run_manager
    app.state.mcp_client_manager = mcp_client_manager

    # -----------------------------------------------------------------------
    # Startup / shutdown lifecycle
    # -----------------------------------------------------------------------

    @app.on_event("startup")
    async def _startup():
        await usage_db.setup()
        await setup_user_db()
        # Initialize conversation checkpointer (creates table if Postgres)
        if hasattr(_conv_checkpointer, "setup"):
            try:
                await _conv_checkpointer.setup()
            except Exception:
                logger.warning("Conversation checkpointer setup failed, falling back to in-memory")
        # Initialise persistent store and expose as app.state.store
        if _db_url:
            try:
                import asyncpg  # type: ignore[import]
                from ..core.store_postgres import PostgresStore as _PostgresStore

                _pg_pool = await asyncpg.create_pool(_db_url, min_size=1, max_size=5)
                _pg_st = _PostgresStore(_pg_pool, memory_filter=_memory_filter)
                await _pg_st.ensure_table()
                store_holder[0] = _pg_st
                app.state.store = _pg_st
                logger.info("PostgresStore initialised for cross-thread memory")
            except Exception:
                logger.warning(
                    "PostgresStore setup failed -- falling back to InMemoryStore",
                    exc_info=True,
                )
                _fallback = InMemoryStore(memory_filter=_memory_filter)
                store_holder[0] = _fallback
                app.state.store = _fallback
        else:
            app.state.store = store_holder[0]

        if _sandbox_enabled:
            logger.info("Sandbox system enabled (SANDBOX_ENABLED=true)")

    @app.on_event("shutdown")
    async def _shutdown():
        if sandbox_manager is not None:
            await sandbox_manager.cleanup_all()
            logger.info("Sandbox manager: all sessions cleaned up")

    # -----------------------------------------------------------------------
    # Static files and root HTML (React frontend preferred, vanilla JS fallback)
    # -----------------------------------------------------------------------

    react_dist = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"

    if react_dist.is_dir() and (react_dist / "index.html").exists():
        logger.info("Serving React frontend from %s", react_dist)
        app.mount("/assets", StaticFiles(directory=str(react_dist / "assets")), name="assets")
        # Keep legacy static mount for backward compatibility
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/", response_class=HTMLResponse)
        async def index():
            return HTMLResponse(content=(react_dist / "index.html").read_text())
    else:
        logger.info("React frontend not found, serving vanilla JS from %s", STATIC_DIR)
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/", response_class=HTMLResponse)
        async def index():
            index_file = STATIC_DIR / "index.html"
            return HTMLResponse(content=index_file.read_text())

    # -----------------------------------------------------------------------
    # Include modular routers
    # -----------------------------------------------------------------------

    # Health check and Prometheus metrics (no /api prefix)
    app.include_router(health_router)
    app.include_router(metrics_router)

    # REST management endpoints (/api/*)
    app.include_router(gateway_router)

    # Agent execution, WebSocket streaming, SSE (/api/prompt, /api/agent/run,
    # /api/team/*, /ws, /ws/stream)
    app.include_router(runtime_router)

    return app
