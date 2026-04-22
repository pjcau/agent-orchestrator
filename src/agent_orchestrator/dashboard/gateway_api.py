"""Gateway API router — REST management endpoints.

Handles configuration, users, jobs, MCP, metrics, and other
management operations. Separated from agent runtime for
independent scaling.

Shared state is accessed via ``request.app.state``:
    - ``request.app.state.bus``              — EventBus instance
    - ``request.app.state.usage_db``         — UsageDB instance
    - ``request.app.state.job_logger``       — JobLogger instance
    - ``request.app.state.conv_manager``     — ConversationManager instance
    - ``request.app.state.alert_handler``    — AlertHandler instance
    - ``request.app.state.run_manager``      — RunManager instance
    - ``request.app.state.frontend_error_count`` — list[int] counter
    - ``request.app.state.store_holder``     — list[BaseStore | None] cell
    - ``request.app.state.sandbox_manager``  — SandboxManager | None
    - ``request.app.state.mcp_client_manager`` — MCPClientManager instance
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path as _Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..core.mcp_server import MCPServerRegistry
from .agent_runner import create_skill_registry, run_agent
from .agents_registry import get_agent_registry
from .events import Event, EventType
from .graphs import (
    _make_provider,
    get_last_run_info,
    list_ollama_models,
    list_openrouter_models,
    replay_node,
)

logger = logging.getLogger(__name__)

gateway_router = APIRouter(prefix="/api", tags=["gateway"])

# ---------------------------------------------------------------------------
# Module-level shared state for MCP registry
# (populated lazily per-request from app.state when used standalone)
# ---------------------------------------------------------------------------
_mcp_registry = MCPServerRegistry()


def _sanitize_log(value: str) -> str:
    """Sanitize user-controlled values for safe logging (prevent log injection)."""
    return value.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


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


def _ensure_mcp_registry() -> MCPServerRegistry:
    """Lazily populate the module-level MCP registry from agent/skill registries."""
    if _mcp_registry.list_tools():
        return _mcp_registry
    agent_reg = get_agent_registry()
    agent_configs = {}
    for agent in agent_reg.get("agents", []):
        agent_configs[agent["name"]] = {"role": agent.get("description", "")}
    _mcp_registry.register_agent_tools(agent_configs)
    skill_reg = create_skill_registry(allowed_commands=[])
    _mcp_registry.register_skill_tools(skill_reg.list_skills(), skill_reg)
    return _mcp_registry


# ---------------------------------------------------------------------------
# Health check (no prefix — mounted at root)
# ---------------------------------------------------------------------------

health_router = APIRouter(tags=["gateway"])


@health_router.get("/health")
async def health():
    """Health check endpoint (unauthenticated) for load balancers and CI/CD."""
    return JSONResponse(content={"status": "ok"})


# ---------------------------------------------------------------------------
# Metrics endpoint (no /api prefix — mounted at root)
# ---------------------------------------------------------------------------

metrics_router = APIRouter(tags=["gateway"])


@metrics_router.get("/metrics")
async def prometheus_metrics(request: Request):
    """Expose metrics in Prometheus text exposition format."""
    usage_db = request.app.state.usage_db
    bus = request.app.state.bus
    frontend_error_count = request.app.state.frontend_error_count

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
    for model_id, stats in per_model.items():
        m = model_id.replace('"', '\\"')
        lines.append(f'orchestrator_model_requests_total{{model="{m}"}} {stats["requests"]}')
        lines.append(f'orchestrator_model_tokens_total{{model="{m}"}} {stats["tokens"]}')
        lines.append(f'orchestrator_model_cost_usd_total{{model="{m}"}} {stats["cost_usd"]:.6f}')
        lines.append(f'orchestrator_model_speed_avg{{model="{m}"}} {stats.get("avg_speed", 0)}')

    # --- Per-agent metrics ---
    lines.append("# HELP orchestrator_agent_requests_total Requests per agent")
    lines.append("# TYPE orchestrator_agent_requests_total counter")
    lines.append("# HELP orchestrator_agent_tokens_total Tokens per agent")
    lines.append("# TYPE orchestrator_agent_tokens_total counter")
    lines.append("# HELP orchestrator_agent_cost_usd_total Cost per agent")
    lines.append("# TYPE orchestrator_agent_cost_usd_total counter")
    for agent_id, stats in per_agent.items():
        a = agent_id.replace('"', '\\"')
        lines.append(f'orchestrator_agent_requests_total{{agent="{a}"}} {stats["requests"]}')
        lines.append(f'orchestrator_agent_tokens_total{{agent="{a}"}} {stats["tokens"]}')
        lines.append(f'orchestrator_agent_cost_usd_total{{agent="{a}"}} {stats["cost_usd"]:.6f}')

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

    # --- Frontend errors ---
    lines.append("# HELP orchestrator_frontend_errors_total Frontend JS errors reported")
    lines.append("# TYPE orchestrator_frontend_errors_total counter")
    lines.append(f"orchestrator_frontend_errors_total {frontend_error_count[0]}")

    # --- LLM call duration histogram (from tracing) ---
    from .tracing_metrics import get_tracing_metrics

    tm = get_tracing_metrics()
    lines.append("# HELP orchestrator_llm_call_duration_seconds LLM call latency")
    lines.append("# TYPE orchestrator_llm_call_duration_seconds histogram")
    llm_durations = tm.get("llm_durations", {})
    if llm_durations:
        for provider, buckets in llm_durations.items():
            p = provider.replace('"', '\\"')
            lines.append(
                f'orchestrator_llm_call_duration_seconds_count{{provider="{p}"}} {buckets["count"]}'
            )
            lines.append(
                f'orchestrator_llm_call_duration_seconds_sum{{provider="{p}"}} {buckets["sum"]:.3f}'
            )
    else:
        lines.append('orchestrator_llm_call_duration_seconds_count{provider="none"} 0')
        lines.append('orchestrator_llm_call_duration_seconds_sum{provider="none"} 0')

    # --- Graph node duration histogram ---
    lines.append("# HELP orchestrator_graph_node_duration_seconds Graph node execution latency")
    lines.append("# TYPE orchestrator_graph_node_duration_seconds histogram")
    node_durations = tm.get("node_durations", {})
    if node_durations:
        for node, buckets in node_durations.items():
            n = node.replace('"', '\\"')
            lines.append(
                f'orchestrator_graph_node_duration_seconds_count{{node="{n}"}} {buckets["count"]}'
            )
            lines.append(
                f'orchestrator_graph_node_duration_seconds_sum{{node="{n}"}} {buckets["sum"]:.3f}'
            )
    else:
        lines.append('orchestrator_graph_node_duration_seconds_count{node="none"} 0')
        lines.append('orchestrator_graph_node_duration_seconds_sum{node="none"} 0')

    # --- Agent stall counter by category ---
    lines.append("# HELP orchestrator_agent_stalls_total Agent stall count by category")
    lines.append("# TYPE orchestrator_agent_stalls_total counter")
    stalls = tm.get("stalls_by_category", {})
    if stalls:
        for cat, count in stalls.items():
            c = cat.replace('"', '\\"')
            lines.append(f'orchestrator_agent_stalls_total{{category="{c}"}} {count}')
    else:
        lines.append('orchestrator_agent_stalls_total{category="none"} 0')

    from starlette.responses import Response

    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# ---------------------------------------------------------------------------
# Session / Jobs
# ---------------------------------------------------------------------------


@gateway_router.get("/session")
async def session(request: Request):
    """Return current session info (ID and jobs directory)."""
    job_logger = request.app.state.job_logger
    return JSONResponse(
        content={
            "session_id": job_logger.session_id,
            "jobs_dir": str(job_logger.session_dir),
        }
    )


@gateway_router.get("/session/history")
async def session_history(request: Request):
    """Return all job records from the current session for chat restoration."""
    job_logger = request.app.state.job_logger
    records = job_logger.get_history()
    return JSONResponse(content={"session_id": job_logger.session_id, "records": records})


@gateway_router.get("/jobs/list")
async def jobs_list(request: Request):
    """List all job sessions."""
    job_logger = request.app.state.job_logger
    sessions = job_logger.list_sessions()
    return JSONResponse(content={"sessions": sessions})


@gateway_router.get("/jobs/{session_id}")
async def jobs_detail(session_id: str, request: Request):
    """Load all records from a specific session."""
    job_logger = request.app.state.job_logger
    records = job_logger.load_session(session_id)
    if not records:
        return JSONResponse(content={"error": "Session not found"}, status_code=404)
    return JSONResponse(content={"session_id": session_id, "records": records})


@gateway_router.post("/jobs/{session_id}/switch")
async def jobs_switch(session_id: str, request: Request):
    """Switch to an existing session to continue work in it."""
    job_logger = request.app.state.job_logger
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


@gateway_router.post("/jobs/{session_id}/restore")
async def jobs_restore_conversation(session_id: str, request: Request):
    """Restore conversation memory from a session's job records.

    Reads all prompt/stream/agent/team records from the session,
    extracts user prompts and assistant outputs, and re-hydrates
    the ConversationManager so subsequent requests have full context.

    Returns the conversation_id (new or recovered from records).
    """
    from ..core.conversation import ConversationMessage

    job_logger = request.app.state.job_logger
    conv_manager = request.app.state.conv_manager

    records = job_logger.load_session(session_id)
    if not records:
        return JSONResponse(
            content={"success": False, "error": "Session not found"},
            status_code=404,
        )

    # Try to recover an existing conversation_id from the records
    recovered_conv_id = None
    for rec in records:
        cid = rec.get("conversation_id")
        if cid:
            recovered_conv_id = cid
            break

    conv_id = recovered_conv_id or str(uuid.uuid4())[:8]

    # Re-hydrate conversation from job records
    messages: list[ConversationMessage] = []
    for rec in records:
        job_type = rec.get("job_type", "")
        result = rec.get("result", {})

        if job_type in ("prompt", "stream"):
            user_text = rec.get("prompt", "")
            assistant_text = result.get("output", "") if result.get("success") is not False else ""
        elif job_type == "agent_run":
            user_text = rec.get("task", "")
            assistant_text = result.get("output", "") if result.get("success") else ""
        elif job_type == "team_run":
            user_text = rec.get("task", "")
            assistant_text = result.get("output", "") if result.get("success") else ""
        else:
            continue

        if user_text:
            messages.append(
                ConversationMessage(
                    role="user",
                    content=user_text,
                    timestamp=rec.get("timestamp", 0.0),
                )
            )
        if assistant_text:
            messages.append(
                ConversationMessage(
                    role="assistant",
                    content=assistant_text,
                    timestamp=rec.get("timestamp", 0.0),
                )
            )

    if messages:
        await conv_manager._save_thread(conv_id, messages)

    return JSONResponse(
        content={
            "success": True,
            "conversation_id": conv_id,
            "messages_restored": len(messages),
            "recovered_existing": recovered_conv_id is not None,
        }
    )


@gateway_router.delete("/jobs/{session_id}")
async def jobs_delete(session_id: str, request: Request):
    """Delete a session and its files. DB metrics are preserved."""
    import shutil

    job_logger = request.app.state.job_logger
    session_dir = job_logger._base_dir / f"job_{session_id}"
    if not session_dir.exists() or not session_dir.is_dir():
        return JSONResponse(content={"error": "Session not found"}, status_code=404)
    if session_id == job_logger.session_id:
        return JSONResponse(
            content={"error": "Cannot delete the current active session"},
            status_code=400,
        )
    if not session_dir.resolve().is_relative_to(job_logger._base_dir.resolve()):
        return JSONResponse(content={"error": "Path outside jobs directory"}, status_code=400)
    file_count = sum(1 for f in session_dir.iterdir() if f.is_file())
    shutil.rmtree(session_dir)
    return JSONResponse(
        content={
            "success": True,
            "session_id": session_id,
            "files_deleted": file_count,
        }
    )


@gateway_router.get("/jobs/{session_id}/files")
async def jobs_files(session_id: str, request: Request):
    """List all files in a session directory (recursive tree)."""
    from pathlib import Path

    job_logger = request.app.state.job_logger
    session_dir = job_logger._base_dir / f"job_{session_id}"
    if not session_dir.exists() or not session_dir.is_dir():
        return JSONResponse(content={"error": "Session not found"}, status_code=404)

    def _build_tree(directory: Path) -> list[dict]:
        entries: list[dict] = []
        for entry in sorted(directory.iterdir(), key=lambda e: (e.is_file(), e.name.lower())):
            if entry.is_dir():
                children = _build_tree(entry)
                entries.append(
                    {
                        "name": entry.name,
                        "type": "directory",
                        "path": str(entry.relative_to(session_dir)),
                        "children": children,
                    }
                )
            elif entry.is_file():
                stat = entry.stat()
                entries.append(
                    {
                        "name": entry.name,
                        "type": "file",
                        "path": str(entry.relative_to(session_dir)),
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                        "is_json": entry.suffix == ".json",
                    }
                )
        return entries

    tree = _build_tree(session_dir)
    flat = []
    for f in sorted(session_dir.rglob("*")):
        if f.is_file():
            stat = f.stat()
            flat.append(
                {
                    "name": f.name,
                    "path": str(f.relative_to(session_dir)),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                    "is_json": f.suffix == ".json",
                }
            )
    return JSONResponse(content={"session_id": session_id, "files": flat, "tree": tree})


@gateway_router.get("/jobs/{session_id}/files/{filename:path}")
async def jobs_file_content(session_id: str, filename: str, request: Request):
    """Read content of a file in a session directory."""
    job_logger = request.app.state.job_logger
    session_dir = job_logger._base_dir / f"job_{session_id}"
    target = (session_dir / filename).resolve()
    if not target.is_relative_to(session_dir.resolve()):
        return JSONResponse(content={"error": "Path outside session"}, status_code=400)
    if not target.is_file():
        return JSONResponse(content={"error": "File not found"}, status_code=404)
    if target.stat().st_size > 500_000:
        return JSONResponse(content={"error": "File too large (>500KB)"}, status_code=400)
    try:
        content = target.read_text(errors="replace")
        return JSONResponse(
            content={"name": filename, "content": content, "size": target.stat().st_size}
        )
    except Exception:
        return JSONResponse(content={"error": "Failed to read file"}, status_code=500)


@gateway_router.get("/jobs/{session_id}/download")
async def jobs_download_zip(session_id: str, request: Request):
    """Download entire session as a ZIP archive."""
    import io
    import zipfile

    job_logger = request.app.state.job_logger
    session_dir = job_logger._base_dir / f"job_{session_id}"
    if not session_dir.exists() or not session_dir.is_dir():
        return JSONResponse(content={"error": "Session not found"}, status_code=404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(session_dir.iterdir()):
            if f.is_file():
                zf.write(f, f.name)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="session_{session_id}.zip"'},
    )


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@gateway_router.post("/upload")
async def upload_document(request: Request):
    """Upload a document and convert it to Markdown for LLM consumption.

    Accepts multipart/form-data with a 'file' field.
    Returns the converted Markdown content and metadata.

    Size limits: 10 MB file size, 50 pages PDF, 10,000 rows CSV/Excel.
    """
    from ..core.document_converter import (
        ContentLimitError,
        DependencyMissingError,
        DocumentConversionError,
        DocumentConverter,
        FileTooLargeError,
        MAX_FILE_SIZE_BYTES,
        UnsupportedFormatError,
    )

    job_logger = request.app.state.job_logger

    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        return JSONResponse(
            content={"error": "Content-Type must be multipart/form-data"},
            status_code=400,
        )

    form = await request.form()
    upload = form.get("file")
    if upload is None:
        return JSONResponse(
            content={"error": "No 'file' field in form data"},
            status_code=400,
        )

    filename = getattr(upload, "filename", None) or "unknown"
    data = await upload.read()

    if len(data) > MAX_FILE_SIZE_BYTES:
        size_mb = len(data) / (1024 * 1024)
        return JSONResponse(
            content={
                "error": f"File size {size_mb:.1f} MB exceeds maximum of "
                f"{MAX_FILE_SIZE_BYTES / (1024 * 1024):.0f} MB"
            },
            status_code=413,
        )

    converter = DocumentConverter(output_dir=str(job_logger.session_dir))
    try:
        result = await converter.convert_bytes(data, filename, save_dir=str(job_logger.session_dir))
        return JSONResponse(
            content={
                "success": True,
                "filename": filename,
                "file_type": result.file_type,
                "markdown_content": result.markdown_content,
                "markdown_path": result.markdown_path,
                "page_count": result.page_count,
                "row_count": result.row_count,
            }
        )
    except UnsupportedFormatError:
        return JSONResponse(content={"error": "Unsupported file format"}, status_code=400)
    except FileTooLargeError:
        return JSONResponse(content={"error": "File too large"}, status_code=413)
    except ContentLimitError:
        return JSONResponse(content={"error": "Content exceeds processing limits"}, status_code=413)
    except DependencyMissingError:
        return JSONResponse(content={"error": "Required dependency not available"}, status_code=501)
    except DocumentConversionError:
        return JSONResponse(content={"error": "Document conversion failed"}, status_code=500)


# ---------------------------------------------------------------------------
# Usage / Errors / Alerts
# ---------------------------------------------------------------------------


@gateway_router.get("/usage")
async def usage_stats(request: Request):
    """Return cumulative usage stats (tokens, cost, per-model, per-agent)."""
    usage_db = request.app.state.usage_db
    return JSONResponse(content=usage_db.get_summary())


@gateway_router.get("/errors")
async def agent_errors(request: Request):
    """Return recent agent errors and error summary."""
    usage_db = request.app.state.usage_db
    recent = await usage_db.get_recent_errors(limit=100)
    summary = await usage_db.get_error_summary()
    return JSONResponse(content={"recent": recent, "summary": summary})


@gateway_router.post("/alerts/webhook")
async def receive_alert_webhook(body: dict, request: Request):
    """Receive Grafana alert webhook and create GitHub issue for analysis."""
    alert_handler = request.app.state.alert_handler
    result = await alert_handler.handle_alert(body)
    return JSONResponse(content=result)


@gateway_router.get("/alerts/recent")
async def recent_alerts(request: Request):
    """Return recent alert records."""
    alert_handler = request.app.state.alert_handler
    return JSONResponse(content=alert_handler.get_recent_alerts())


@gateway_router.post("/errors/client")
async def report_client_error(body: dict, request: Request):
    """Receive and store frontend JavaScript errors."""
    usage_db = request.app.state.usage_db
    frontend_error_count = request.app.state.frontend_error_count

    component = str(body.get("component", "unknown"))[:100]
    message = str(body.get("message", ""))[:2000]
    source = str(body.get("source", ""))[:500]
    line = int(body.get("line", 0)) if isinstance(body.get("line"), (int, float)) else 0
    session_id = str(body.get("session_id", ""))[:100]

    await usage_db.record_error(
        session_id=session_id,
        agent="frontend",
        tool_name=component,
        error_type="frontend_error",
        error_message=f"{message} (at {source}:{line})",
        step_number=0,
        model="",
        provider="",
    )

    frontend_error_count[0] += 1
    return JSONResponse(content={"status": "recorded"})


# ---------------------------------------------------------------------------
# Snapshot / Cache / Events
# ---------------------------------------------------------------------------


@gateway_router.get("/snapshot")
async def snapshot(request: Request):
    bus = request.app.state.bus
    return JSONResponse(content=bus.get_snapshot())


@gateway_router.get("/cache/stats")
async def cache_stats():
    from .agent_runner import get_tool_cache
    from ..core.llm_nodes import get_llm_cache

    llm = get_llm_cache()
    tool = get_tool_cache()
    llm_stats = llm.get_stats().to_dict()
    tool_stats = tool.get_stats().to_dict()
    return JSONResponse(
        content={
            "llm": {**llm_stats, "entries": llm.size()},
            "tool": {**tool_stats, "entries": tool.size()},
            "combined": {
                "hits": llm_stats["hits"] + tool_stats["hits"],
                "misses": llm_stats["misses"] + tool_stats["misses"],
                "evictions": llm_stats["evictions"] + tool_stats["evictions"],
                "entries": llm.size() + tool.size(),
                "total_saved_tokens": llm_stats["total_saved_tokens"]
                + tool_stats["total_saved_tokens"],
            },
        }
    )


@gateway_router.post("/cache/clear")
async def cache_clear():
    from .agent_runner import get_tool_cache
    from ..core.llm_nodes import get_llm_cache

    llm_cleared = get_llm_cache().clear()
    tool_cleared = get_tool_cache().clear()
    return JSONResponse(
        content={
            "cleared": llm_cleared + tool_cleared,
            "llm_cleared": llm_cleared,
            "tool_cleared": tool_cleared,
        }
    )


@gateway_router.get("/events")
async def events(limit: int = 100, request: Request = None):
    bus = request.app.state.bus
    history = bus.get_history()
    return JSONResponse(content=[e.to_dict() for e in history[-limit:]])


# ---------------------------------------------------------------------------
# SSE Runs
# ---------------------------------------------------------------------------


@gateway_router.post("/runs")
async def create_run(body: dict, request: Request):
    """Create a new graph run and start streaming it.

    Accepts a JSON body with:
    - ``graph_id``: name of a built-in graph (optional, uses echo graph if absent)
    - ``input``: initial state dict passed to the graph
    - ``config``: optional execution config (thread_id, etc.)
    - ``stream_mode``: ``"events"`` (default) or ``"values"``
    - ``hitl``: optional HITL config dict (enabled, timeout_seconds, auto_approve)

    Returns ``{"run_id": "<uuid>"}`` immediately. Use ``GET /api/runs/{run_id}``
    to poll status and ``GET /api/runs/{run_id}/stream`` to subscribe to events.
    """
    from ..core.graph import StateGraph, START, END
    from .sse import HITLConfig

    run_manager = request.app.state.run_manager

    input_data = body.get("input") or {}
    config = body.get("config") or {}
    stream_mode = str(body.get("stream_mode", "events"))
    if stream_mode not in ("events", "values"):
        return JSONResponse(
            content={"error": "stream_mode must be 'events' or 'values'"},
            status_code=400,
        )

    hitl_raw = body.get("hitl") or {}
    hitl_cfg = HITLConfig(
        enabled=bool(hitl_raw.get("enabled", True)),
        timeout_seconds=int(hitl_raw.get("timeout_seconds", 300)),
        auto_approve=bool(hitl_raw.get("auto_approve", False)),
    )

    graph_id = str(body.get("graph_id", "echo"))
    try:
        from .graphs import build_run_graph

        compiled = build_run_graph(graph_id)
    except Exception:
        sg = StateGraph()

        async def _echo(state):
            return state

        sg.add_node("echo", _echo)
        sg.add_edge(START, "echo")
        sg.add_edge("echo", END)
        compiled = sg.compile()

    run_id = run_manager.create_run(
        graph=compiled,
        config=config,
        input_data=input_data,
        hitl_config=hitl_cfg,
        stream_mode=stream_mode,
    )
    return JSONResponse(content={"run_id": run_id}, status_code=201)


@gateway_router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    """Return status and result of a run.

    Returns 404 if the run does not exist (evicted or never created).
    """
    run_manager = request.app.state.run_manager
    info = run_manager.get_run(run_id)
    if info is None:
        return JSONResponse(content={"error": "Run not found"}, status_code=404)
    payload: dict = {
        "run_id": info.run_id,
        "status": info.status,
        "created_at": info.created_at,
    }
    if info.result is not None:
        payload["result"] = info.result
    if info.error is not None:
        payload["error"] = info.error
    if info.interrupt is not None:
        payload["interrupt"] = {
            "type": info.interrupt.interrupt_type.value,
            "message": info.interrupt.message,
            "node": info.interrupt.node,
            "options": info.interrupt.options,
        }
    return JSONResponse(content=payload)


@gateway_router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, request: Request, stream_mode: str = "events"):
    """SSE endpoint — streams graph execution events to the caller.

    The response is a ``text/event-stream`` with one JSON payload per
    ``data:`` line. Each event has an ``id:`` field for reconnection.

    Query parameters:
        stream_mode: ``"events"`` (node-level, default) or ``"values"``
            (full state snapshot per step). Overrides the mode set at
            run creation.

    Reconnection: send the ``Last-Event-ID`` header with the last
    received event id — the server emits a reconnect comment and
    continues from the queue tail.
    """
    run_manager = request.app.state.run_manager
    if run_manager.get_run(run_id) is None:
        return JSONResponse(content={"error": "Run not found"}, status_code=404)

    last_event_id = request.headers.get("last-event-id")

    async def _event_generator():
        async for chunk in run_manager.subscribe(run_id, last_event_id=last_event_id):
            yield chunk

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@gateway_router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, body: dict, request: Request):
    """Resume an interrupted (HITL) run with human input.

    Accepts ``{"human_input": {...}}`` and merges the dict into the
    graph state before continuing execution.

    Returns ``{"run_id": "<uuid>"}`` — may be the same run_id if the
    background task is still alive, or a new one if the server restarted.
    """
    run_manager = request.app.state.run_manager
    human_input = body.get("human_input") or {}
    try:
        resumed_id = await run_manager.resume_run(run_id, human_input)
    except ValueError:
        logger.warning("resume_run rejected for %r", _sanitize_log(run_id), exc_info=True)
        return JSONResponse(
            content={"error": "Cannot resume run (not found or not interrupted)"},
            status_code=400,
        )
    return JSONResponse(content={"run_id": resumed_id})


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


@gateway_router.get("/agents")
async def agents():
    return JSONResponse(content=get_agent_registry())


@gateway_router.get("/agent/config")
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


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


@gateway_router.get("/mcp/manifest")
async def mcp_manifest():
    """Export MCP server manifest for client discovery."""
    registry = _ensure_mcp_registry()
    return JSONResponse(content=registry.export_manifest())


@gateway_router.get("/mcp/tools")
async def mcp_tools():
    """List all MCP tools (agents + skills)."""
    registry = _ensure_mcp_registry()
    tools = registry.list_tools()
    return JSONResponse(
        content={
            "count": len(tools),
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in tools
            ],
        }
    )


@gateway_router.post("/mcp/tools/{tool_name}/invoke")
async def mcp_invoke_tool(tool_name: str, body: dict, request: Request):
    """Invoke an MCP tool by name.

    For skill-backed tools, executes the skill directly.
    For agent-backed tools, runs the agent with the given task.
    """
    bus = request.app.state.bus
    job_logger = request.app.state.job_logger
    usage_db = request.app.state.usage_db

    registry = _ensure_mcp_registry()
    tool = registry.get_tool(tool_name)
    if not tool:
        return JSONResponse(
            content={"error": f"MCP tool '{tool_name}' not found"},
            status_code=404,
        )

    params = body.get("params", body.get("arguments", {}))

    # Skill-backed tool: skill_{name}
    if tool_name.startswith("skill_"):
        skill_name = tool_name[len("skill_") :]
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
        await bus.emit(
            Event(
                event_type=EventType.AGENT_TOOL_CALL,
                agent_name="mcp",
                data={"tool_name": tool_name, "arguments": params},
            )
        )
        return JSONResponse(
            content={
                "tool": tool_name,
                "success": result.success,
                "output": str(result.output)[:5000] if result.output else "",
                "error": result.error,
            }
        )

    # Agent-backed tool: agent_run_{name}
    if tool_name.startswith("agent_run_"):
        task_text = params.get("task", "")
        model = params.get("model", "")
        provider_type = params.get("provider", "ollama")
        if not task_text:
            return JSONResponse(content={"error": "'task' parameter required"}, status_code=400)
        if not model:
            return JSONResponse(content={"error": "'model' parameter required"}, status_code=400)
        agent_name = tool_name[len("agent_run_") :]
        ollama_url = _get_ollama_url()
        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
        provider = _make_provider(model, provider_type, ollama_url, openrouter_key)

        agent_reg = get_agent_registry()
        agent_info = next((a for a in agent_reg.get("agents", []) if a["name"] == agent_name), None)
        role = agent_info.get("description", "") if agent_info else ""

        job_logger.touch()
        result = await run_agent(
            agent_name=agent_name,
            task_description=task_text,
            provider=provider,
            role=role,
            event_bus=bus,
            working_directory=str(job_logger.session_dir),
            usage_db=usage_db,
            session_id=job_logger.session_id,
        )
        return JSONResponse(
            content={
                "tool": tool_name,
                "success": result.get("success", False),
                "output": result.get("output", "")[:5000],
            }
        )

    return JSONResponse(
        content={"error": f"Cannot invoke tool '{tool_name}': unknown handler type"},
        status_code=400,
    )


# ---------------------------------------------------------------------------
# Cost preview
# ---------------------------------------------------------------------------


@gateway_router.post("/cost/preview")
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


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@gateway_router.get("/models")
async def models():
    """List all available models (Ollama local + OpenRouter cloud)."""
    ollama_url = _get_ollama_url()
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")

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


@gateway_router.get("/openrouter/pricing")
async def openrouter_pricing(q: str = ""):
    """Fetch live model pricing from OpenRouter public API."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://openrouter.ai/api/v1/models")
            resp.raise_for_status()
            data = resp.json()

        models_list = []
        for m in data.get("data", []):
            pricing = m.get("pricing", {})
            prompt_cost = float(pricing.get("prompt", 0)) * 1_000_000
            completion_cost = float(pricing.get("completion", 0)) * 1_000_000
            name = m.get("id", "")
            if q and q.lower() not in name.lower():
                continue
            models_list.append(
                {
                    "id": name,
                    "name": m.get("name", name),
                    "input_per_m": round(prompt_cost, 4),
                    "output_per_m": round(completion_cost, 4),
                    "context": m.get("context_length", 0),
                    "is_free": prompt_cost == 0 and completion_cost == 0,
                }
            )

        models_list.sort(key=lambda x: (not x["is_free"], x["input_per_m"]))
        return JSONResponse(content={"models": models_list, "count": len(models_list)})
    except Exception:
        logger.exception("OpenRouter pricing fetch failed")
        return JSONResponse(
            content={"error": "Failed to fetch pricing", "models": []},
            status_code=502,
        )


