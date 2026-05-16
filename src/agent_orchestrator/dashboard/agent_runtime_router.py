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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from agent_orchestrator.core.failure_patterns import load_default_registry
from agent_orchestrator.core.repair_loop import RepairLoop
from agent_orchestrator.core.verification_gate import VerificationGate
from agent_orchestrator.core.verifiers import (
    DependencyVerifier,
    EncodingVerifier,
    SyntaxVerifier,
)

from .agent_runner import run_agent, run_team
from .agents_registry import get_agent_registry
from .auth import check_ws_auth
from .events import Event, EventBus, EventType
from .graphs import _make_provider, run_graph

logger = logging.getLogger(__name__)


def _safe_log(value: str) -> str:
    """Strip CR/LF/TAB from values before they reach the logger.

    Mirrors ``dashboard.app._sanitize_log``; duplicated to avoid an import
    cycle between this router and ``app.py``.
    """
    return str(value).replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


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
# Workspace repair-loop integration (v1.5 P1).
# Opt-in via REPAIR_LOOP_ENABLED=true. Wraps run_team() with a 5-attempt
# verify-and-retry harness. See docs/architecture-repair-loop.md.
# ---------------------------------------------------------------------------


def _repair_loop_enabled() -> bool:
    return os.environ.get("REPAIR_LOOP_ENABLED", "").strip().lower() == "true"


@dataclass
class _TeamRunWrapper:
    """Adapter: wraps the run_team() dict result in the RepairLoop protocol."""

    workdir: Path
    cost_usd: float
    raw: dict[str, Any]


def _make_emit_bridge(bus: EventBus | None) -> Any:
    """Sync (str, dict) -> None sink that forwards to an async EventBus.

    The gate and repair loop call this synchronously from within their own
    async context, so `asyncio.create_task` is the right primitive — it
    requires a running event loop, which is guaranteed here.
    """
    if bus is None:
        return None

    def _emit(event_name: str, data: dict[str, Any]) -> None:
        try:
            event_type = EventType(event_name)
        except ValueError:
            return  # unknown event name → silently drop
        try:
            asyncio.create_task(bus.emit(Event(event_type=event_type, data=data)))
        except RuntimeError:
            # No running loop (e.g. shutdown) — drop the event rather than crash.
            pass

    return _emit


def _build_repair_loop(
    bus: EventBus | None,
    team_runner: Any,
) -> RepairLoop:
    """Construct the bundled repair loop. `team_runner` is the closure that
    invokes `run_team()` and packages the result for the loop."""
    emit = _make_emit_bridge(bus)
    gate = VerificationGate(
        [SyntaxVerifier(), EncodingVerifier(), DependencyVerifier()],
        emit_event=emit,
    )
    try:
        registry = load_default_registry()
    except Exception as exc:  # noqa: BLE001 — broken YAML must not break team_run
        logger.warning("repair loop: failed to load failure_patterns.yaml: %s", exc)
        registry = None

    max_attempts = int(os.environ.get("REPAIR_LOOP_MAX_ATTEMPTS", "5"))
    max_cost = float(os.environ.get("REPAIR_LOOP_MAX_COST_USD", "0.50"))

    return RepairLoop(
        team_runner=team_runner,
        gate=gate,
        max_attempts=max_attempts,
        max_cost_usd=max_cost,
        pattern_registry=registry,
        emit_event=emit,
    )


