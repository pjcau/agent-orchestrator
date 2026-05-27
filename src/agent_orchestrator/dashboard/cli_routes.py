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

import asyncio
import json
import logging
import os
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .agent_runner import run_agent
from .agents_registry import get_agent_registry
from .events import EventBus
from .graphs import _make_provider

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# /api/cli/v1/run — SSE streaming agent execution
# ---------------------------------------------------------------------------


_OLLAMA_ALLOWED_PREFIXES = (
    "http://localhost",
    "http://127.0.0.1",
    "http://host.docker.internal",
    "http://ollama",
)


def _ollama_url() -> str:
    url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    if not any(url.startswith(p) for p in _OLLAMA_ALLOWED_PREFIXES):
        raise ValueError(
            f"OLLAMA_BASE_URL must start with one of {_OLLAMA_ALLOWED_PREFIXES}, got: {url}"
        )
    return url


def _sse(event: str, data: dict[str, Any]) -> bytes:
    """Format a single SSE message. One newline ends a field, two end the message."""
    payload = json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def _keepalive() -> bytes:
    """An SSE comment line — keeps proxies from closing the connection."""
    return b": keepalive\n\n"


@cli_router.post("/run", response_model=None)
async def cli_run(body: dict, request: Request) -> StreamingResponse | JSONResponse:
    """Run an agent and stream events as Server-Sent Events.

    The connection follows the standard ``text/event-stream`` shape:

        event: start
        data: {"run_id": "..."}

        event: <agent.spawn|agent.step|agent.tool_call|...>
        data: {...}

        event: complete
        data: {"success": true, "output": "...", "elapsed_s": ..., ...}

    Each agent execution gets its own private :class:`EventBus` so concurrent
    runs do not leak events into each other's streams. The shared dashboard
    bus is not used here; that means CLI runs do not appear in the dashboard
    event feed by design — they are a separate channel.
    """
    agent_name = (body.get("agent") or "").strip()
    task_desc = (body.get("task") or "").strip()
    model = (body.get("model") or "").strip()
    provider_type = (body.get("provider") or "ollama").strip()
    max_steps = int(body.get("max_steps") or 10)
    if not agent_name or not task_desc or not model:
        return JSONResponse(
            content={"success": False, "error": "agent, task and model are required"},
            status_code=400,
        )

    try:
        ollama = _ollama_url()
    except ValueError as exc:
        return JSONResponse(content={"success": False, "error": str(exc)}, status_code=400)
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    try:
        provider = _make_provider(model, provider_type, ollama, openrouter_key)
    except Exception as exc:  # noqa: BLE001 — surface provider-construction errors to caller
        return JSONResponse(
            content={"success": False, "error": f"provider error: {exc}"},
            status_code=400,
        )

    registry = get_agent_registry()
    agent_info = next((a for a in registry.get("agents", []) if a["name"] == agent_name), None)
    role = agent_info.get("description", "") if agent_info else ""

    run_id = uuid.uuid4().hex
    private_bus = EventBus()  # isolated event channel for this request

    async def _generator() -> AsyncIterator[bytes]:
        queue = private_bus.subscribe()
        yield _sse(
            "start",
            {
                "run_id": run_id,
                "agent": agent_name,
                "model": model,
                "provider": provider_type,
            },
        )

        # Sandbox / job logger are dashboard-side concerns; CLI runs use a
        # tmp working directory and skip the per-session sandbox plumbing.
        # This keeps the endpoint cheap and avoids pulling the request into
        # the SandboxManager lifecycle.
        run_task = asyncio.create_task(
            run_agent(
                agent_name=agent_name,
                task_description=task_desc,
                provider=provider,
                role=role,
                tools=body.get("tools"),
                max_steps=max_steps,
                event_bus=private_bus,
                working_directory=None,
                usage_db=None,
                session_id=run_id,
                conversation_id=None,
                conversation_manager=None,
                sandbox=None,
            )
        )

        try:
            while True:
                # Race the next event against the run finishing.
                getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    {getter, run_task},
                    timeout=15.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    # 15 s without any event — send a keepalive comment.
                    getter.cancel()
                    yield _keepalive()
                    continue

                if getter in done:
                    event = getter.result()
                    yield _sse(
                        event.event_type.value,
                        {
                            "agent": event.agent_name,
                            "node": event.node_name,
                            "data": event.data,
                            "ts": event.timestamp,
                        },
                    )
                else:
                    getter.cancel()

                if run_task in done:
                    break

            result = await run_task
        except asyncio.CancelledError:
            # Client disconnected before completion.
            if not run_task.done():
                run_task.cancel()
            return
        except Exception as exc:  # noqa: BLE001 — surface as terminal SSE event
            logger.exception("CLI run failed (run_id=%s)", run_id)
            yield _sse(
                "complete",
                {
                    "run_id": run_id,
                    "success": False,
                    "error": str(exc),
                },
            )
            return
        finally:
            private_bus.unsubscribe(queue)

        # Drain remaining events that may have queued between the last yield
        # and run completion, so the CLI never misses the last few steps.
        while not queue.empty():
            event = queue.get_nowait()
            yield _sse(
                event.event_type.value,
                {
                    "agent": event.agent_name,
                    "node": event.node_name,
                    "data": event.data,
                    "ts": event.timestamp,
                },
            )

        yield _sse("complete", {"run_id": run_id, **result})

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
