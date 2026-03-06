"""API key authentication middleware for the FastAPI dashboard."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Simple API key authentication middleware.

    If no API keys configured, all requests pass through (dev mode).
    API key can be passed via X-API-Key header or ?api_key query param.
    Static files (CSS/JS/HTML) are always allowed.
    """

    def __init__(self, app, api_keys: list[str] | None = None) -> None:
        super().__init__(app)
        self.api_keys: set[str] = set(api_keys) if api_keys else set()

    async def dispatch(self, request: Request, call_next):
        # No keys configured = dev mode, allow all
        if not self.api_keys:
            return await call_next(request)

        # Always allow static files
        if request.url.path.startswith("/static"):
            return await call_next(request)

        # Check API key
        api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        if api_key not in self.api_keys:
            return JSONResponse({"error": "Invalid or missing API key"}, status_code=401)

        return await call_next(request)