# ---------------------------------------------------------------------------
# Ollama model management
# ---------------------------------------------------------------------------


@gateway_router.post("/ollama/pull")
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
        logger.exception("Ollama pull failed for model %r", _sanitize_log(model_name))
        return JSONResponse(
            content={"success": False, "error": "Failed to pull model"}, status_code=500
        )


@gateway_router.delete("/ollama/model")
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
        logger.exception("Ollama delete failed for model %r", _sanitize_log(model_name))
        return JSONResponse(
            content={"success": False, "error": "Failed to delete model"}, status_code=500
        )


# ---------------------------------------------------------------------------
# File context (project files)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = _Path(__file__).parent.parent.parent.parent
_PROJECT_BASE = _PROJECT_ROOT.resolve()


@gateway_router.get("/files")
async def list_files(path: str = ""):
    """List files in the project directory."""
    import os as _os

    base_real = _os.path.realpath(str(_PROJECT_BASE))
    if not path:
        # Listing the project root; no user-tainted component.
        target_real = base_real
    else:
        if "\x00" in path or ".." in path or _os.path.isabs(path):
            return JSONResponse(content={"error": "Path traversal denied"}, status_code=400)
        joined = _os.path.realpath(_os.path.join(base_real, path))
        # CodeQL-recognized containment sanitizer.
        if not joined.startswith(base_real + _os.sep):
            return JSONResponse(content={"error": "Path traversal denied"}, status_code=400)
        target_real = joined

    if not _os.path.isdir(target_real):
        return JSONResponse(content={"error": "Not a directory"}, status_code=404)

    items = []
    try:
        entries = sorted(_os.listdir(target_real))
    except OSError:
        return JSONResponse(content={"error": "Not a directory"}, status_code=404)
    for name in entries:
        if name.startswith(".") or name in ("__pycache__", "node_modules", ".git"):
            continue
        entry_real = _os.path.realpath(_os.path.join(target_real, name))
        # Re-verify containment for each entry (defends against symlinks
        # pointing outside the project root). CodeQL-recognized sanitizer.
        if not entry_real.startswith(base_real + _os.sep):
            continue
        rel = _os.path.relpath(entry_real, base_real)
        try:
            stat_result = _os.stat(entry_real)
        except OSError:
            continue
        is_dir = _os.path.isdir(entry_real)
        items.append(
            {
                "name": name,
                "path": rel,
                "is_dir": is_dir,
                "size": stat_result.st_size if not is_dir else 0,
            }
        )
    return JSONResponse(content={"path": path, "items": items})


