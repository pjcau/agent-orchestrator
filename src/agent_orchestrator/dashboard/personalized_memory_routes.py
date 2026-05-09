"""HTTP API for per-user personalized memory (P4).

Endpoints:
- ``GET    /api/user-memory/users/{user_id}``        — list all entries for a user
- ``GET    /api/user-memory/users/{user_id}/{key}``  — single entry
- ``DELETE /api/user-memory/users/{user_id}/{key}``  — remove a single entry
- ``DELETE /api/user-memory/users/{user_id}``        — wipe all entries (GDPR erasure)

The ``PersonalizedMemory`` instance is read from ``app.state.personalized_memory``.
It is assembled at startup in ``dashboard/app.py`` using the shared store and
optional MemoryFilter.

All operations return JSON.  Missing resources return 404.  If the
``personalized_memory`` state attribute is absent (e.g. store not yet ready),
endpoints return 503.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

memory_router = APIRouter(prefix="/api/user-memory")


def _get_pm(request: Request):
    """Return the PersonalizedMemory from app state or None."""
    return getattr(request.app.state, "personalized_memory", None)


@memory_router.get("/users/{user_id}")
async def list_user_entries(user_id: str, request: Request, limit: int = 50):
    """List memory entries for *user_id*, newest first."""
    pm = _get_pm(request)
    if pm is None:
        return JSONResponse(
            content={"error": "Personalized memory not configured"}, status_code=503
        )
    try:
        entries = await pm.list(user_id, limit=max(1, min(500, limit)))
    except Exception as exc:
        logger.error("list_user_entries failed for user=%s: %s", user_id, exc, exc_info=True)
        return JSONResponse(content={"error": "Internal error"}, status_code=500)

    return JSONResponse(
        content={
            "user_id": user_id,
            "count": len(entries),
            "entries": entries,
        }
    )


@memory_router.get("/users/{user_id}/{key}")
async def get_user_entry(user_id: str, key: str, request: Request):
    """Return the memory entry for *user_id* / *key*, or 404."""
    pm = _get_pm(request)
    if pm is None:
        return JSONResponse(
            content={"error": "Personalized memory not configured"}, status_code=503
        )
    try:
        value = await pm.get(user_id, key)
    except Exception as exc:
        logger.error(
            "get_user_entry failed for user=%s key=%s: %s", user_id, key, exc, exc_info=True
        )
        return JSONResponse(content={"error": "Internal error"}, status_code=500)

    if value is None:
        return JSONResponse(
            content={"error": f"No entry '{key}' for user '{user_id}'"}, status_code=404
        )
    return JSONResponse(content={"user_id": user_id, "key": key, "value": value})


@memory_router.delete("/users/{user_id}/{key}")
async def delete_user_entry(user_id: str, key: str, request: Request):
    """Remove the memory entry *key* for *user_id*.

    Returns 404 when the key does not exist.
    """
    pm = _get_pm(request)
    if pm is None:
        return JSONResponse(
            content={"error": "Personalized memory not configured"}, status_code=503
        )
    try:
        deleted = await pm.delete(user_id, key)
    except Exception as exc:
        logger.error(
            "delete_user_entry failed for user=%s key=%s: %s", user_id, key, exc, exc_info=True
        )
        return JSONResponse(content={"error": "Internal error"}, status_code=500)

    if not deleted:
        return JSONResponse(
            content={"error": f"No entry '{key}' for user '{user_id}'"}, status_code=404
        )
    return JSONResponse(content={"success": True, "user_id": user_id, "key": key})


@memory_router.delete("/users/{user_id}")
async def wipe_user_memory(user_id: str, request: Request):
    """Delete **all** memory entries for *user_id* (GDPR erasure).

    Returns the count of removed entries.
    """
    pm = _get_pm(request)
    if pm is None:
        return JSONResponse(
            content={"error": "Personalized memory not configured"}, status_code=503
        )
    try:
        count = await pm.wipe(user_id)
    except Exception as exc:
        logger.error("wipe_user_memory failed for user=%s: %s", user_id, exc, exc_info=True)
        return JSONResponse(content={"error": "Internal error"}, status_code=500)

    return JSONResponse(content={"success": True, "user_id": user_id, "removed": count})
