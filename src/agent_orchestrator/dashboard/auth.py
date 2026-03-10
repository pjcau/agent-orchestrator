"""Authentication module for the FastAPI dashboard.

Supports two modes:
1. API key auth (X-API-Key header or ?api_key query param) — for programmatic access
2. OAuth2 (GitHub) with JWT session cookies — for browser-based access

Configuration via environment variables:
- DASHBOARD_API_KEYS: comma-separated API keys (if empty, dev mode = no auth)
- OAUTH_CLIENT_ID / OAUTH_CLIENT_SECRET: GitHub OAuth2 credentials
- JWT_SECRET_KEY: secret for signing JWT session cookies
- BASE_URL: public URL for OAuth2 callbacks (e.g. https://agents.yourdomain.com)
"""

from __future__ import annotations

import os
import time
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

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
JWT_EXPIRY_SECONDS = 86400  # 24 hours


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
    return os.environ.get("BASE_URL", "http://localhost:5005")


# ---------------------------------------------------------------------------
# APIKeyMiddleware (original, enhanced)
# ---------------------------------------------------------------------------


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Authentication middleware supporting API keys and JWT session cookies.

    If no API keys configured AND no OAuth configured, all requests pass through (dev mode).
    API key can be passed via X-API-Key header or ?api_key query param.
    JWT session token can be passed via 'session' cookie.
    Static files, health check, WebSocket, and auth routes are always allowed.
    """

    # Paths that bypass authentication
    EXEMPT_PREFIXES = ("/static", "/health", "/ws", "/auth/", "/login", "/api/models")

    def __init__(self, app, api_keys: list[str] | None = None) -> None:
        super().__init__(app)
        self.api_keys: set[str] = set(api_keys) if api_keys else set()
        self._oauth_configured = bool(os.environ.get("OAUTH_CLIENT_ID"))

    async def dispatch(self, request: Request, call_next):
        # No keys configured and no OAuth = dev mode, allow all
        if not self.api_keys and not self._oauth_configured:
            return await call_next(request)

        # Always allow exempt paths
        if any(request.url.path.startswith(p) for p in self.EXEMPT_PREFIXES):
            return await call_next(request)

        # Check API key (header or query param)
        api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
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

        # If OAuth is configured, redirect browser to login page
        if self._oauth_configured and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/login")

        return JSONResponse({"error": "Invalid or missing API key"}, status_code=401)
