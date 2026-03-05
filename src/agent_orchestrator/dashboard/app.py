"""FastAPI dashboard application.

Serves:
- WebSocket at /ws for real-time events
- WebSocket at /ws/stream for streaming LLM responses
- REST APIs for models, agents, prompt, files, conversations, presets
- Static files for the dashboard UI
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .agents_registry import get_agent_registry
from .events import Event, EventBus, EventType
from .graphs import list_ollama_models, list_openrouter_models, run_graph

STATIC_DIR = Path(__file__).parent / "static"
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def create_dashboard_app(event_bus: EventBus | None = None) -> FastAPI:
    bus = event_bus or EventBus.get()

    app = FastAPI(title="Agent Orchestrator Dashboard", version="0.2.0")

    # In-memory conversation store (per session)
    conversations: dict[str, list[dict]] = {}

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
        return JSONResponse(content=get_agent_registry())

    # --- Models: Ollama + OpenRouter ---

    @app.get("/api/models")
    async def models():
        """List all available models (Ollama local + OpenRouter cloud)."""
        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
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

    # --- Ollama Model Management ---

    @app.post("/api/ollama/pull")
    async def ollama_pull(body: dict):
        """Pull a model from Ollama."""
        model_name = body.get("name", "").strip()
        if not model_name:
            return JSONResponse(content={"error": "No model name"}, status_code=400)

        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
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
        except Exception as e:
            return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)

    @app.delete("/api/ollama/model")
    async def ollama_delete(body: dict):
        """Delete a model from Ollama."""
        model_name = body.get("name", "").strip()
        if not model_name:
            return JSONResponse(content={"error": "No model name"}, status_code=400)

        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.delete(
                    f"{ollama_url}/api/delete",
                    json={"name": model_name},
                )
                resp.raise_for_status()
                return JSONResponse(content={"success": True})
        except Exception as e:
            return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)

    # --- File Context ---

    @app.get("/api/files")
    async def list_files(path: str = ""):
        """List files in the project directory."""
        base = PROJECT_ROOT
        target = (base / path).resolve()

        # Security: don't allow escaping project root
        if not str(target).startswith(str(base)):
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
        base = PROJECT_ROOT
        target = (base / path).resolve()

        if not str(target).startswith(str(base)):
            return JSONResponse(content={"error": "Path outside project"}, status_code=400)

        if not target.is_file():
            return JSONResponse(content={"error": "Not a file"}, status_code=404)

        # Limit file size to 100KB
        if target.stat().st_size > 100_000:
            return JSONResponse(content={"error": "File too large (>100KB)"}, status_code=400)

        try:
            content = target.read_text(errors="replace")
            return JSONResponse(content={"path": path, "content": content})
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)

    # --- Conversations (Multi-turn) ---

    @app.post("/api/conversation/new")
    async def new_conversation():
        conv_id = str(uuid.uuid4())[:8]
        conversations[conv_id] = []
        return JSONResponse(content={"conversation_id": conv_id})

    @app.get("/api/conversation/{conv_id}")
    async def get_conversation(conv_id: str):
        msgs = conversations.get(conv_id, [])
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
        if conv_id and conv_id in conversations:
            recent = conversations[conv_id][-6:]  # last 3 exchanges
            if recent:
                history_context = "\n".join(
                    f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:500]}"
                    for m in recent
                )

        if history_context:
            full_prompt = (
                f"Previous conversation:\n{history_context}\n\nCurrent request:\n{full_prompt}"
            )

        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")

        result = await run_graph(
            prompt=full_prompt,
            model=model,
            provider_type=provider_type,
            graph_type=graph_type,
            ollama_url=ollama_url,
            openrouter_key=openrouter_key,
            event_bus=bus,
        )

        # Save to conversation
        if conv_id:
            if conv_id not in conversations:
                conversations[conv_id] = []
            conversations[conv_id].append({"role": "user", "content": user_prompt})
            if result.get("success"):
                conversations[conv_id].append(
                    {"role": "assistant", "content": result.get("output", "")}
                )

        return JSONResponse(content=result)

    # --- Streaming via WebSocket ---

    @app.websocket("/ws/stream")
    async def stream_endpoint(ws: WebSocket):
        """Stream LLM responses token-by-token."""
        await ws.accept()
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

                # Build prompt with context
                full_prompt = prompt_text
                if file_context:
                    full_prompt = f"{prompt_text}\n\n```\n{file_context}\n```"

                # Add conversation history
                if conv_id and conv_id in conversations:
                    recent = conversations[conv_id][-6:]
                    if recent:
                        history = "\n".join(
                            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:500]}"
                            for m in recent
                        )
                        full_prompt = (
                            f"Previous conversation:\n{history}\n\nCurrent request:\n{full_prompt}"
                        )

                ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
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

                    # Save to conversation
                    if conv_id:
                        if conv_id not in conversations:
                            conversations[conv_id] = []
                        conversations[conv_id].append({"role": "user", "content": prompt_text})
                        conversations[conv_id].append(
                            {"role": "assistant", "content": full_response}
                        )

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

    # --- Events WebSocket ---

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
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

    return app
