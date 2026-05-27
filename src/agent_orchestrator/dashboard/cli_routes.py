"""CLI client endpoints (``/api/cli/v1/*``).

This namespace is consumed by the Rust ``ago`` CLI. It is intentionally minimal
so that the surface area exposed to local CLI tooling is small and easy to
review for security.

Authentication is delegated entirely to :class:`APIKeyMiddleware`: by the time
a request reaches a route in this module, it has already been authorized via
either an ``X-API-Key`` header or a JWT session cookie. For API-key requests
there is no associated user identity, so we return a generic ``api-key`` role.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

cli_router = APIRouter(prefix="/api/cli/v1", tags=["cli"])


def _server_version(request: Request) -> str:
    """Return the FastAPI app's ``version`` field as a string."""
    return getattr(request.app, "version", "0.0.0") or "0.0.0"


@cli_router.get("/whoami")
async def whoami(request: Request) -> dict[str, Any]:
    """Return the identity associated with the current credentials.

    Used by ``ago login`` to validate a freshly pasted API key, and by
    ``ago whoami`` to display the active identity.

    Response shape::

        {
          "name": "...",          # optional, OAuth display name
          "email": "...",         # optional, OAuth email
          "role": "...",          # "admin" | "developer" | "viewer"
          "provider": "...",      # "api-key" | "github" | "google" | ...
          "server_version": "..." # server FastAPI version
        }
    """
    user = getattr(request.state, "user", None)
    if user:
        return {
            "name": user.get("name") or None,
            "email": user.get("sub") or None,
            "role": user.get("role") or "viewer",
            "provider": user.get("provider") or "session",
            "server_version": _server_version(request),
        }
    # API key authenticated — middleware allowed the request through, but no
    # per-user identity is attached. Return a generic descriptor.
    return {
        "name": "api-key",
        "email": None,
        "role": "developer",
        "provider": "api-key",
        "server_version": _server_version(request),
    }


@cli_router.get("/version")
async def version(request: Request) -> dict[str, Any]:
    """Public server-version endpoint used by future upgrade nudges.

    Authentication still applies (the middleware does not exempt this path),
    which keeps the surface symmetric with ``whoami`` and avoids exposing
    server-version metadata anonymously.
    """
    return {
        "server_version": _server_version(request),
        # CLI clients with a version lower than this should be encouraged to
        # upgrade. Bumped manually when a breaking change is shipped.
        "min_cli_version": "0.1.0",
    }
