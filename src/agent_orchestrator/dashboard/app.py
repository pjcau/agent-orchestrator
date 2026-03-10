"""FastAPI dashboard application.

Serves:
- WebSocket at /ws for real-time events
- WebSocket at /ws/stream for streaming LLM responses
- REST APIs for models, agents, prompt, files, conversations, presets
- Static files for the dashboard UI
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .agent_runner import create_skill_registry, run_agent, run_team
from .agents_registry import get_agent_registry
from .events import Event, EventBus, EventType
from .job_logger import JobLogger
from .graphs import (
    _make_provider,
    get_last_run_info,
    list_ollama_models,
    list_openrouter_models,
    replay_node,
    run_graph,
)
from .auth import APIKeyMiddleware, check_ws_auth
from .oauth_routes import router as oauth_router
from .user_store import setup_db as setup_user_db
from .usage_db import UsageDB

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

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


def create_dashboard_app(event_bus: EventBus | None = None) -> FastAPI:
    bus = event_bus or EventBus.get()

    app = FastAPI(title="Agent Orchestrator Dashboard", version="0.2.0")

    # Middleware order: Starlette runs LAST-added FIRST.
    # We want: Request → CORS → Auth → Route handler
    # So register Auth FIRST, then CORS (CORS wraps Auth).

    # 1) Auth middleware (innermost — runs after CORS)
    api_keys_raw = os.environ.get("DASHBOARD_API_KEYS", "")
    api_keys = [k.strip() for k in api_keys_raw.split(",") if k.strip()] if api_keys_raw else []
    app.add_middleware(APIKeyMiddleware, api_keys=api_keys or None)

    # 2) CORS middleware (outermost — runs first, handles OPTIONS preflight)
    from starlette.middleware.cors import CORSMiddleware

    allowed_origins = os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
    allowed_origins = [o.strip() for o in allowed_origins if o.strip()]
    if not allowed_origins:
        base = os.environ.get("BASE_URL", "https://localhost:5005")
        allowed_origins = [base]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["X-API-Key", "Content-Type", "Authorization"],
    )

    # Store api_keys set for WebSocket auth checks
    _ws_api_keys = set(api_keys) if api_keys else set()

    # Track active WebSocket connections — close old ones when new arrive
    # Only one connection per endpoint path is allowed at a time
    _active_ws: dict[str, WebSocket] = {}  # key: "/ws" or "/ws/stream"

    # Starlette session middleware (required for authlib OAuth2 state)
    try:
        from starlette.middleware.sessions import SessionMiddleware

        session_secret = os.environ.get("JWT_SECRET_KEY", "")
        if not session_secret:
            import logging

            logging.getLogger(__name__).warning(
                "JWT_SECRET_KEY not set. Sessions will use a random key (not persistent across restarts)."
            )
            import secrets

            session_secret = secrets.token_hex(32)
        app.add_middleware(SessionMiddleware, secret_key=session_secret, same_site="lax")
    except ImportError:
        pass  # itsdangerous not installed, sessions disabled

    # OAuth2 routes (Google/GitHub login)
    app.include_router(oauth_router)

    # Conversations are persisted in PostgreSQL via usage_db

    # Job logger — persists all task results to jobs/<session_id>/
    job_logger = JobLogger()

    # Usage DB — persistent cumulative stats (Postgres)
    usage_db = UsageDB()

    @app.on_event("startup")
    async def _startup():
        await usage_db.setup()
        await setup_user_db()

    # Mount static files
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/health")
    async def health():
        """Health check endpoint (unauthenticated) for load balancers and CI/CD."""
        return JSONResponse(content={"status": "ok"})

    @app.get("/", response_class=HTMLResponse)
    async def index():
        index_file = STATIC_DIR / "index.html"
        return HTMLResponse(content=index_file.read_text())

    @app.get("/api/session")
    async def session():
        """Return current session info (ID and jobs directory)."""
        return JSONResponse(
            content={
                "session_id": job_logger.session_id,
                "jobs_dir": str(job_logger.session_dir),
            }
        )

    @app.get("/api/session/history")
    async def session_history():
        """Return all job records from the current session for chat restoration."""
        records = job_logger.get_history()
        return JSONResponse(content={"session_id": job_logger.session_id, "records": records})

    @app.get("/api/jobs/list")
    async def jobs_list():
        """List all job sessions."""
        sessions = job_logger.list_sessions()
        return JSONResponse(content={"sessions": sessions})

    @app.get("/api/jobs/{session_id}")
    async def jobs_detail(session_id: str):
        """Load all records from a specific session."""
        records = job_logger.load_session(session_id)
        if not records:
            return JSONResponse(content={"error": "Session not found"}, status_code=404)
        return JSONResponse(content={"session_id": session_id, "records": records})

    @app.post("/api/jobs/{session_id}/switch")
    async def jobs_switch(session_id: str):
        """Switch to an existing session to continue work in it."""
        ok = job_logger.switch_session(session_id)
        if not ok:
            return JSONResponse(
                content={"success": False, "error": "Session not found"},
                status_code=404,
            )
        return JSONResponse(
            content={
                "success": True,
                "session_id": session_id,
                "jobs_dir": str(job_logger.session_dir),
            }
        )

    @app.get("/api/usage")
    async def usage_stats():
        """Return cumulative usage stats (tokens, cost, per-model, per-agent)."""
        return JSONResponse(content=usage_db.get_summary())

    @app.get("/metrics")
    async def prometheus_metrics():
        """Expose metrics in Prometheus text exposition format."""
        lines: list[str] = []
        totals = usage_db.get_totals()
        per_model = usage_db.get_per_model()
        per_agent = usage_db.get_per_agent()
        snap = bus.get_snapshot()

        # --- Request totals ---
        lines.append("# HELP orchestrator_requests_total Total API requests")
        lines.append("# TYPE orchestrator_requests_total counter")
        lines.append(f"orchestrator_requests_total {totals['total_requests']}")

        # --- Token totals ---
        lines.append("# HELP orchestrator_tokens_total Total tokens consumed")
        lines.append("# TYPE orchestrator_tokens_total counter")
        lines.append(f'orchestrator_tokens_total{{type="input"}} {totals["total_input_tokens"]}')
        lines.append(f'orchestrator_tokens_total{{type="output"}} {totals["total_output_tokens"]}')

        # --- Cost ---
        lines.append("# HELP orchestrator_cost_usd_total Total cost in USD")
        lines.append("# TYPE orchestrator_cost_usd_total counter")
        lines.append(f"orchestrator_cost_usd_total {totals['total_cost_usd']:.6f}")

        # --- Per-model metrics ---
        lines.append("# HELP orchestrator_model_requests_total Requests per model")
        lines.append("# TYPE orchestrator_model_requests_total counter")
        lines.append("# HELP orchestrator_model_tokens_total Tokens per model")
        lines.append("# TYPE orchestrator_model_tokens_total counter")
        lines.append("# HELP orchestrator_model_cost_usd_total Cost per model")
        lines.append("# TYPE orchestrator_model_cost_usd_total counter")
        lines.append("# HELP orchestrator_model_speed_avg Average output tokens/s per model")
        lines.append("# TYPE orchestrator_model_speed_avg gauge")
        for model, stats in per_model.items():
            m = model.replace('"', '\\"')
            lines.append(f'orchestrator_model_requests_total{{model="{m}"}} {stats["requests"]}')
            lines.append(f'orchestrator_model_tokens_total{{model="{m}"}} {stats["tokens"]}')
            lines.append(
                f'orchestrator_model_cost_usd_total{{model="{m}"}} {stats["cost_usd"]:.6f}'
            )
            lines.append(f'orchestrator_model_speed_avg{{model="{m}"}} {stats.get("avg_speed", 0)}')

        # --- Per-agent metrics ---
        lines.append("# HELP orchestrator_agent_requests_total Requests per agent")
        lines.append("# TYPE orchestrator_agent_requests_total counter")
        lines.append("# HELP orchestrator_agent_tokens_total Tokens per agent")
        lines.append("# TYPE orchestrator_agent_tokens_total counter")
        lines.append("# HELP orchestrator_agent_cost_usd_total Cost per agent")
        lines.append("# TYPE orchestrator_agent_cost_usd_total counter")
        for agent, stats in per_agent.items():
            a = agent.replace('"', '\\"')
            lines.append(f'orchestrator_agent_requests_total{{agent="{a}"}} {stats["requests"]}')
            lines.append(f'orchestrator_agent_tokens_total{{agent="{a}"}} {stats["tokens"]}')
            lines.append(
                f'orchestrator_agent_cost_usd_total{{agent="{a}"}} {stats["cost_usd"]:.6f}'
            )

        # --- Agent status from event bus ---
        lines.append("# HELP orchestrator_agent_status Current agent status (1=active)")
        lines.append("# TYPE orchestrator_agent_status gauge")
        for name, info in snap.get("agents", {}).items():
            status = info.get("status", "unknown")
            a = name.replace('"', '\\"')
            lines.append(f'orchestrator_agent_status{{agent="{a}",status="{status}"}} 1')

        # --- Orchestrator status ---
        lines.append("# HELP orchestrator_status Current orchestrator status")
        lines.append("# TYPE orchestrator_status gauge")
        status = snap.get("orchestrator_status", "idle")
        lines.append(f'orchestrator_status{{status="{status}"}} 1')

        # --- Event count ---
        lines.append("# HELP orchestrator_events_total Total events emitted")
        lines.append("# TYPE orchestrator_events_total counter")
        lines.append(f"orchestrator_events_total {snap.get('event_count', 0)}")

        # --- Error count from event history ---
        error_count = sum(
            1 for e in bus.get_history() if e.event_type.value in ("agent.error", "agent.stalled")
        )
        lines.append("# HELP orchestrator_errors_total Total agent errors and stalls")
        lines.append("# TYPE orchestrator_errors_total counter")
        lines.append(f"orchestrator_errors_total {error_count}")

        # --- Cache stats ---
        cache = snap.get("cache", {})
        lines.append("# HELP orchestrator_cache_hits_total Cache hits")
        lines.append("# TYPE orchestrator_cache_hits_total counter")
        lines.append(f"orchestrator_cache_hits_total {cache.get('hits', 0)}")
        lines.append("# HELP orchestrator_cache_misses_total Cache misses")
        lines.append("# TYPE orchestrator_cache_misses_total counter")
        lines.append(f"orchestrator_cache_misses_total {cache.get('misses', 0)}")

        # --- Task delegation (cooperation) ---
        tasks = snap.get("tasks", [])
        completed_tasks = sum(1 for t in tasks if t.get("status") == "completed")
        failed_tasks = sum(1 for t in tasks if t.get("status") == "failed")
        pending_tasks = sum(1 for t in tasks if t.get("status") == "pending")
        lines.append("# HELP orchestrator_tasks_total Task delegation counts by status")
        lines.append("# TYPE orchestrator_tasks_total gauge")
        lines.append(f'orchestrator_tasks_total{{status="completed"}} {completed_tasks}')
        lines.append(f'orchestrator_tasks_total{{status="failed"}} {failed_tasks}')
        lines.append(f'orchestrator_tasks_total{{status="pending"}} {pending_tasks}')

        from starlette.responses import Response

        return Response(
            content="\n".join(lines) + "\n",
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/api/snapshot")
    async def snapshot():
        return JSONResponse(content=bus.get_snapshot())

    @app.get("/api/events")
    async def events(limit: int = 100):
        history = bus.get_history()
        return JSONResponse(content=[e.to_dict() for e in history[-limit:]])

    @app.get("/api/agents")
    async def agents():
        return JSONResponse(content=get_agent_registry())

    # --- Agent Execution (v0.3.0) ---

    @app.get("/api/agent/config")
    async def agent_config():
        """Return agent configs with available skills and tools for the UI."""
        registry = get_agent_registry()
        skill_reg = create_skill_registry()
        skills_info = [
            {"name": s, "description": skill_reg.get(s).description if skill_reg.get(s) else ""}
            for s in skill_reg.list_skills()
        ]
        return JSONResponse(
            content={
                "agents": registry.get("agents", []),
                "skills": skills_info,
            }
        )

    @app.post("/api/agent/run")
    async def agent_run(body: dict):
        """Run an agent on a task with real-time events."""
        agent_name = body.get("agent", "").strip()
        task_desc = body.get("task", "").strip()
        model = body.get("model", "")
        provider_type = body.get("provider", "ollama")
        tools = body.get("tools")  # list[str] or None = all
        max_steps = body.get("max_steps", 10)

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

        # Get role from agent registry
        registry = get_agent_registry()
        agent_info = next(
            (a for a in registry.get("agents", []) if a["name"] == agent_name),
            None,
        )
        role = agent_info.get("description", "") if agent_info else ""

        try:
            job_logger.touch()
            result = await run_agent(
                agent_name=agent_name,
                task_description=task_desc,
                provider=provider,
                role=role,
                tools=tools,
                max_steps=max_steps,
                event_bus=bus,
                working_directory=str(job_logger.session_dir),
            )
            job_logger.log(
                "agent_run",
                {
                    "agent": agent_name,
                    "task": task_desc,
                    "model": model,
                    "provider": provider_type,
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
                content={"success": False, "error": str(exc)},
                status_code=500,
            )

    @app.post("/api/team/run")
    async def team_run(body: dict):
        """Run a multi-agent team on a task (team-lead + sub-agents with tools)."""
        task_desc = body.get("task", "").strip()
        model = body.get("model", "")
        provider_type = body.get("provider", "openrouter")

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

        ollama_url = _get_ollama_url()
        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
        provider = _make_provider(model, provider_type, ollama_url, openrouter_key)

        try:
            job_logger.touch()
            result = await run_team(
                task_description=task_desc,
                provider=provider,
                event_bus=bus,
                working_directory=str(job_logger.session_dir),
            )
            job_logger.log(
                "team_run",
                {
                    "task": task_desc,
                    "model": model,
                    "provider": provider_type,
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

            # Record per-agent costs
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
            return JSONResponse(content=result)
        except Exception as exc:
            job_logger.log(
                "team_run",
                {
                    "task": task_desc,
                    "model": model,
                    "provider": provider_type,
                    "result": {"success": False, "error": str(exc)},
                },
            )
            return JSONResponse(
                content={"success": False, "error": str(exc)},
                status_code=500,
            )

    @app.post("/api/skill/invoke")
    async def skill_invoke(body: dict):
        """Invoke a skill directly (without an agent)."""
        skill_name = body.get("skill", "").strip()
        params = body.get("params", {})

        if not skill_name:
            return JSONResponse(
                content={"success": False, "error": "Skill name required"},
                status_code=400,
            )

        skill_reg = create_skill_registry(
            allowed_commands=[
                "ls",
                "cat",
                "head",
                "tail",
                "wc",
                "grep",
                "find",
                "python",
                "python3",
                "pytest",
                "ruff",
                "git",
            ]
        )
        result = await skill_reg.execute(skill_name, params)

        # Emit tool call event
        await bus.emit(
            Event(
                event_type=EventType.AGENT_TOOL_CALL,
                agent_name="manual",
                data={
                    "tool_name": skill_name,
                    "arguments": {k: str(v)[:200] for k, v in params.items()},
                },
            )
        )
        await bus.emit(
            Event(
                event_type=EventType.AGENT_TOOL_RESULT,
                agent_name="manual",
                data={
                    "tool_name": skill_name,
                    "success": result.success,
                    "output": str(result)[:500],
                },
            )
        )

        return JSONResponse(
            content={
                "success": result.success,
                "output": str(result.output)[:5000] if result.output else "",
                "error": result.error,
            }
        )

    @app.post("/api/cost/preview")
    async def cost_preview(body: dict):
        """Estimate cost for running an agent task."""
        model = body.get("model", "")
        provider_type = body.get("provider", "ollama")
        max_steps = body.get("max_steps", 10)

        if provider_type == "ollama":
            return JSONResponse(
                content={
                    "estimated_cost_usd": 0.0,
                    "provider": "ollama",
                    "note": "Local models are free",
                }
            )

        ollama_url = _get_ollama_url()
        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
        provider = _make_provider(model, provider_type, ollama_url, openrouter_key)

        # Rough estimate: ~2000 input + ~500 output tokens per step
        est_input = 2000 * max_steps
        est_output = 500 * max_steps
        est_cost = provider.estimate_cost(est_input, est_output)

        return JSONResponse(
            content={
                "estimated_cost_usd": round(est_cost, 6),
                "estimated_input_tokens": est_input,
                "estimated_output_tokens": est_output,
                "model": model,
                "max_steps": max_steps,
            }
        )

    # --- Models: Ollama + OpenRouter ---

    @app.get("/api/models")
    async def models():
        """List all available models (Ollama local + OpenRouter cloud)."""
        ollama_url = _get_ollama_url()
        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")

        # Fetch in parallel
        ollama_task = asyncio.create_task(list_ollama_models(ollama_url))
        openrouter_task = asyncio.create_task(list_openrouter_models(openrouter_key))

        ollama_models = await ollama_task
        openrouter_models = await openrouter_task

        return JSONResponse(
            content={
                "ollama": ollama_models,
                "openrouter": openrouter_models,
            }
        )

    # --- OpenRouter Pricing ---

    @app.get("/api/openrouter/pricing")
    async def openrouter_pricing(q: str = ""):
        """Fetch live model pricing from OpenRouter public API."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get("https://openrouter.ai/api/v1/models")
                resp.raise_for_status()
                data = resp.json()

            models = []
            for m in data.get("data", []):
                pricing = m.get("pricing", {})
                prompt_cost = float(pricing.get("prompt", 0)) * 1_000_000
                completion_cost = float(pricing.get("completion", 0)) * 1_000_000
                name = m.get("id", "")
                # Filter by query if provided
                if q and q.lower() not in name.lower():
                    continue
                models.append(
                    {
                        "id": name,
                        "name": m.get("name", name),
                        "input_per_m": round(prompt_cost, 4),
                        "output_per_m": round(completion_cost, 4),
                        "context": m.get("context_length", 0),
                        "is_free": prompt_cost == 0 and completion_cost == 0,
                    }
                )

            # Sort: free first, then by input cost
            models.sort(key=lambda x: (not x["is_free"], x["input_per_m"]))
            return JSONResponse(content={"models": models, "count": len(models)})
        except Exception:
            logger.exception("OpenRouter pricing fetch failed")
            return JSONResponse(
                content={"error": "Failed to fetch pricing", "models": []},
                status_code=502,
            )

    # --- Ollama Model Management ---

    @app.post("/api/ollama/pull")
    async def ollama_pull(body: dict):
        """Pull a model from Ollama."""
        model_name = body.get("name", "").strip()
        if not model_name:
            return JSONResponse(content={"error": "No model name"}, status_code=400)

        ollama_url = _get_ollama_url()
        import httpx

        try:
            async with httpx.AsyncClient(timeout=600.0) as client:
                resp = await client.post(
                    f"{ollama_url}/api/pull",
                    json={"name": model_name, "stream": False},
                )
                resp.raise_for_status()
                return JSONResponse(
                    content={"success": True, "status": resp.json().get("status", "ok")}
                )
        except Exception:
            logger.exception("Ollama pull failed for model %r", model_name)
            return JSONResponse(
                content={"success": False, "error": "Failed to pull model"}, status_code=500
            )

    @app.delete("/api/ollama/model")
    async def ollama_delete(body: dict):
        """Delete a model from Ollama."""
        model_name = body.get("name", "").strip()
        if not model_name:
            return JSONResponse(content={"error": "No model name"}, status_code=400)

        ollama_url = _get_ollama_url()
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.delete(
                    f"{ollama_url}/api/delete",
                    json={"name": model_name},
                )
                resp.raise_for_status()
                return JSONResponse(content={"success": True})
        except Exception:
            logger.exception("Ollama delete failed for model %r", model_name)
            return JSONResponse(
                content={"success": False, "error": "Failed to delete model"}, status_code=500
            )

    # --- File Context ---

    @app.get("/api/files")
    async def list_files(path: str = ""):
        """List files in the project directory."""
        base = PROJECT_ROOT.resolve()
        target = (base / path).resolve()

        # Security: prevent path traversal
        if not target.is_relative_to(base):
            return JSONResponse(content={"error": "Path outside project"}, status_code=400)

        if not target.is_dir():
            return JSONResponse(content={"error": "Not a directory"}, status_code=404)

        items = []
        for entry in sorted(target.iterdir()):
            rel = entry.relative_to(base)
            # Skip hidden dirs, __pycache__, node_modules, .git
            if any(
                part.startswith(".") or part in ("__pycache__", "node_modules", ".git")
                for part in rel.parts
            ):
                continue
            items.append(
                {
                    "name": entry.name,
                    "path": str(rel),
                    "is_dir": entry.is_dir(),
                    "size": entry.stat().st_size if entry.is_file() else 0,
                }
            )
        return JSONResponse(content={"path": path, "items": items})

    @app.get("/api/file")
    async def read_file(path: str):
        """Read a file's content."""
        base = PROJECT_ROOT.resolve()
        target = (base / path).resolve()

        # Security: prevent path traversal
        if not target.is_relative_to(base):
            return JSONResponse(content={"error": "Path outside project"}, status_code=400)

        if not target.is_file():
            return JSONResponse(content={"error": "Not a file"}, status_code=404)

        # Limit file size to 100KB
        if target.stat().st_size > 100_000:
            return JSONResponse(content={"error": "File too large (>100KB)"}, status_code=400)

        try:
            content = target.read_text(errors="replace")
            return JSONResponse(content={"path": path, "content": content})
        except Exception:
            logger.exception("Failed to read file: %r", path)
            return JSONResponse(content={"error": "Failed to read file"}, status_code=500)

    # --- Conversations (Multi-turn) ---

    @app.post("/api/conversation/new")
    async def new_conversation():
        conv_id = str(uuid.uuid4())[:8]
        await usage_db.create_conversation(conv_id)
        return JSONResponse(content={"conversation_id": conv_id})

    @app.get("/api/conversation/{conv_id}")
    async def get_conversation(conv_id: str):
        msgs = await usage_db.get_conversation(conv_id)
        return JSONResponse(content={"conversation_id": conv_id, "messages": msgs})

    # --- Presets ---

    @app.get("/api/presets")
    async def presets():
        return JSONResponse(
            content={
                "presets": [
                    {
                        "id": "explain",
                        "label": "Explain",
                        "icon": "?",
                        "prompt": "Explain this code clearly and concisely:\n\n{context}",
                        "graph": "chat",
                    },
                    {
                        "id": "review",
                        "label": "Review",
                        "icon": "R",
                        "prompt": "Review this code for bugs, security issues, and quality:\n\n{context}",
                        "graph": "review",
                    },
                    {
                        "id": "test",
                        "label": "Tests",
                        "icon": "T",
                        "prompt": "Write unit tests for this code:\n\n{context}",
                        "graph": "chat",
                    },
                    {
                        "id": "refactor",
                        "label": "Refactor",
                        "icon": "F",
                        "prompt": "Refactor this code to be cleaner and more maintainable:\n\n{context}",
                        "graph": "chain",
                    },
                    {
                        "id": "docs",
                        "label": "Docs",
                        "icon": "D",
                        "prompt": "Write documentation (docstrings + usage examples) for this code:\n\n{context}",
                        "graph": "chat",
                    },
                    {
                        "id": "fix",
                        "label": "Fix",
                        "icon": "!",
                        "prompt": "Find and fix bugs in this code:\n\n{context}",
                        "graph": "chain",
                    },
                ]
            }
        )

    # --- Prompt execution (non-streaming) ---

    @app.post("/api/prompt")
    async def prompt(body: dict):
        user_prompt = body.get("prompt", "").strip()
        model = body.get("model", "")
        provider_type = body.get("provider", "ollama")
        graph_type = body.get("graph_type", "auto")
        conv_id = body.get("conversation_id")
        file_context = body.get("file_context", "")

        if not user_prompt:
            return JSONResponse(
                content={"success": False, "error": "Empty prompt"}, status_code=400
            )
        if not model:
            return JSONResponse(
                content={"success": False, "error": "No model selected"}, status_code=400
            )

        # Build full prompt with file context
        full_prompt = user_prompt
        if file_context:
            full_prompt = f"{user_prompt}\n\n```\n{file_context}\n```"

        # Add conversation history context
        history_context = ""
        if conv_id:
            recent = await usage_db.get_recent_messages(conv_id, limit=6)
            if recent:
                history_context = "\n".join(
                    f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:500]}"
                    for m in recent
                )

        if history_context:
            full_prompt = (
                f"Previous conversation:\n{history_context}\n\nCurrent request:\n{full_prompt}"
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
        )

        job_logger.log(
            "prompt",
            {
                "prompt": user_prompt,
                "model": model,
                "provider": provider_type,
                "graph_type": graph_type,
                "result": result,
            },
        )

        # Record usage
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

        # Save to conversation (PostgreSQL)
        if conv_id:
            await usage_db.append_message(conv_id, "user", user_prompt)
            if result.get("success"):
                await usage_db.append_message(conv_id, "assistant", result.get("output", ""))

        return JSONResponse(content=result)

    # --- Graph control: Reset + Replay Node ---

    @app.post("/api/graph/reset")
    async def graph_reset():
        """Clear all event history and agent/task state."""
        bus._history.clear()
        for q in bus._subscribers:
            while not q.empty():
                try:
                    q.get_nowait()
                except Exception:
                    break
        # Notify clients
        await bus.emit(
            Event(
                event_type=EventType.ORCHESTRATOR_END,
                data={"success": True, "reset": True},
            )
        )
        return JSONResponse(content={"success": True})

    @app.post("/api/graph/replay")
    async def graph_replay(body: dict):
        """Replay a single node from the last graph run."""
        node_name = body.get("node", "").strip()
        if not node_name:
            return JSONResponse(
                content={"success": False, "error": "No node specified"}, status_code=400
            )
        result = await replay_node(node_name=node_name, event_bus=bus)
        return JSONResponse(content=result)

    @app.get("/api/graph/last-run")
    async def graph_last_run():
        """Get info about the last graph execution."""
        return JSONResponse(content=get_last_run_info())

    # --- Streaming via WebSocket ---

    @app.websocket("/ws/stream")
    async def stream_endpoint(ws: WebSocket):
        """Stream LLM responses token-by-token (authenticated)."""
        # Check auth BEFORE accepting the connection
        ws_user = check_ws_auth(ws, _ws_api_keys)
        if not ws_user:
            await ws.close(code=1008, reason="Authentication required")
            return

        # Close previous stream connection (prevents zombie sockets)
        old_ws = _active_ws.get("/ws/stream")
        if old_ws:
            try:
                await old_ws.close(code=1001, reason="Replaced by new connection")
            except Exception:
                pass

        await ws.accept()
        _active_ws["/ws/stream"] = ws
        try:
            while True:
                data = await ws.receive_json()
                prompt_text = data.get("prompt", "").strip()
                model = data.get("model", "")
                provider_type = data.get("provider", "ollama")
                system = data.get(
                    "system", "You are a helpful AI assistant. Be concise and direct."
                )
                conv_id = data.get("conversation_id")
                file_context = data.get("file_context", "")

                if not prompt_text or not model:
                    await ws.send_json({"type": "error", "error": "Missing prompt or model"})
                    continue

                job_logger.touch()

                # Build prompt with context
                full_prompt = prompt_text
                if file_context:
                    full_prompt = f"{prompt_text}\n\n```\n{file_context}\n```"

                # Add conversation history
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

                    # Emit graph events
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

                    # Save to conversation (PostgreSQL)
                    if conv_id:
                        await usage_db.append_message(conv_id, "user", prompt_text)
                        await usage_db.append_message(conv_id, "assistant", full_response)

                    # Emit token update
                    await bus.emit(
                        Event(
                            event_type=EventType.TOKEN_UPDATE, data={"total_tokens": total_tokens}
                        )
                    )

                except Exception as e:
                    await ws.send_json({"type": "error", "error": str(e)})

        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            if _active_ws.get("/ws/stream") is ws:
                _active_ws.pop("/ws/stream", None)

    # --- Events WebSocket ---

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        # Check auth BEFORE accepting the connection
        ws_user = check_ws_auth(ws, _ws_api_keys)
        if not ws_user:
            await ws.close(code=1008, reason="Authentication required")
            return

        # Close previous connection (prevents zombie sockets eating browser connection slots)
        old_ws = _active_ws.get("/ws")
        if old_ws:
            try:
                await old_ws.close(code=1001, reason="Replaced by new connection")
            except Exception:
                pass

        await ws.accept()
        _active_ws["/ws"] = ws
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
            if _active_ws.get("/ws") is ws:
                _active_ws.pop("/ws", None)

    return app