async def _run_team_with_repair(
    task_description: str,
    *,
    provider: Any,
    event_bus: EventBus,
    working_directory: str,
    usage_db: Any,
    session_id: str,
    conversation_id: str | None,
    conversation_manager: Any,
    sandbox_manager: Any,
) -> dict[str, Any]:
    """Drop-in replacement for `run_team()` when REPAIR_LOOP_ENABLED=true.

    Returns the SAME dict shape as `run_team()` for backwards compatibility
    (so the existing job_logger / usage_db flow keeps working), enriched
    with a top-level ``repair`` key carrying the loop summary.
    """
    workdir = Path(working_directory)
    # Capture every attempt's raw dict in attempt order; the last one is what
    # the dashboard surfaces, while keeping the full history available for
    # debugging.
    raw_history: list[dict[str, Any]] = []

    async def _runner(task: str, **kw: Any) -> _TeamRunWrapper:
        raw = await run_team(task_description=task, **kw)
        raw_history.append(raw)
        return _TeamRunWrapper(
            workdir=workdir,
            cost_usd=float(raw.get("total_cost_usd") or 0.0),
            raw=raw,
        )

    loop = _build_repair_loop(event_bus, _runner)
    repair_result = await loop.run(
        task_description,
        provider=provider,
        event_bus=event_bus,
        working_directory=working_directory,
        usage_db=usage_db,
        session_id=session_id,
        conversation_id=conversation_id,
        conversation_manager=conversation_manager,
        sandbox_manager=sandbox_manager,
    )

    # Fall back to an empty dict if the loop aborted before any attempt ran
    # (shouldn't happen — max_attempts >= 1 — but defensive).
    last_raw: dict[str, Any] = raw_history[-1] if raw_history else {}

    payload = dict(last_raw)
    # The repair loop is authoritative for "did the whole pipeline pass?".
    # We override only `success`; everything else (output, plan, agent_costs,
    # tokens) flows from the underlying run_team call.
    payload["success"] = bool(last_raw.get("success")) and repair_result.final_report.passed
    payload["total_cost_usd"] = repair_result.cumulative_cost_usd
    payload["elapsed_s"] = repair_result.cumulative_duration_s
    payload["repair"] = {
        "status": repair_result.status,
        "attempts": repair_result.attempt_count,
        "cumulative_cost_usd": repair_result.cumulative_cost_usd,
        "final_passed": repair_result.final_report.passed,
        "final_failures": [
            {
                "verifier": f.verifier,
                "category": f.category,
                "message": f.message,
                "file": f.file,
                "signature": f.signature,
            }
            for f in repair_result.final_report.failures
        ],
        "auto_fixed_signatures": [
            sig for a in repair_result.attempts for sig in a.auto_fixed_signatures
        ],
    }
    return payload


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
    # P1 RAG: optional toggle. When true the dashboard fetches relevant
    # chunks from the configured namespace before calling the LLM and
    # prepends them to the prompt as a "Retrieved context" block.
    rag_enabled = bool(body.get("rag_enabled", False))
    rag_namespace = str(body.get("rag_namespace", "shared")).strip() or "shared"
    rag_k = int(body.get("rag_k", 5) or 5)

    if not user_prompt:
        return JSONResponse(content={"success": False, "error": "Empty prompt"}, status_code=400)
    if not model:
        return JSONResponse(
            content={"success": False, "error": "No model selected"}, status_code=400
        )

    full_prompt = user_prompt
    if file_context:
        full_prompt = f"{user_prompt}\n\n```\n{file_context}\n```"

    # ── RAG injection (P1) ────────────────────────────────────────────
    rag_summary: dict | None = None
    if rag_enabled:
        retriever = getattr(request.app.state, "knowledge_retriever", None)
        if retriever is not None:
            try:
                from ..skills.retrieval_skill import parse_namespace, render_namespace

                ns = parse_namespace(rag_namespace)
                rag_result = await retriever.retrieve(user_prompt, ns, k=rag_k)
                if not rag_result.is_empty:
                    full_prompt = f"{rag_result.as_context_block()}\n{full_prompt}"
                rag_summary = {
                    "namespace": render_namespace(ns),
                    "hits": len(rag_result.hits),
                    "embedding_model": rag_result.embedding_model,
                    "scores": [h.score for h in rag_result.hits],
                }
                logger.info(
                    "RAG retrieved %d chunks from %r for prompt (model=%r)",
                    len(rag_result.hits),
                    _safe_log(render_namespace(ns)),
                    _safe_log(rag_result.embedding_model),
                )
                await bus.emit(
                    Event(
                        event_type=EventType.KNOWLEDGE_RETRIEVED,
                        data={
                            "namespace": list(ns),
                            "namespace_label": render_namespace(ns),
                            "query": user_prompt,
                            "k": rag_k,
                            "hits": len(rag_result.hits),
                            "embedding_model": rag_result.embedding_model,
                            "scores": [h.score for h in rag_result.hits],
                            "trigger": "auto",
                        },
                    )
                )
            except ValueError as exc:
                rag_summary = {"error": str(exc)}
                await bus.emit(
                    Event(
                        event_type=EventType.KNOWLEDGE_RETRIEVAL_SKIPPED,
                        data={"reason": str(exc), "namespace": rag_namespace},
                    )
                )
        else:
            await bus.emit(
                Event(
                    event_type=EventType.KNOWLEDGE_RETRIEVAL_SKIPPED,
                    data={"reason": "knowledge subsystem not configured"},
                )
            )

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

    if rag_summary is not None:
        # Surface RAG details in the response so the UI can show "RAG used"
        # bubbles, scores, and the namespace that was searched.
        result = dict(result)
        result["rag"] = rag_summary

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
            if _repair_loop_enabled():
                result = await _run_team_with_repair(
                    task_desc,
                    provider=provider,
                    event_bus=bus,
                    working_directory=str(job_logger.session_dir),
                    usage_db=usage_db,
                    session_id=job_logger.session_id,
                    conversation_id=conv_id_team,
                    conversation_manager=conv_manager if conv_id_team else None,
                    sandbox_manager=sandbox_manager,
                )
            else:
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
        except Exception as exc:
            logger.debug("Failed to close replaced /ws/stream connection: %s", exc)

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
            rag_enabled = bool(data.get("rag_enabled", False))
            rag_namespace = str(data.get("rag_namespace", "shared")).strip() or "shared"
            rag_k = int(data.get("rag_k", 5) or 5)

            if not prompt_text or not model:
                await ws.send_json({"type": "error", "error": "Missing prompt or model"})
                continue

            job_logger.touch()

            full_prompt = prompt_text
            if file_context:
                full_prompt = f"{prompt_text}\n\n```\n{file_context}\n```"

            # ── RAG injection (P1) for streaming path ──────────────────
            if rag_enabled:
                retriever = getattr(ws.app.state, "knowledge_retriever", None)
                if retriever is not None:
                    try:
                        from ..skills.retrieval_skill import (
                            parse_namespace,
                            render_namespace,
                        )

                        ns = parse_namespace(rag_namespace)
                        rag_result = await retriever.retrieve(prompt_text, ns, k=rag_k)
                        if not rag_result.is_empty:
                            full_prompt = f"{rag_result.as_context_block()}\n{full_prompt}"
                        logger.info(
                            "RAG (stream) retrieved %d chunks from %r",
                            len(rag_result.hits),
                            _safe_log(render_namespace(ns)),
                        )
                        await bus.emit(
                            Event(
                                event_type=EventType.KNOWLEDGE_RETRIEVED,
                                data={
                                    "namespace": list(ns),
                                    "namespace_label": render_namespace(ns),
                                    "query": prompt_text,
                                    "k": rag_k,
                                    "hits": len(rag_result.hits),
                                    "embedding_model": rag_result.embedding_model,
                                    "scores": [h.score for h in rag_result.hits],
                                    "trigger": "auto-stream",
                                },
                            )
                        )
                        # Notify the client so it can render a "RAG used" chip.
                        await ws.send_json(
                            {
                                "type": "rag",
                                "namespace": render_namespace(ns),
                                "hits": len(rag_result.hits),
                                "embedding_model": rag_result.embedding_model,
                                "scores": [h.score for h in rag_result.hits],
                            }
                        )
                    except ValueError as exc:
                        await bus.emit(
                            Event(
                                event_type=EventType.KNOWLEDGE_RETRIEVAL_SKIPPED,
                                data={"reason": str(exc), "namespace": rag_namespace},
                            )
                        )

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
    except Exception as exc:
        logger.warning("/ws/stream loop terminated unexpectedly: %s", exc, exc_info=True)
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
        except Exception as exc:
            logger.debug("Failed to close replaced /ws connection: %s", exc)

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
    except Exception as exc:
        logger.warning("/ws event relay terminated unexpectedly: %s", exc, exc_info=True)
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
        except Exception as exc:
            logger.debug("Sandbox terminal read loop ended: %s", exc)

    read_task = asyncio.create_task(_read_output())

    try:
        while True:
            data = await ws.receive_text()
            if proc.stdin and not proc.stdin.is_closing():
                proc.stdin.write(data.encode())
                await proc.stdin.drain()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("Sandbox terminal loop terminated unexpectedly: %s", exc, exc_info=True)
    finally:
        read_task.cancel()
        if proc.returncode is None:
            proc.kill()
            await proc.communicate()
