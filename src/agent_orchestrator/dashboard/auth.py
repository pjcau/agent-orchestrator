"""Authentication module for the FastAPI dashboard.

Supports two modes:
1. API key auth (X-API-Key header) — for programmatic access
2. OAuth2 (GitHub and/or Google) with JWT session cookies — for browser-based access

Configuration via environment variables:
- DASHBOARD_API_KEYS: comma-separated API keys (REQUIRED in production)
- OAUTH_CLIENT_ID / OAUTH_CLIENT_SECRET: GitHub OAuth2 credentials
- GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET: Google OAuth2 credentials
- ALLOWED_GOOGLE_EMAILS: comma-separated allowlist of Google emails (and/or
  ``*@domain`` wildcards). Empty/missing = no Google login allowed (fail-closed).
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

# CLI tokens minted via device-flow login. Long-lived but rotatable: the user
# can re-run `ago login --device` at any time to get a fresh token. The TTL
# is overridable via env so deployments with stricter policies can shorten it.
CLI_TOKEN_DEFAULT_TTL_SECONDS = 30 * 24 * 3600  # 30 days


def _get_jwt_secret() -> str:
    return os.environ.get("JWT_SECRET_KEY", "")


def _cli_token_ttl() -> int:
    raw = os.environ.get("AGO_CLI_TOKEN_TTL_SECONDS", "").strip()
    if not raw:
        return CLI_TOKEN_DEFAULT_TTL_SECONDS
    try:
        ttl = int(raw)
    except ValueError:
        logger.warning("invalid AGO_CLI_TOKEN_TTL_SECONDS=%r — using default", raw)
        return CLI_TOKEN_DEFAULT_TTL_SECONDS
    return max(60, ttl)


def create_session_token(
    user_info: dict[str, Any],
    *,
    expiry_seconds: int | None = None,
    provider_override: str | None = None,
) -> str:
    """Create a JWT session token from user info.

    Used by both the browser session (default 4 h) and ``ago login --device``
    (``expiry_seconds = _cli_token_ttl()``, ``provider_override = "device-flow"``).
    """
    if not HAS_JWT:
        raise RuntimeError("PyJWT is required for session tokens: pip install PyJWT")
    secret = _get_jwt_secret()
    if not secret:
        raise RuntimeError("JWT_SECRET_KEY environment variable is required")
    ttl = expiry_seconds if expiry_seconds is not None else JWT_EXPIRY_SECONDS
    payload = {
        "sub": user_info.get("email", "") or user_info.get("sub", ""),
        "name": user_info.get("name", ""),
        "provider": provider_override or user_info.get("provider", "unknown"),
        "github_login": user_info.get("github_login", ""),
        "role": user_info.get("role", "viewer"),
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl,
    }
    return pyjwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def create_cli_token(user_info: dict[str, Any]) -> str:
    """Mint a long-lived JWT for `ago login --device`.

    Returned to the CLI by ``/api/cli/v1/auth/device-poll`` once a logged-in
    browser approves the pairing. The token is stateless — no per-token row
    on the server — so it survives restarts and works across workers.
    """
    return create_session_token(
        user_info,
        expiry_seconds=_cli_token_ttl(),
        provider_override="device-flow",
    )


def _looks_like_jwt(value: str) -> bool:
    """Quick gate to avoid running HMAC verify on every gibberish header.

    A compact JWT always has exactly two dots separating three base64url
    segments. Cheaper than asking PyJWT to fail.
    """
    return value.count(".") == 2 and not value.endswith(".")


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
    """Create and configure OAuth client for GitHub and/or Google.

    Returns None if authlib is not installed or no provider is configured.
    Each provider is registered independently — both, either, or neither may be active.
    """
    if not HAS_AUTHLIB:
        return None

    github_id = os.environ.get("OAUTH_CLIENT_ID", "")
    google_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")

    if not github_id and not google_id:
        return None

    oauth = OAuth()

    if github_id:
        oauth.register(
            "github",
            client_id=github_id,
            client_secret=os.environ.get("OAUTH_CLIENT_SECRET", ""),
            access_token_url="https://github.com/login/oauth/access_token",
            authorize_url="https://github.com/login/oauth/authorize",
            api_base_url="https://api.github.com/",
            client_kwargs={"scope": "user:email"},
        )

    if google_id:
        oauth.register(
            "google",
            client_id=google_id,
            client_secret=os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
            server_metadata_url=("https://accounts.google.com/.well-known/openid-configuration"),
            client_kwargs={"scope": "openid email profile"},
        )

    return oauth


def get_base_url() -> str:
    """Get the public base URL for OAuth2 callbacks."""
    return os.environ.get("BASE_URL", "https://localhost:5005")


# ---------------------------------------------------------------------------
# Google email allowlist
# ---------------------------------------------------------------------------


def _load_allowed_emails() -> set[str]:
    """Parse ALLOWED_GOOGLE_EMAILS env var into a set of normalized entries.

    Accepts a comma-separated list. Each entry is stripped and lowercased.
    Supports exact emails (``alice@gmail.com``) and domain wildcards
    (``*@example.com``). Empty entries are dropped.
    """
    raw = os.environ.get("ALLOWED_GOOGLE_EMAILS", "")
    return {entry.strip().lower() for entry in raw.split(",") if entry.strip()}


def is_email_allowed(email: str) -> bool:
    """Return True if ``email`` is permitted by ALLOWED_GOOGLE_EMAILS.

    Fail-closed: an empty/missing allowlist denies every email.
    Matches are case-insensitive. Domain wildcards (``*@domain``) match any
    address with that domain.
    """
    if not email:
        return False
    email_lower = email.strip().lower()
    if "@" not in email_lower:
        return False
    allowed = _load_allowed_emails()
    if not allowed:
        return False
    if email_lower in allowed:
        return True
    domain = email_lower.split("@", 1)[1]
    return f"*@{domain}" in allowed


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

    # Paths that bypass authentication (no WebSocket — those check auth themselves).
    # The two `/api/cli/v1/auth/device-*` entries are the anonymous bootstrap
    # endpoints of the RFC 8628 device-flow: the *browser-facing* approval
    # routes at `/api/cli/v1/auth/device` and `.../approve` are explicitly
    # NOT exempt — they require a JWT session cookie to attribute approval
    # to a real user.
    EXEMPT_PREFIXES = (
        "/assets",
        "/health",
        "/auth/",
        "/login",
        "/api/models",
        "/metrics",
        "/api/cli/v1/auth/device-start",
        "/api/cli/v1/auth/device-poll",
    )

    def __init__(self, app, api_keys: list[str] | None = None) -> None:
        super().__init__(app)
        self.api_keys: set[str] = set(api_keys) if api_keys else set()
        self._oauth_configured = bool(
            os.environ.get("OAUTH_CLIENT_ID") or os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
        )
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

        # Check API key (header only — never query params to avoid log leaks).
        api_key = request.headers.get("X-API-Key")
        if api_key:
            if api_key in self.api_keys:
                return await call_next(request)
            # Try as a JWT (issued by device-flow login). Stateless — no
            # per-token storage on the server, so this works cross-restart
            # and cross-worker by construction.
            if _looks_like_jwt(api_key):
                user = verify_session_token(api_key)
                if user and user.get("role"):
                    request.state.user = user
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

        # If OAuth is configured, redirect browser to login page.
        # Preserve the original URL in a short-lived cookie so the OAuth
        # callbacks can return the user there after sign-in instead of dropping
        # them on the home page. The CLI device-flow approval URL is the
        # motivating case: landing on `/` (chat) after login broke the pairing.
        if self._oauth_configured and "text/html" in request.headers.get("accept", ""):
            response = RedirectResponse("/login")
            return_to = request.url.path
            if request.url.query:
                return_to = f"{return_to}?{request.url.query}"
            response.set_cookie(
                "auth_return_to",
                return_to,
                httponly=True,
                secure=True,
                samesite="lax",
                max_age=600,
            )
            return response

        return JSONResponse({"error": "Authentication required"}, status_code=401)


def check_ws_auth(request: Request, api_keys: set[str] | None = None) -> dict | None:
    """Check authentication for WebSocket connections.

    Must be called BEFORE ws.accept(). Returns user dict or None.
    Checks: X-API-Key header, ephemeral device-flow key, auth_session cookie, or dev mode.
    """
    # Dev mode
    if os.environ.get("ALLOW_DEV_MODE", "").lower() == "true":
        return {"role": "admin", "name": "dev-mode", "github_login": "dev"}

    # API key from header
    api_key = request.headers.get("X-API-Key")
    if api_keys and api_key and api_key in api_keys:
        return {"role": "developer", "name": "api-key-user"}

    # Device-flow JWT — stateless, no app.state lookup required.
    if api_key and _looks_like_jwt(api_key):
        user = verify_session_token(api_key)
        if user and user.get("role"):
            return dict(user)

    # JWT session cookie
    session_token = request.cookies.get("auth_session")
    if session_token:
        user = verify_session_token(session_token)
        if user and user.get("role"):
            return user

    return None
