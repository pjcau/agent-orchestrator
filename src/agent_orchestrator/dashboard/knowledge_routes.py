"""HTTP API for the knowledge / RAG subsystem (P1).

Endpoints:
- ``POST /api/knowledge/ingest``  — add text/markdown to a namespace
- ``POST /api/knowledge/search``  — query a namespace
- ``GET  /api/knowledge/namespaces`` — list namespaces with chunk counts
- ``DELETE /api/knowledge/namespaces/{namespace}`` — drop a namespace
- ``GET  /api/knowledge/health``  — quick check whether RAG is wired

Wires the ``Ingester`` / ``Retriever`` from ``app.state`` (assembled in
``dashboard/app.py`` startup). Emits ``KNOWLEDGE_INGESTED`` and
``KNOWLEDGE_RETRIEVED`` events so the dashboard can highlight RAG
activity in the event log.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..core.knowledge import IngestRequest
from ..skills.retrieval_skill import parse_namespace, render_namespace
from .events import Event, EventBus, EventType

logger = logging.getLogger(__name__)

knowledge_router = APIRouter(prefix="/api/knowledge")


@knowledge_router.get("/health")
async def knowledge_health(request: Request):
    """Tell the UI whether RAG is wired and what model is in use."""
    ingester = getattr(request.app.state, "knowledge_ingester", None)
    retriever = getattr(request.app.state, "knowledge_retriever", None)
    if ingester is None or retriever is None:
        return JSONResponse(content={"enabled": False})
    # Both share the same embedder; reading it from the retriever is fine.
    embedder = retriever._embedder  # noqa: SLF001 — internal access by design
    return JSONResponse(
        content={
            "enabled": True,
            "embedding_model": embedder.info.name,
            "embedding_dim": embedder.dim,
            "embedding_provider": embedder.info.provider,
        }
    )


@knowledge_router.post("/ingest")
async def ingest(body: dict, request: Request):
    ingester = getattr(request.app.state, "knowledge_ingester", None)
    if ingester is None:
        return JSONResponse(
            content={"error": "Knowledge subsystem not configured"}, status_code=503
        )

    text = str(body.get("text", "")).strip()
    if not text:
        return JSONResponse(content={"error": "text is required"}, status_code=400)
    source_id = str(body.get("source_id", "")).strip()
    if not source_id:
        return JSONResponse(content={"error": "source_id is required"}, status_code=400)
    ns_str = str(body.get("namespace", "shared")).strip() or "shared"
    try:
        namespace = parse_namespace(ns_str)
    except ValueError as exc:
        return JSONResponse(content={"error": str(exc)}, status_code=400)

    metadata = body.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}

    result = await ingester.ingest(
        IngestRequest(
            text=text, namespace=namespace, source_id=source_id, metadata=metadata
        )
    )

    bus: EventBus | None = getattr(request.app.state, "bus", None)
    if bus is not None:
        await bus.emit(
            Event(
                event_type=EventType.KNOWLEDGE_INGESTED,
                data={
                    "namespace": list(namespace),
                    "namespace_label": render_namespace(namespace),
                    "source_id": source_id,
                    "chunks_added": result.chunks_added,
                    "embedding_model": result.embedding_model,
                },
            )
        )

    return JSONResponse(
        content={
            "success": True,
            "namespace": render_namespace(namespace),
            "source_id": source_id,
            "chunks_added": result.chunks_added,
            "embedding_model": result.embedding_model,
        }
    )


@knowledge_router.post("/search")
async def search(body: dict, request: Request):
    retriever = getattr(request.app.state, "knowledge_retriever", None)
    if retriever is None:
        return JSONResponse(
            content={"error": "Knowledge subsystem not configured"}, status_code=503
        )

    query = str(body.get("query", "")).strip()
    if not query:
        return JSONResponse(content={"error": "query is required"}, status_code=400)
    ns_str = str(body.get("namespace", "shared")).strip() or "shared"
    try:
        namespace = parse_namespace(ns_str)
    except ValueError as exc:
        return JSONResponse(content={"error": str(exc)}, status_code=400)
    try:
        k = max(1, min(20, int(body.get("k", 5))))
    except (TypeError, ValueError):
        k = 5

    result = await retriever.retrieve(query, namespace, k=k)

    bus: EventBus | None = getattr(request.app.state, "bus", None)
    if bus is not None:
        await bus.emit(
            Event(
                event_type=EventType.KNOWLEDGE_RETRIEVED,
                data={
                    "namespace": list(namespace),
                    "namespace_label": render_namespace(namespace),
                    "query": query,
                    "k": k,
                    "hits": len(result.hits),
                    "embedding_model": result.embedding_model,
                    "scores": [h.score for h in result.hits],
                },
            )
        )

    return JSONResponse(
        content={
            "namespace": render_namespace(namespace),
            "query": query,
            "k": k,
            "embedding_model": result.embedding_model,
            "hits": [
                {
                    "chunk_id": h.chunk.chunk_id,
                    "score": h.score,
                    "text": h.chunk.text,
                    "location": h.chunk.metadata.get("location", ""),
                    "source_id": h.chunk.metadata.get("source_id", ""),
                }
                for h in result.hits
            ],
            "context_block": result.as_context_block(),
        }
    )


@knowledge_router.get("/namespaces")
async def list_namespaces(request: Request):
    retriever = getattr(request.app.state, "knowledge_retriever", None)
    if retriever is None:
        return JSONResponse(
            content={"error": "Knowledge subsystem not configured"}, status_code=503
        )
    store = retriever._store  # noqa: SLF001 — composition is intentional
    names = await store.list_namespaces()
    out = []
    for ns in names:
        count = await store.count(ns)
        out.append({"namespace": render_namespace(ns), "tuple": list(ns), "chunks": count})
    return JSONResponse(content={"namespaces": out})


@knowledge_router.delete("/namespaces/{namespace}")
async def delete_namespace(namespace: str, request: Request):
    ingester = getattr(request.app.state, "knowledge_ingester", None)
    if ingester is None:
        return JSONResponse(
            content={"error": "Knowledge subsystem not configured"}, status_code=503
        )
    try:
        ns = parse_namespace(namespace)
    except ValueError as exc:
        return JSONResponse(content={"error": str(exc)}, status_code=400)

    removed = await ingester._store.delete_namespace(ns)  # noqa: SLF001
    return JSONResponse(
        content={"success": True, "namespace": render_namespace(ns), "removed": removed}
    )
