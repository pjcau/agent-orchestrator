# 16 - API Layer

## Two API Servers

### LangGraph Server (port 2024)
- Agent runtime and workflow execution
- Thread management
- SSE streaming responses
- Checkpointing
- Runs via `langgraph dev` CLI

### Gateway API (port 8001)
- FastAPI application
- Non-agent REST operations
- Health check: `GET /health`

## Gateway Routers

| Router | Endpoints |
|--------|-----------|
| **Models** `/api/models` | `GET /` list, `GET /{name}` details |
| **MCP** `/api/mcp` | `GET /config`, `PUT /config` |
| **Skills** `/api/skills` | `GET /`, `GET /{name}`, `PUT /{name}`, `POST /install` |
| **Memory** `/api/memory` | `GET /`, `POST /reload`, `GET /config`, `GET /status` |
| **Uploads** `/api/threads/{id}/uploads` | `POST /`, `GET /list`, `DELETE /{filename}` |
| **Artifacts** `/api/threads/{id}/artifacts` | `GET /{path}`, `?download=true` |
| **Suggestions** `/api/threads/{id}/suggestions` | `POST /` generate follow-ups |
| **Agents** `/api/agents` | Custom agent CRUD |
| **Channels** `/api/channels` | IM channel management |

## Nginx Routing

```nginx
/api/langgraph/*  → LangGraph Server (2024)
/api/*            → Gateway API (8001)
/*                → Frontend (3000)
```

## LangGraph API

Standard LangGraph endpoints:
- `POST /threads` — create thread
- `POST /threads/{id}/runs` — start run
- `POST /threads/{id}/runs/stream` — streaming run
- `GET /threads/{id}/state` — get state
- `POST /threads/{id}/runs/wait` — synchronous run

## File Upload Flow

```
1. Client → POST /api/threads/{id}/uploads (multipart)
2. Gateway stores in .deer-flow/threads/{id}/user-data/uploads/
3. PDF/PPT/Excel/Word auto-converted to Markdown (markitdown)
4. Returns: {files: [{filename, path, virtual_path, artifact_url}]}
5. Next agent run: UploadsMiddleware injects file list
```

## Suggestions API

`POST /api/threads/{id}/suggestions`:
- Generates follow-up questions based on conversation
- Normalizes both plain-string and rich block content from models
- Returns JSON array of suggestion strings

## Key Design: Process Separation

Gateway API and LangGraph Server run in **separate processes**:
- Config changes via Gateway (write to disk)
- LangGraph detects changes via file mtime
- No shared memory between processes
- Clean process isolation but requires file-based coordination
