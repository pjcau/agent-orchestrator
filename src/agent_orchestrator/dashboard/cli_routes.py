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
import html
import json
import logging
import os
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from agent_orchestrator.agent_host import serve_agent_host
from agent_orchestrator.core.cache_context import set_cache_context

from .agent_runner import run_agent
from .agents_registry import get_agent_registry
from .auth import check_ws_auth, create_cli_token
from .cli_device_flow import (
    STATUS_APPROVED,
    STATUS_DENIED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    DeviceFlowStore,
    InMemoryDeviceFlowStore,
    normalize_user_code,
)
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


# ---------------------------------------------------------------------------
# Device-flow OAuth (RFC 8628) — used by `ago login --device`.
# ---------------------------------------------------------------------------


def _store(request: Request) -> DeviceFlowStore:
    """Return the per-app DeviceFlowStore, creating an in-memory one on first use."""
    state = request.app.state
    store: DeviceFlowStore | None = getattr(state, "device_flow_store", None)
    if store is None:
        store = InMemoryDeviceFlowStore()
        state.device_flow_store = store
    return store


def _verification_uri(request: Request) -> str:
    base = os.environ.get("BASE_URL") or str(request.base_url).rstrip("/")
    return f"{base}/api/cli/v1/auth/device"


@cli_router.post("/auth/device-start")
async def device_authorization(request: Request) -> dict[str, Any]:
    """Start a device-authorization flow (RFC 8628 §3.1).

    Anyone with the dashboard's API key — or a JWT session — can start a
    flow. The pairing only becomes useful once a logged-in browser visits
    ``GET /api/cli/v1/auth/device?user_code=...`` and approves it.
    """
    store = _store(request)
    flow = await store.create()
    return flow.public_dict(_verification_uri(request))


@cli_router.post("/auth/device-poll")
async def device_token(body: dict, request: Request) -> JSONResponse:
    """Poll for the access token (RFC 8628 §3.4).

    Returns:
        200 ``{"access_token": "..."}`` when the user has approved the flow.
        400 ``{"error": "authorization_pending"}`` while the user has not
            yet approved.
        400 ``{"error": "access_denied"}`` if the user rejected the request.
        400 ``{"error": "expired_token"}`` if the request expired.
        404 ``{"error": "unknown_device_code"}`` if the device_code is bogus.
    """
    device_code = str(body.get("device_code") or "").strip()
    if not device_code:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "device_code is required"},
            status_code=400,
        )
    store = _store(request)
    flow = await store.lookup_by_device_code(device_code)
    if flow is None:
        return JSONResponse({"error": "unknown_device_code"}, status_code=404)

    import time as _t

    now = _t.time()
    if flow.is_expired(now) and flow.status != STATUS_APPROVED:
        return JSONResponse({"error": STATUS_EXPIRED}, status_code=400)
    # RFC 8628 §3.5 — slow_down: enforce the polling interval.
    if flow.last_poll_at and now - flow.last_poll_at < max(flow.interval - 1, 1):
        await store.record_poll(device_code, now)
        return JSONResponse({"error": "slow_down"}, status_code=400)
    await store.record_poll(device_code, now)

    if flow.status == STATUS_PENDING:
        return JSONResponse({"error": STATUS_PENDING}, status_code=400)
    if flow.status == STATUS_DENIED:
        return JSONResponse({"error": STATUS_DENIED}, status_code=400)
    if flow.status == STATUS_EXPIRED:
        return JSONResponse({"error": STATUS_EXPIRED}, status_code=400)
    if flow.status == STATUS_APPROVED:
        # Atomic read+remove — the same device_code is single-use.
        snapshot = await store.consume(device_code)
        if snapshot is None or snapshot.status != STATUS_APPROVED or not snapshot.user_info:
            return JSONResponse({"error": STATUS_EXPIRED}, status_code=400)
        token = create_cli_token(snapshot.user_info)
        return JSONResponse({"access_token": token, "token_type": "Bearer"})
    # Defensive — keeps the switch closed.
    return JSONResponse({"error": "server_error"}, status_code=500)


