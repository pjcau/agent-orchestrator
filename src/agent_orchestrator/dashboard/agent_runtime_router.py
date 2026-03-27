"""Agent Runtime router — execution and streaming endpoints.

Handles agent/team runs, graph execution, WebSocket streaming,
and SSE streaming. The compute-heavy part of the application.

Shared state is accessed via ``request.app.state``:
    - ``request.app.state.bus``           — EventBus instance
    - ``request.app.state.usage_db``      — UsageDB instance
    - ``request.app.state.job_logger``    — JobLogger instance
    - ``request.app.state.conv_manager``  — ConversationManager instance
    - ``request.app.state.active_ws``     — dict[str, WebSocket] (shared mutable)
    - ``request.app.state.active_jobs``   — dict[str, dict] (shared mutable)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .agent_runner import run_agent, run_team
from .agents_registry import get_agent_registry
from .auth import check_ws_auth
from .events import Event, EventBus, EventType
from .graphs import _make_provider, run_graph

logger = logging.getLogger(__name__)

runtime_router = APIRouter(tags=["runtime"])

# Allowed Ollama URL prefixes (SSRF protection)
_OLLAMA_ALLOWED_PREFIXES = (
    "http://localhost",
    "http://127.0.0.1",
    "http://host.docker.internal",
    "http://ollama",  # Docker service name
)


def _get_ollama_url() -> str:
    """Get and validate the Ollama base URL (SSRF-safe)."""
    url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    if not any(url.startswith(p) for p in _OLLAMA_ALLOWED_PREFIXES):
        raise ValueError(
            f"OLLAMA_BASE_URL must start with one of {_OLLAMA_ALLOWED_PREFIXES}, got: {url}"
        )
    return url


# ---------------------------------------------------------------------------
# Prompt execution (non-streaming)
# ---------------------------------------------------------------------------


@runtime_router.post("/api/prompt")
async def prompt(body: dict, request: Request):
    bus: EventBus = request.app.state.bus
    usage_db = request.app.state.usage_db
    job_logger = request.app.state.job_logger
    conv_manager = request.app.state.conv_manager

    user_prompt = body.get("prompt", "").strip()
    model = body.get("model", "")
    provider_type = body.get("provider", "ollama")
    graph_type = body.get("graph_type", "auto")
    conv_id = body.get("conversation_id")
    file_context = body.get("file_context", "")

    if not user_prompt:
        return JSONResponse(content={"success": False, "error": "Empty prompt"}, status_code=400)
    if not model:
        return JSONResponse(
            content={"success": False, "error": "No model selected"}, status_code=400
        )

    full_prompt = user_prompt
    if file_context:
        full_prompt = f"{user_prompt}\n\n```\n{file_context}\n```"

    ollama_url = _get_ollama_url()
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")

    job_logger.touch()
    result = await run_graph(
        prompt=full_prompt,
        model=model,
        provider_type=provider_type,
        graph_type=graph_type,
        ollama_url=ollama_url,
        openrouter_key=openrouter_key,
        event_bus=bus,
        conversation_id=conv_id,
        conversation_manager=conv_manager if conv_id else None,
    )

    job_logger.log(
        "prompt",
        {
            "prompt": user_prompt,
            "model": model,
            "provider": provider_type,
            "graph_type": graph_type,
            "conversation_id": conv_id,
            "result": result,
        },
    )

    usage = result.get("usage") or {}
    await usage_db.record(
        model=model,
        provider=provider_type,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cost_usd=usage.get("cost_usd", 0.0),
        elapsed_s=result.get("elapsed_s", 0.0),
        session_id=job_logger.session_id,
    )

    if conv_id:
        await usage_db.append_message(conv_id, "user", user_prompt)
        if result.get("success"):
            await usage_db.append_message(conv_id, "assistant", result.get("output", ""))

    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# Agent execution
# ---------------------------------------------------------------------------


@runtime_router.post("/api/agent/run")
async def agent_run(body: dict, request: Request):
    """Run an agent on a task with real-time events."""
    bus: EventBus = request.app.state.bus
    usage_db = request.app.state.usage_db
    job_logger = request.app.state.job_logger
    conv_manager = request.app.state.conv_manager

    agent_name = body.get("agent", "").strip()
    task_desc = body.get("task", "").strip()
    model = body.get("model", "")
    provider_type = body.get("provider", "ollama")
    tools = body.get("tools")
    max_steps = body.get("max_steps", 10)
    conv_id = body.get("conversation_id")

    if not agent_name or not task_desc:
        return JSONResponse(
            content={"success": False, "error": "Agent name and task required"},
            status_code=400,
        )
    if not model:
        return JSONResponse(
            content={"success": False, "error": "No model selected"},
            status_code=400,
        )

    ollama_url = _get_ollama_url()
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    provider = _make_provider(model, provider_type, ollama_url, openrouter_key)

    registry = get_agent_registry()
    agent_info = next((a for a in registry.get("agents", []) if a["name"] == agent_name), None)
    role = agent_info.get("description", "") if agent_info else ""

    try:
        job_logger.touch()
        # Obtain a sandbox for this session when the sandbox system is enabled.
        sandbox_manager = request.app.state.sandbox_manager
        _agent_sandbox = None
        if sandbox_manager is not None:
            try:
                _agent_sandbox = await sandbox_manager.get_or_create(job_logger.session_id)
            except Exception:
                logger.warning(
                    "Failed to obtain sandbox for agent run — running without sandbox",
                    exc_info=True,
                )

        result = await run_agent(
            agent_name=agent_name,
            task_description=task_desc,
            provider=provider,
            role=role,
            tools=tools,
            max_steps=max_steps,
            event_bus=bus,
            working_directory=str(job_logger.session_dir),
            usage_db=usage_db,
            session_id=job_logger.session_id,
            conversation_id=conv_id,
            conversation_manager=conv_manager if conv_id else None,
            sandbox=_agent_sandbox,
        )
        job_logger.log(
            "agent_run",
            {
                "agent": agent_name,
                "task": task_desc,
                "model": model,
                "provider": provider_type,
                "conversation_id": conv_id,
                "result": result,
            },
        )

        await usage_db.record(
            model=model,
            agent=agent_name,
            provider=provider_type,
            input_tokens=result.get("total_input_tokens", 0),
            output_tokens=result.get("total_output_tokens", 0),
            cost_usd=result.get("total_cost_usd", 0.0),
            elapsed_s=result.get("elapsed_s", 0.0),
            session_id=job_logger.session_id,
        )
        return JSONResponse(content=result)
    except Exception as exc:
        logger.exception("Agent run failed")
        job_logger.log(
            "agent_run",
            {
                "agent": agent_name,
                "task": task_desc,
                "model": model,
                "provider": provider_type,
                "result": {"success": False, "error": str(exc)},
            },
        )
        return JSONResponse(
            content={"success": False, "error": "Agent execution failed"},
            status_code=500,
        )


# ---------------------------------------------------------------------------
# Team run
# ---------------------------------------------------------------------------


@runtime_router.post("/api/team/run")
async def team_run(body: dict, request: Request):
    """Start a multi-agent team run as a background task.

    Returns immediately with a job_id. Results stream via WebSocket
    as team.started, team.step, and team.complete events.
    """
    bus: EventBus = request.app.state.bus
    usage_db = request.app.state.usage_db
    job_logger = request.app.state.job_logger
    conv_manager = request.app.state.conv_manager
    active_jobs: dict = request.app.state.active_jobs

    task_desc = body.get("task", "").strip()
    model = body.get("model", "")
    provider_type = body.get("provider", "openrouter")
    conv_id_team = body.get("conversation_id")

    if not task_desc:
        return JSONResponse(
            content={"success": False, "error": "Task description required"},
            status_code=400,
        )
    if not model:
        return JSONResponse(
            content={"success": False, "error": "No model selected"},
            status_code=400,
        )

    job_id = str(uuid.uuid4())[:8]
    ollama_url = _get_ollama_url()
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    provider = _make_provider(model, provider_type, ollama_url, openrouter_key)

    # Evict completed/failed jobs to prevent unbounded growth (keep last 20)
    finished = [k for k, v in active_jobs.items() if v["status"] != "running"]
    for k in finished[:-20]:
        active_jobs.pop(k, None)

    active_jobs[job_id] = {"status": "running", "task": task_desc, "result": None}

    async def _run_in_background():
        try:
            job_logger.touch()

            await bus.emit(
                Event(
                    event_type=EventType.TEAM_STARTED,
                    data={"job_id": job_id, "task": task_desc[:500], "model": model},
                )
            )

            sandbox_manager = request.app.state.sandbox_manager
            result = await run_team(
                task_description=task_desc,
                provider=provider,
                event_bus=bus,
                working_directory=str(job_logger.session_dir),
                usage_db=usage_db,
                session_id=job_logger.session_id,
                conversation_id=conv_id_team,
                conversation_manager=conv_manager if conv_id_team else None,
                sandbox_manager=sandbox_manager,
            )

            job_logger.log(
                "team_run",
                {
                    "task": task_desc,
                    "model": model,
                    "provider": provider_type,
                    "conversation_id": conv_id_team,
                    "result": {
                        "success": result.get("success"),
                        "output": result.get("output", "")[:2000],
                        "plan": result.get("plan", "")[:1000],
                        "agent_costs": result.get("agent_costs", {}),
                        "total_tokens": result.get("total_tokens"),
                        "total_cost_usd": result.get("total_cost_usd"),
                        "elapsed_s": result.get("elapsed_s"),
                    },
                },
            )

            for ag_name, ag_cost in (result.get("agent_costs") or {}).items():
                await usage_db.record(
                    model=model,
                    agent=ag_name,
                    provider=provider_type,
                    input_tokens=ag_cost.get("input_tokens", 0),
                    output_tokens=ag_cost.get("tokens", 0),
                    cost_usd=ag_cost.get("cost_usd", 0.0),
                    elapsed_s=result.get("elapsed_s", 0.0),
                    session_id=job_logger.session_id,
                )

            active_jobs[job_id] = {"status": "completed", "task": task_desc, "result": result}

            await bus.emit(
                Event(
                    event_type=EventType.TEAM_COMPLETE,
                    data={"job_id": job_id, **result},
                )
            )

        except Exception as exc:
            logger.exception("Team run failed (job_id=%s)", job_id)
            error_result = {"success": False, "error": str(exc)}
            active_jobs[job_id] = {
                "status": "failed",
                "task": task_desc,
                "result": error_result,
            }
            job_logger.log(
                "team_run",
                {
                    "task": task_desc,
                    "model": model,
                    "provider": provider_type,
                    "result": error_result,
                },
            )
            await bus.emit(
                Event(
                    event_type=EventType.TEAM_COMPLETE,
                    data={"job_id": job_id, **error_result},
                )
            )

    asyncio.create_task(_run_in_background())
    return JSONResponse(content={"job_id": job_id, "status": "started"})


@runtime_router.get("/api/team/status/{job_id}")
async def team_status(job_id: str, request: Request):
    """Poll the status of a background team run."""
    active_jobs: dict = request.app.state.active_jobs
    job = active_jobs.get(job_id)
    if not job:
        return JSONResponse(content={"error": "Job not found"}, status_code=404)
    return JSONResponse(content={"job_id": job_id, **job})


# ---------------------------------------------------------------------------
# WebSocket — streaming (token-by-token)
# ---------------------------------------------------------------------------


@runtime_router.websocket("/ws/stream")
async def stream_endpoint(ws: WebSocket):
    """Stream LLM responses token-by-token (authenticated)."""
    # State is on the app backing this websocket connection
    _ws_api_keys: set = ws.app.state.ws_api_keys
    bus: EventBus = ws.app.state.bus
    usage_db = ws.app.state.usage_db
    job_logger = ws.app.state.job_logger
    active_ws: dict = ws.app.state.active_ws

    ws_user = check_ws_auth(ws, _ws_api_keys)
    if not ws_user:
        await ws.close(code=1008, reason="Authentication required")
        return

    old_ws = active_ws.get("/ws/stream")
    if old_ws:
        try:
            await old_ws.close(code=1001, reason="Replaced by new connection")
        except Exception:
            pass

    await ws.accept()
    active_ws["/ws/stream"] = ws
    try:
        while True:
            data = await ws.receive_json()
            prompt_text = data.get("prompt", "").strip()
            model = data.get("model", "")
            provider_type = data.get("provider", "ollama")
            system = data.get("system", "You are a helpful AI assistant. Be concise and direct.")
            conv_id = data.get("conversation_id")
            file_context = data.get("file_context", "")

            if not prompt_text or not model:
                await ws.send_json({"type": "error", "error": "Missing prompt or model"})
                continue

            job_logger.touch()

            full_prompt = prompt_text
            if file_context:
                full_prompt = f"{prompt_text}\n\n```\n{file_context}\n```"

            if conv_id:
                recent = await usage_db.get_recent_messages(conv_id, limit=6)
                if recent:
                    history = "\n".join(
                        f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:500]}"
                        for m in recent
                    )
                    full_prompt = (
                        f"Previous conversation:\n{history}\n\nCurrent request:\n{full_prompt}"
                    )

            ollama_url = _get_ollama_url()
            openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")

            try:
                from ..providers.local import LocalProvider
                from ..providers.openrouter import OpenRouterProvider
                from ..core.provider import Message, Role

                if provider_type == "openrouter":
                    provider = OpenRouterProvider(model=model, api_key=openrouter_key)
                else:
                    provider = LocalProvider(
                        model=model,
                        base_url=f"{ollama_url}/v1",
                    )

                messages = [Message(role=Role.USER, content=full_prompt)]

                start_time = time.time()
                total_tokens = 0
                full_response = ""

                await ws.send_json({"type": "start", "model": model})

                await bus.emit(
                    Event(
                        event_type=EventType.GRAPH_START,
                        data={"nodes": ["stream"], "edges": []},
                    )
                )
                await bus.emit(
                    Event(event_type=EventType.GRAPH_NODE_ENTER, node_name="stream", data={})
                )

                async for chunk in provider.stream(
                    messages=messages,
                    system=system,
                    max_tokens=4096,
                ):
                    if chunk.content:
                        full_response += chunk.content
                        total_tokens += 1  # approximate
                        await ws.send_json({"type": "token", "content": chunk.content})
                    if chunk.is_final:
                        break

                elapsed = time.time() - start_time
                speed = total_tokens / elapsed if elapsed > 0 else 0

                await bus.emit(
                    Event(event_type=EventType.GRAPH_NODE_EXIT, node_name="stream", data={})
                )
                await bus.emit(
                    Event(
                        event_type=EventType.GRAPH_END,
                        data={"success": True, "elapsed_s": round(elapsed, 2)},
                    )
                )

                await ws.send_json(
                    {
                        "type": "done",
                        "output": full_response,
                        "usage": {
                            "output_tokens": total_tokens,
                            "model": model,
                        },
                        "elapsed_s": round(elapsed, 2),
                        "speed": round(speed, 1),
                    }
                )

                job_logger.log(
                    "stream",
                    {
                        "prompt": prompt_text,
                        "model": model,
                        "provider": provider_type,
                        "conversation_id": conv_id,
                        "result": {
                            "success": True,
                            "output": full_response,
                            "tokens": total_tokens,
                            "elapsed_s": round(elapsed, 2),
                            "speed": round(speed, 1),
                        },
                    },
                )

                await usage_db.record(
                    model=model,
                    provider=provider_type,
                    output_tokens=total_tokens,
                    elapsed_s=round(elapsed, 2),
                    session_id=job_logger.session_id,
                )

                if conv_id:
                    await usage_db.append_message(conv_id, "user", prompt_text)
                    await usage_db.append_message(conv_id, "assistant", full_response)

                await bus.emit(
                    Event(
                        event_type=EventType.TOKEN_UPDATE,
                        data={"total_tokens": total_tokens},
                    )
                )

            except Exception as e:
                await ws.send_json({"type": "error", "error": str(e)})

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if active_ws.get("/ws/stream") is ws:
            active_ws.pop("/ws/stream", None)


# ---------------------------------------------------------------------------
# WebSocket — events bus
# ---------------------------------------------------------------------------


@runtime_router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Relay EventBus events to the client in real time (authenticated)."""
    _ws_api_keys: set = ws.app.state.ws_api_keys
    bus: EventBus = ws.app.state.bus
    active_ws: dict = ws.app.state.active_ws

    ws_user = check_ws_auth(ws, _ws_api_keys)
    if not ws_user:
        await ws.close(code=1008, reason="Authentication required")
        return

    old_ws = active_ws.get("/ws")
    if old_ws:
        try:
            await old_ws.close(code=1001, reason="Replaced by new connection")
        except Exception:
            pass

    await ws.accept()
    active_ws["/ws"] = ws
    queue = bus.subscribe()
    try:
        await ws.send_json({"type": "snapshot", "data": bus.get_snapshot()})

        while True:
            event = await queue.get()
            await ws.send_json({"type": "event", "data": event.to_dict()})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        bus.unsubscribe(queue)
        if active_ws.get("/ws") is ws:
            active_ws.pop("/ws", None)


