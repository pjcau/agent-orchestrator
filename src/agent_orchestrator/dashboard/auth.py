"""Authentication module for the FastAPI dashboard.

Supports two modes:
1. API key auth (X-API-Key header) — for programmatic access
2. OAuth2 (GitHub) with JWT session cookies — for browser-based access

Configuration via environment variables:
- DASHBOARD_API_KEYS: comma-separated API keys (REQUIRED in production)
- OAUTH_CLIENT_ID / OAUTH_CLIENT_SECRET: GitHub OAuth2 credentials
- JWT_SECRET_KEY: secret for signing JWT session cookies (REQUIRED, no default)
- BASE_URL: public URL for OAuth2 callbacks (e.g. https://agents.yourdomain.com)
- ALLOW_DEV_MODE: set to "true" to explicitly allow unauthenticated dev mode
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

logger = logging.getLogger(__name__)

# JWT and OAuth2 imports are optional — gracefully degrade if not installed
try:
    import jwt as pyjwt

    HAS_JWT = True
except ImportError:
    HAS_JWT = False

try:
    from authlib.integrations.starlette_client import OAuth

    HAS_AUTHLIB = True
except ImportError:
    HAS_AUTHLIB = False


# ---------------------------------------------------------------------------
# JWT session helpers
# ---------------------------------------------------------------------------

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_SECONDS = 14400  # 4 hours


def _get_jwt_secret() -> str:
    return os.environ.get("JWT_SECRET_KEY", "")


def create_session_token(user_info: dict[str, Any]) -> str:
    """Create a JWT session token from user info (email, name, provider, role, github_login)."""
    if not HAS_JWT:
        raise RuntimeError("PyJWT is required for session tokens: pip install PyJWT")
    secret = _get_jwt_secret()
    if not secret:
        raise RuntimeError("JWT_SECRET_KEY environment variable is required")
    payload = {
        "sub": user_info.get("email", ""),
        "name": user_info.get("name", ""),
        "provider": user_info.get("provider", "unknown"),
        "github_login": user_info.get("github_login", ""),
        "role": user_info.get("role", "viewer"),
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXPIRY_SECONDS,
    }
    return pyjwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def verify_session_token(token: str) -> dict[str, Any] | None:
    """Verify and decode a JWT session token. Returns payload or None."""
    if not HAS_JWT:
        return None
    secret = _get_jwt_secret()
    if not secret:
        return None
    try:
        return pyjwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except (pyjwt.InvalidTokenError, pyjwt.ExpiredSignatureError):
        return None


# ---------------------------------------------------------------------------
# OAuth2 setup (GitHub)
# ---------------------------------------------------------------------------


def create_oauth() -> Any | None:
    """Create and configure OAuth client for GitHub.

    Returns None if authlib is not installed or no OAuth credentials configured.
    """
    if not HAS_AUTHLIB:
        return None

    github_id = os.environ.get("OAUTH_CLIENT_ID", "")

    if not github_id:
        return None

    oauth = OAuth()

    oauth.register(
        "github",
        client_id=github_id,
        client_secret=os.environ.get("OAUTH_CLIENT_SECRET", ""),
        access_token_url="https://github.com/login/oauth/access_token",
        authorize_url="https://github.com/login/oauth/authorize",
        api_base_url="https://api.github.com/",
        client_kwargs={"scope": "user:email"},
    )

    return oauth


def get_base_url() -> str:
    """Get the public base URL for OAuth2 callbacks."""
    return os.environ.get("BASE_URL", "https://localhost:5005")


# ---------------------------------------------------------------------------
# APIKeyMiddleware (original, enhanced)
# ---------------------------------------------------------------------------


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Authentication middleware supporting API keys and JWT session cookies.

    Security model (fail-closed):
    - Auth is REQUIRED by default. All unauthenticated requests are denied.
    - Dev mode (no auth) must be explicitly enabled via ALLOW_DEV_MODE=true.
    - API keys are only accepted via X-API-Key header (never query params).
    - WebSocket endpoints require authentication (checked before ws.accept).

    Static files, health check, and auth routes are always allowed.
    """

    # Paths that bypass authentication (no WebSocket — those check auth themselves)
    EXEMPT_PREFIXES = ("/static", "/health", "/auth/", "/login", "/api/models")

    def __init__(self, app, api_keys: list[str] | None = None) -> None:
        super().__init__(app)
        self.api_keys: set[str] = set(api_keys) if api_keys else set()
        self._oauth_configured = bool(os.environ.get("OAUTH_CLIENT_ID"))
        env = os.environ.get("ENVIRONMENT", "").lower()
        wants_dev = os.environ.get("ALLOW_DEV_MODE", "").lower() == "true"
        if wants_dev and env == "production":
            logger.error(
                "SECURITY: ALLOW_DEV_MODE=true is BLOCKED in production. "
                "Remove ALLOW_DEV_MODE or set ENVIRONMENT to something else."
            )
            self._dev_mode = False
        else:
            self._dev_mode = wants_dev
        if self._dev_mode:
            logger.warning(
                "SECURITY: Dev mode enabled (ALLOW_DEV_MODE=true). "
                "All endpoints are unauthenticated. Do NOT use in production."
            )

    async def dispatch(self, request: Request, call_next):
        # CORS preflight — always pass through (handled by CORSMiddleware)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Dev mode must be explicitly opted in
        if self._dev_mode:
            return await call_next(request)

        # Always allow exempt paths
        if any(request.url.path.startswith(p) for p in self.EXEMPT_PREFIXES):
            return await call_next(request)

        # Check API key (header only — never query params to avoid log leaks)
        api_key = request.headers.get("X-API-Key")
        if api_key and api_key in self.api_keys:
            return await call_next(request)

        # Check JWT session cookie
        session_token = request.cookies.get("auth_session")
        if session_token:
            user = verify_session_token(session_token)
            if user and user.get("role"):
                # Store user info in request state for route handlers
                request.state.user = user
                return await call_next(request)

        # Log failed auth attempt
        client_ip = request.client.host if request.client else "unknown"
        logger.warning("Auth denied: %s %s from %s", request.method, request.url.path, client_ip)

        # If OAuth is configured, redirect browser to login page
        if self._oauth_configured and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/login")

        return JSONResponse({"error": "Authentication required"}, status_code=401)


def check_ws_auth(request: Request, api_keys: set[str] | None = None) -> dict | None:
    """Check authentication for WebSocket connections.

    Must be called BEFORE ws.accept(). Returns user dict or None.
    Checks: X-API-Key header, auth_session cookie, or dev mode.
    """
    # Dev mode
    if os.environ.get("ALLOW_DEV_MODE", "").lower() == "true":
        return {"role": "admin", "name": "dev-mode", "github_login": "dev"}

    # API key from header
    if api_keys:
        api_key = request.headers.get("X-API-Key")
        if api_key and api_key in api_keys:
            return {"role": "developer", "name": "api-key-user"}

    # JWT session cookie
    session_token = request.cookies.get("auth_session")
    if session_token:
        user = verify_session_token(session_token)
        if user and user.get("role"):
            return user

    return None