# ---- Browser-facing approval endpoint ------------------------------------
#
# The CLI prints ``verification_uri_complete`` for the user; clicking the URL
# lands here. The middleware ensures the user has a valid JWT session before
# reaching this code — anonymous browsers are redirected to ``/login``.
#
# RFC 8628 §3.3 recommends a confirmation step. We follow that: GET shows a
# minimal HTML form; POST does the actual approval. This prevents accidental
# approvals via prefetchers / link previews.


_HTML_HEAD = (
    "<!doctype html>"
    "<meta charset='utf-8'>"
    "<title>Authorize CLI device</title>"
    "<style>"
    "body{font-family:system-ui;background:#0b1220;color:#e6edf3;"
    "display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}"
    "main{background:#161b22;border:1px solid #30363d;border-radius:12px;"
    "padding:28px 34px;max-width:440px;width:90%}"
    "h1{font-size:18px;margin:0 0 8px;font-weight:600}"
    "p{margin:6px 0;color:#9da7b3;font-size:14px;line-height:1.5}"
    "code{background:#0b1220;padding:2px 6px;border-radius:4px;"
    "border:1px solid #30363d;font-size:13px}"
    ".row{display:flex;gap:8px;margin-top:18px}"
    "button{flex:1;padding:10px 14px;border-radius:6px;border:1px solid #30363d;"
    "background:#21262d;color:#e6edf3;cursor:pointer;font-size:14px}"
    "button.primary{background:#238636;border-color:#2ea043}"
    "button.primary:hover{background:#2ea043}"
    "form{display:contents}"
    ".ok{color:#3fb950}.err{color:#f85149}"
    "</style>"
)


@cli_router.get("/auth/device", response_class=HTMLResponse)
async def device_approval_page(request: Request) -> HTMLResponse:
    user = getattr(request.state, "user", None)
    if not user:
        # The auth middleware should have intercepted this; defensive fallback.
        return HTMLResponse(
            _HTML_HEAD + "<main><h1>Sign in required</h1>"
            "<p>Sign in to the dashboard first, then re-open the CLI link.</p></main>",
            status_code=401,
        )
    raw_code = request.query_params.get("user_code", "")
    code = normalize_user_code(raw_code) if raw_code else ""
    if not code:
        return HTMLResponse(
            _HTML_HEAD + "<main><h1>Enter pairing code</h1>"
            "<p>Open the link the CLI printed, or run <code>ago login --device</code> again.</p>"
            "<form method='post' action='/api/cli/v1/auth/device/approve'>"
            "<input name='user_code' placeholder='XXXX-XXXX' "
            "style='width:100%;padding:10px;border-radius:6px;border:1px solid #30363d;"
            "background:#0b1220;color:#e6edf3;font-size:14px' autofocus required>"
            "<div class='row'><button class='primary' type='submit'>Continue</button></div>"
            "</form></main>",
            status_code=400,
        )
    store = _store(request)
    flow = await store.lookup_by_user_code(code)
    if flow is None or flow.is_expired(0 if False else __import__("time").time()):
        return HTMLResponse(
            _HTML_HEAD + "<main><h1>Code not recognised or expired</h1>"
            "<p>Run <code>ago login --device</code> again on your device.</p></main>",
            status_code=410,
        )
    if flow.status == STATUS_APPROVED:
        return HTMLResponse(
            _HTML_HEAD + "<main><h1 class='ok'>Already approved</h1>"
            "<p>This pairing has already been completed. Your CLI should now be logged in.</p></main>"
        )
    if flow.status == STATUS_DENIED:
        return HTMLResponse(
            _HTML_HEAD + "<main><h1 class='err'>Pairing previously denied</h1>"
            "<p>Run <code>ago login --device</code> again to start over.</p></main>",
            status_code=410,
        )
    safe_code = html.escape(code)
    safe_name = html.escape(
        (user.get("name") or user.get("sub") or "you") if isinstance(user, dict) else "you"
    )
    return HTMLResponse(
        _HTML_HEAD + "<main>"
        f"<h1>Authorize CLI for {safe_name}?</h1>"
        f"<p>The CLI requested permission to act as <code>{safe_name}</code> using "
        f"the device pairing code <code>{safe_code}</code>.</p>"
        "<p>Approve only if you started <code>ago login --device</code> on the same device.</p>"
        "<form method='post' action='/api/cli/v1/auth/device/approve'>"
        f"<input type='hidden' name='user_code' value='{safe_code}'>"
        "<div class='row'>"
        "<button type='submit' name='decision' value='deny'>Cancel</button>"
        "<button class='primary' type='submit' name='decision' value='approve'>Approve</button>"
        "</div></form></main>"
    )


