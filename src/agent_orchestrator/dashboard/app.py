"""FastAPI dashboard application.

Serves:
- WebSocket at /ws for real-time events
- REST API at /api/snapshot for current state
- REST API at /api/events for event history
- REST API at /api/models for available Ollama models
- POST /api/prompt to trigger orchestrator graph execution
- Static files for the dashboard UI
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .events import EventBus
from .graphs import run_graph, list_ollama_models
from .agents_registry import get_agent_registry

STATIC_DIR = Path(__file__).parent / "static"


def create_dashboard_app(event_bus: EventBus | None = None) -> FastAPI:
    bus = event_bus or EventBus.get()

    app = FastAPI(title="Agent Orchestrator Dashboard", version="0.1.0")

    # Mount static files
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        index_file = STATIC_DIR / "index.html"
        return HTMLResponse(content=index_file.read_text())

    @app.get("/api/snapshot")
    async def snapshot():
        return JSONResponse(content=bus.get_snapshot())

    @app.get("/api/events")
    async def events(limit: int = 100):
        history = bus.get_history()
        return JSONResponse(content=[e.to_dict() for e in history[-limit:]])

    @app.get("/api/agents")
    async def agents():
        """Return the agent hierarchy and skills registry."""
        return JSONResponse(content=get_agent_registry())

    @app.get("/api/models")
    async def models():
        """List available Ollama models."""
        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        model_list = await list_ollama_models(ollama_url)
        return JSONResponse(content={"models": model_list})

    @app.post("/api/prompt")
    async def prompt(body: dict):
        """Execute a graph with the given prompt and model."""
        user_prompt = body.get("prompt", "").strip()
        model = body.get("model", "")
        graph_type = body.get("graph_type", "auto")

        if not user_prompt:
            return JSONResponse(
                content={"success": False, "error": "Empty prompt"}, status_code=400
            )
        if not model:
            return JSONResponse(
                content={"success": False, "error": "No model selected"}, status_code=400
            )

        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        result = await run_graph(
            prompt=user_prompt,
            model=model,
            graph_type=graph_type,
            ollama_url=ollama_url,
            event_bus=bus,
        )
        return JSONResponse(content=result)

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        queue = bus.subscribe()
        try:
            # Send snapshot on connect
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

    return app