@gateway_router.get("/file")
async def read_file(path: str):
    """Read a file's content."""
    import os as _os

    # Inline containment check (CodeQL-recognized sanitizer pattern).
    if not path or "\x00" in path or ".." in path or _os.path.isabs(path):
        return JSONResponse(content={"error": "Path traversal denied"}, status_code=400)
    base_real = _os.path.realpath(str(_PROJECT_BASE))
    target_real = _os.path.realpath(_os.path.join(base_real, path))
    if not target_real.startswith(base_real + _os.sep):
        return JSONResponse(content={"error": "Path traversal denied"}, status_code=400)

    if not _os.path.isfile(target_real):
        return JSONResponse(content={"error": "Not a file"}, status_code=404)

    try:
        stat_result = _os.stat(target_real)
    except OSError:
        return JSONResponse(content={"error": "Not a file"}, status_code=404)
    if stat_result.st_size > 100_000:
        return JSONResponse(content={"error": "File too large (>100KB)"}, status_code=400)

    try:
        with open(target_real, encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        return JSONResponse(content={"path": path, "content": content})
    except Exception:
        logger.exception("Failed to read file: %r", _sanitize_log(path))
        return JSONResponse(content={"error": "Failed to read file"}, status_code=500)


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


@gateway_router.post("/conversation/new")
async def new_conversation(request: Request):
    usage_db = request.app.state.usage_db
    conv_id = str(uuid.uuid4())[:8]
    await usage_db.create_conversation(conv_id)
    return JSONResponse(content={"conversation_id": conv_id})


@gateway_router.get("/conversation/{conv_id}")
async def get_conversation(conv_id: str, request: Request):
    usage_db = request.app.state.usage_db
    conv_manager = request.app.state.conv_manager
    history = await conv_manager.get_history(conv_id)
    if history:
        msgs = [m.to_dict() for m in history]
    else:
        msgs = await usage_db.get_conversation(conv_id)
    return JSONResponse(content={"conversation_id": conv_id, "messages": msgs})


@gateway_router.delete("/conversation/{conv_id}")
async def clear_conversation(conv_id: str, request: Request):
    conv_manager = request.app.state.conv_manager
    await conv_manager.clear_thread(conv_id)
    return JSONResponse(content={"success": True, "conversation_id": conv_id})


@gateway_router.post("/conversation/{conv_id}/fork")
async def fork_conversation(conv_id: str, request: Request, body: dict = {}):
    conv_manager = request.app.state.conv_manager
    new_id = body.get("new_id")
    forked_id = await conv_manager.fork_thread(conv_id, new_id)
    return JSONResponse(content={"success": True, "source_id": conv_id, "forked_id": forked_id})


@gateway_router.get("/conversations")
async def list_conversations(request: Request):
    conv_manager = request.app.state.conv_manager
    threads = await conv_manager.list_threads()
    return JSONResponse(content={"threads": threads})


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


@gateway_router.get("/presets")
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


# ---------------------------------------------------------------------------
# Graph control
# ---------------------------------------------------------------------------


@gateway_router.post("/graph/reset")
async def graph_reset(request: Request):
    """Clear all event history and agent/task state."""
    bus = request.app.state.bus
    bus._history.clear()
    for q in bus._subscribers:
        while not q.empty():
            try:
                q.get_nowait()
            except Exception:
                break
    await bus.emit(
        Event(
            event_type=EventType.ORCHESTRATOR_END,
            data={"success": True, "reset": True},
        )
    )
    return JSONResponse(content={"success": True})


@gateway_router.post("/graph/replay")
async def graph_replay(body: dict, request: Request):
    """Replay a single node from the last graph run."""
    bus = request.app.state.bus
    node_name = body.get("node", "").strip()
    if not node_name:
        return JSONResponse(
            content={"success": False, "error": "No node specified"}, status_code=400
        )
    result = await replay_node(node_name=node_name, event_bus=bus)
    return JSONResponse(content=result)


@gateway_router.get("/graph/last-run")
async def graph_last_run():
    """Get info about the last graph execution."""
    return JSONResponse(content=get_last_run_info())


# ---------------------------------------------------------------------------
# Skill invoke
# ---------------------------------------------------------------------------


@gateway_router.get("/memory/namespaces")
async def memory_list_namespaces(request: Request):
    """List all distinct memory namespaces in the store."""
    store = request.app.state.store_holder[0]
    if store is None:
        return JSONResponse(content={"namespaces": []})
    namespaces = await store.alist_namespaces(limit=200)
    return JSONResponse(content={"namespaces": [list(ns) for ns in namespaces]})


@gateway_router.get("/memory/stats")
async def memory_stats(request: Request):
    """Return total entry count, namespace count, and backend type."""
    store = request.app.state.store_holder[0]
    if store is None:
        return JSONResponse(content={"total_entries": 0, "namespace_count": 0, "backend": "none"})
    namespaces = await store.alist_namespaces(limit=500)
    total = 0
    for ns in namespaces:
        results = await store.asearch(ns, limit=1000)
        total += len(results)
    return JSONResponse(
        content={
            "total_entries": total,
            "namespace_count": len(namespaces),
            "backend": type(store).__name__,
        }
    )


@gateway_router.get("/memory/{namespace:path}")
async def memory_list_entries(namespace: str, request: Request):
    """List entries in a given namespace (dot-separated path, e.g. agent/backend)."""
    store = request.app.state.store_holder[0]
    if store is None:
        return JSONResponse(content={"entries": []})
    ns_tuple = tuple(namespace.replace("/", ".").split("."))
    items = await store.asearch(ns_tuple, limit=100)
    return JSONResponse(
        content={
            "namespace": list(ns_tuple),
            "entries": [
                {
                    "key": item.key,
                    "value": item.value,
                    "updated_at": item.updated_at,
                }
                for item in items
            ],
        }
    )


@gateway_router.delete("/memory/{namespace:path}/{key}")
async def memory_delete_entry(namespace: str, key: str, request: Request):
    """Delete a specific memory entry by namespace and key."""
    store = request.app.state.store_holder[0]
    if store is None:
        return JSONResponse(content={"success": False, "error": "Store not initialised"})
    ns_tuple = tuple(namespace.replace("/", ".").split("."))
    await store.adelete(ns_tuple, key)
    return JSONResponse(content={"success": True, "namespace": list(ns_tuple), "key": key})


# ---------------------------------------------------------------------------
# Sandbox management
# ---------------------------------------------------------------------------


@gateway_router.get("/sandbox/status")
async def sandbox_status(request: Request):
    """Return sandbox system status and active session count."""
    sandbox_manager = request.app.state.sandbox_manager
    if sandbox_manager is None:
        return JSONResponse(
            content={
                "enabled": False,
                "active_sessions": 0,
                "session_ids": [],
            }
        )
    return JSONResponse(
        content={
            "enabled": True,
            "active_sessions": sandbox_manager.active_count,
            "max_concurrent": sandbox_manager.max_concurrent,
            "session_ids": sandbox_manager.session_ids,
            "allocated_ports": {str(k): v for k, v in sandbox_manager.allocated_ports.items()},
        }
    )


@gateway_router.get("/sandbox/{session_id}/info")
async def sandbox_info(session_id: str, request: Request):
    """Return detailed info for a session's sandbox (ports, status, uptime)."""
    sandbox_manager = request.app.state.sandbox_manager
    if sandbox_manager is None:
        return JSONResponse(
            content={"error": "Sandbox system is disabled"},
            status_code=400,
        )
    info = await sandbox_manager.get_sandbox_info(session_id)
    if info is None:
        return JSONResponse(
            content={"error": f"No sandbox for session '{session_id}'"},
            status_code=404,
        )
    return JSONResponse(
        content={
            "session_id": session_id,
            "container_id": info.container_id,
            "status": info.status,
            "image": info.image,
            "mapped_ports": {str(k): v for k, v in info.mapped_ports.items()},
            "uptime_seconds": info.uptime_seconds,
            "memory_limit": info.memory_limit,
            "cpu_limit": info.cpu_limit,
        }
    )


@gateway_router.get("/sandbox/{session_id}/stats")
async def sandbox_stats(session_id: str, request: Request):
    """Return a live CPU/memory/network snapshot for a session's sandbox.

    Shape: ``{cpu_percent, memory_bytes, memory_limit_bytes,
    memory_percent, net_rx_bytes, net_tx_bytes}``. All zeros when the
    container is stopped or docker stats is unavailable.
    """
    sandbox_manager = request.app.state.sandbox_manager
    if sandbox_manager is None:
        return JSONResponse(
            content={"error": "Sandbox system is disabled"},
            status_code=400,
        )
    sandbox = sandbox_manager._sandboxes.get(session_id)
    if sandbox is None:
        return JSONResponse(
            content={"error": f"No sandbox for session '{session_id}'"},
            status_code=404,
        )
    stats = await sandbox.get_stats()
    return JSONResponse(content=stats)


@gateway_router.get("/sandbox/{session_id}/logs")
async def sandbox_logs(session_id: str, request: Request):
    """Stream container logs via Server-Sent Events.

    Query parameters:
    - ``tail`` (int, default 100): number of trailing lines.
    - ``follow`` (bool, default false): stream new log lines as they arrive.
    """
    from starlette.responses import StreamingResponse

    sandbox_manager = request.app.state.sandbox_manager
    if sandbox_manager is None:
        return JSONResponse(
            content={"error": "Sandbox system is disabled"},
            status_code=400,
        )

    sandbox = sandbox_manager._sandboxes.get(session_id)
    if sandbox is None or not sandbox.is_running:
        return JSONResponse(
            content={"error": f"No running sandbox for session '{session_id}'"},
            status_code=404,
        )

    container_id = sandbox.container_id
    if container_id is None:
        # LOCAL mode — no Docker logs available
        return JSONResponse(
            content={"error": "Logs only available for Docker sandboxes"},
            status_code=400,
        )

    tail = request.query_params.get("tail", "100")
    follow = request.query_params.get("follow", "false").lower() == "true"

    async def _stream_logs():
        import asyncio as _aio

        cmd = ["docker", "logs", f"--tail={tail}"]
        if follow:
            cmd.append("--follow")
        cmd.append(container_id)

        proc = await _aio.create_subprocess_exec(
            *cmd,
            stdout=_aio.subprocess.PIPE,
            stderr=_aio.subprocess.STDOUT,
        )
        try:
            while True:
                line = await proc.stdout.readline()  # type: ignore[union-attr]
                if not line:
                    break
                yield f"data: {line.decode(errors='replace').rstrip()}\n\n"
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.communicate()

    return StreamingResponse(
        _stream_logs(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@gateway_router.delete("/sandbox/{session_id}")
async def sandbox_cleanup(session_id: str, request: Request):
    """Force cleanup of a session's sandbox.

    Stops and removes the Docker container (or local process) for the
    given session. Safe to call even if the session has no sandbox.
    """
    sandbox_manager = request.app.state.sandbox_manager
    if sandbox_manager is None:
        return JSONResponse(
            content={"success": False, "error": "Sandbox system is disabled"},
            status_code=400,
        )
    await sandbox_manager.cleanup_session(session_id)
    return JSONResponse(content={"success": True, "session_id": session_id})


# ---------------------------------------------------------------------------
# External MCP client servers
# ---------------------------------------------------------------------------


@gateway_router.get("/mcp/servers")
async def mcp_list_servers(request: Request):
    """List all connected external MCP servers."""
    mcp_client_manager = request.app.state.mcp_client_manager
    servers = mcp_client_manager.list_servers()
    tools = mcp_client_manager.get_all_tools()
    by_server: dict[str, int] = {}
    for t in tools:
        by_server[t.server_name] = by_server.get(t.server_name, 0) + 1
    return JSONResponse(
        content={"servers": [{"name": s, "tool_count": by_server.get(s, 0)} for s in servers]}
    )


@gateway_router.post("/mcp/servers")
async def mcp_add_server(body: dict, request: Request):
    """Connect to an external MCP server.

    Body fields:
    - ``name`` (str, required) — unique identifier for this server
    - ``transport`` (str, required) — ``"stdio"`` or ``"sse"``
    - ``command`` (list[str], required for stdio)
    - ``url`` (str, required for sse)
    - ``env`` (dict, optional) — extra environment variables for stdio
    - ``headers`` (dict, optional) — HTTP headers for sse
    """
    from ..core.mcp_client import MCPServerConfig

    mcp_client_manager = request.app.state.mcp_client_manager
    name = body.get("name", "").strip()
    if not name:
        return JSONResponse(content={"error": "'name' is required"}, status_code=400)

    config = MCPServerConfig(
        transport=body.get("transport", ""),
        command=body.get("command"),
        url=body.get("url"),
        env=body.get("env"),
        headers=body.get("headers"),
    )
    try:
        config.validate()
    except ValueError:
        logger.warning("MCP config validation failed for %r", _sanitize_log(name), exc_info=True)
        return JSONResponse(content={"error": "Invalid MCP server configuration"}, status_code=400)

    try:
        await mcp_client_manager.add_server(name, config)
    except Exception:
        logger.warning("MCP add_server failed for %r", _sanitize_log(name), exc_info=True)
        return JSONResponse(content={"error": "Failed to connect to MCP server"}, status_code=502)

    tool_count = len([t for t in mcp_client_manager.get_all_tools() if t.server_name == name])
    return JSONResponse(
        content={"name": name, "connected": True, "tool_count": tool_count},
        status_code=201,
    )


@gateway_router.delete("/mcp/servers/{name}")
async def mcp_remove_server(name: str, request: Request):
    """Disconnect an external MCP server by name."""
    mcp_client_manager = request.app.state.mcp_client_manager
    if name not in mcp_client_manager.list_servers():
        return JSONResponse(content={"error": f"Server '{name}' not found"}, status_code=404)
    await mcp_client_manager.remove_server(name)
    return JSONResponse(content={"name": name, "disconnected": True})


@gateway_router.get("/mcp/resources/{uri:path}")
async def mcp_read_resource(uri: str, request: Request):
    """Fetch resource content from an external MCP server.

    The URI format is ``{server_name}/{resource_uri}``.
    """
    mcp_client_manager = request.app.state.mcp_client_manager
    parts = uri.split("/", 1)
    if len(parts) < 2:
        return JSONResponse(
            content={"error": "URI must be in format '{server_name}/{resource_uri}'"},
            status_code=400,
        )
    server_name, resource_uri = parts[0], parts[1]
    client = mcp_client_manager._clients.get(server_name)
    if client is None:
        return JSONResponse(
            content={"error": f"Server '{server_name}' not connected"}, status_code=404
        )
    try:
        content = await client.read_resource(resource_uri)
    except Exception:
        logger.warning(
            "MCP read_resource failed: server=%r uri=%r",
            _sanitize_log(server_name),
            _sanitize_log(resource_uri),
            exc_info=True,
        )
        return JSONResponse(content={"error": "Failed to read resource"}, status_code=502)
    return JSONResponse(content={"uri": resource_uri, "server": server_name, "content": content})


# ---------------------------------------------------------------------------
# Skill invoke
# ---------------------------------------------------------------------------


@gateway_router.post("/skill/invoke")
async def skill_invoke(body: dict, request: Request):
    """Invoke a skill directly (without an agent)."""
    bus = request.app.state.bus

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


# ---------------------------------------------------------------------------
# Prompt Registry (PR #56) — /api/prompts
# ---------------------------------------------------------------------------


def _prompt_registry_or_404(request: Request):
    reg = getattr(request.app.state, "prompt_registry", None)
    if reg is None:
        raise HTTPException(
            status_code=503,
            detail="prompt_registry not initialised (store unavailable)",
        )
    return reg


@gateway_router.get("/prompts")
async def list_prompts(request: Request, limit: int = 100, offset: int = 0):
    """Return every registered prompt template (newest first)."""
    reg = _prompt_registry_or_404(request)
    templates = await reg.list_all(limit=limit, offset=offset)
    templates.sort(key=lambda t: t.updated_at, reverse=True)
    return JSONResponse(content={"templates": [t.to_dict() for t in templates]})


@gateway_router.get("/prompts/search")
async def search_prompts(
    request: Request,
    tags: str | None = None,
    category: str | None = None,
    limit: int = 10,
):
    """Search prompts by AND-intersection of tags and optional category.

    ``tags`` is a comma-separated list (``?tags=python,testing``).
    """
    reg = _prompt_registry_or_404(request)
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    results = await reg.search(tags=tag_list, category=category, limit=limit)
    return JSONResponse(content={"templates": [t.to_dict() for t in results]})


@gateway_router.get("/prompts/{name}")
async def get_prompt(name: str, request: Request):
    """Return one prompt template by name, or 404 if unknown."""
    reg = _prompt_registry_or_404(request)
    tpl = await reg.get(name)
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"prompt '{name}' not found")
    return JSONResponse(content=tpl.to_dict())


@gateway_router.post("/prompts")
async def create_or_update_prompt(body: dict, request: Request):
    """Register a new prompt or update an existing one.

    Body schema: ``{name, content, tags?, category?, version?, description?, metadata?}``.
    """
    from ..core.prompt_registry import PromptTemplate

    name = body.get("name")
    content = body.get("content")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail="'name' is required")
    if not isinstance(content, str) or not content:
        raise HTTPException(status_code=400, detail="'content' is required")

    reg = _prompt_registry_or_404(request)
    template = PromptTemplate(
        name=name.strip(),
        content=content,
        tags=list(body.get("tags") or []),
        category=body.get("category"),
        version=str(body.get("version") or "1"),
        description=body.get("description"),
        metadata=dict(body.get("metadata") or {}),
    )
    await reg.register(template)
    return JSONResponse(content=template.to_dict(), status_code=201)


@gateway_router.delete("/prompts/{name}")
async def delete_prompt(name: str, request: Request):
    """Remove a prompt template. Idempotent."""
    reg = _prompt_registry_or_404(request)
    await reg.delete(name)
    return JSONResponse(content={"deleted": name})


# ---------------------------------------------------------------------------
# Compaction metrics (PR #60) — /api/compaction/stats
# ---------------------------------------------------------------------------


@gateway_router.get("/compaction/stats")
async def compaction_stats(request: Request):
    """Return live conversation-compaction statistics for the dashboard.

    The values surface directly from the running ``ConversationManager`` so
    the frontend can show a real "tokens saved" counter rather than a
    placeholder. When no summarization has fired yet all values are zero.
    """
    conv_manager = request.app.state.conv_manager
    return JSONResponse(
        content={
            "summarization_count": int(getattr(conv_manager, "summarization_count", 0)),
            "tokens_saved": int(getattr(conv_manager, "tokens_saved", 0)),
            "messages_compacted": int(getattr(conv_manager, "messages_compacted", 0)),
            "last_compaction_ratio": float(getattr(conv_manager, "last_compaction_ratio", 0.0)),
        }
    )