@cli_router.post("/auth/device/approve", response_class=HTMLResponse)
async def device_approval_submit(request: Request) -> HTMLResponse:
    user = getattr(request.state, "user", None)
    if not user:
        return HTMLResponse(_HTML_HEAD + "<main><h1>Sign in required</h1></main>", status_code=401)
    form = await request.form()
    raw_code = str(form.get("user_code") or "")
    code = normalize_user_code(raw_code)
    decision = str(form.get("decision") or "approve")
    if not code:
        return HTMLResponse(
            _HTML_HEAD + "<main><h1 class='err'>Invalid pairing code</h1></main>",
            status_code=400,
        )
    store = _store(request)
    flow = await store.lookup_by_user_code(code)
    if flow is None:
        return HTMLResponse(
            _HTML_HEAD + "<main><h1 class='err'>Pairing code not found</h1></main>",
            status_code=410,
        )
    if decision == "deny":
        try:
            await store.deny(code)
        except KeyError:
            pass
        return HTMLResponse(
            _HTML_HEAD + "<main><h1>Pairing cancelled</h1><p>You can close this tab.</p></main>"
        )
    try:
        await store.approve(code, dict(user))
    except KeyError:
        return HTMLResponse(
            _HTML_HEAD + "<main><h1 class='err'>Could not approve</h1>"
            "<p>The code may have expired or been used already.</p></main>",
            status_code=410,
        )
    return HTMLResponse(
        _HTML_HEAD + "<main><h1 class='ok'>Device approved</h1>"
        "<p>Return to your terminal — the CLI should be authenticated within a few seconds.</p></main>"
    )


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
    conv_id = (body.get("conversation_id") or "").strip() or None
    # Cacheable @-ref prefix → OpenRouterProvider via ContextVar.
    set_cache_context((body.get("cache_context") or "").strip() or None)
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

    # Pick up the shared ConversationManager so multi-turn `ago chat`
    # sessions actually see prior exchanges. Falls back to None when the
    # dashboard was bootstrapped without one (e.g. in unit tests).
    conv_manager = getattr(request.app.state, "conv_manager", None) if conv_id else None

    async def _generator() -> AsyncIterator[bytes]:
        queue = private_bus.subscribe()
        yield _sse(
            "start",
            {
                "run_id": run_id,
                "agent": agent_name,
                "model": model,
                "provider": provider_type,
                "conversation_id": conv_id,
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
                conversation_id=conv_id,
                conversation_manager=conv_manager,
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


# ---------------------------------------------------------------------------
# Agent-host — client-side tool delegation (see agent_host/ package).
# ---------------------------------------------------------------------------


@cli_router.websocket("/agent-host")
async def agent_host_endpoint(ws: WebSocket) -> None:
    """WebSocket entry point for ``ago chat --client-tools``.

    Authentication is checked **before** ``ws.accept()`` exactly as in
    ``/ws/stream`` — never accept an anonymous WebSocket and only then
    discover it's not authorized, that would leak the existence of the
    endpoint and give an attacker a free retry budget.

    All protocol logic lives in :mod:`agent_orchestrator.agent_host.server`;
    this stub only owns the FastAPI surface and the auth gate. Keeping the
    route file thin matches the rest of the dashboard layout and keeps the
    import boundary clean (``agent_host`` has zero dashboard imports).
    """
    ws_api_keys: set = getattr(ws.app.state, "ws_api_keys", set())
    user = check_ws_auth(ws, ws_api_keys)
    if not user:
        await ws.close(code=1008, reason="Authentication required")
        return
    await ws.accept()
    try:
        reason = await serve_agent_host(ws)
        logger.info(
            "agent-host: session ended reason=%s identity=%s",
            reason,
            user.get("name") or user.get("github_login") or "api-key",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent-host: unhandled error: %s", exc)
        try:
            await ws.close(code=1011, reason="internal_error")
        except Exception:  # noqa: BLE001
            pass