# ---------------------------------------------------------------------------
# WebSocket — sandbox terminal
# ---------------------------------------------------------------------------


@runtime_router.websocket("/ws/sandbox/{session_id}/terminal")
async def sandbox_terminal(ws: WebSocket, session_id: str):
    """Interactive terminal into a session's sandbox container.

    Bidirectional WebSocket: client sends commands (text), server streams
    output back. Uses ``docker exec -i`` for Docker sandboxes.
    """
    _ws_api_keys: set = ws.app.state.ws_api_keys
    ws_user = check_ws_auth(ws, _ws_api_keys)
    if not ws_user:
        await ws.close(code=1008, reason="Authentication required")
        return

    sandbox_manager = ws.app.state.sandbox_manager
    if sandbox_manager is None:
        await ws.close(code=1008, reason="Sandbox system is disabled")
        return

    sandbox = sandbox_manager._sandboxes.get(session_id)
    if sandbox is None or not sandbox.is_running:
        await ws.close(code=1008, reason=f"No running sandbox for session '{session_id}'")
        return

    container_id = sandbox.container_id
    if container_id is None:
        await ws.close(code=1008, reason="Terminal only available for Docker sandboxes")
        return

    await ws.accept()

    # Start an interactive shell in the container
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        "-i",
        container_id,
        "/bin/sh",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    async def _read_output():
        """Read output from the shell process and send to WebSocket."""
        try:
            while True:
                chunk = await proc.stdout.read(4096)  # type: ignore[union-attr]
                if not chunk:
                    break
                await ws.send_text(chunk.decode(errors="replace"))
        except Exception:
            pass

    read_task = asyncio.create_task(_read_output())

    try:
        while True:
            data = await ws.receive_text()
            if proc.stdin and not proc.stdin.is_closing():
                proc.stdin.write(data.encode())
                await proc.stdin.drain()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        read_task.cancel()
        if proc.returncode is None:
            proc.kill()
            await proc.communicate()
