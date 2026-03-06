# LangGraph — CLI

## Available Commands

Built with `click`. Five commands:

| Command | Description |
|---------|-------------|
| `langgraph up` | Launch API server via Docker Compose |
| `langgraph build` | Build Docker image for deployment |
| `langgraph dockerfile` | Generate Dockerfile (+ optional docker-compose.yml) |
| `langgraph dev` | Run dev server in-process with hot reloading |
| `langgraph new` | Scaffold project from template |

## `langgraph up` — Server Launch

```bash
langgraph up --config langgraph.json --port 8123 --watch
```

**Flow:**
1. Validate config file
2. Check Docker capabilities (version, compose plugin vs standalone)
3. Pull base image
4. Generate docker-compose YAML to stdin
5. `docker compose -f - up --remove-orphans`
6. Monitor stdout for "Application startup complete"

**Docker Compose stack generated:**
- `langgraph-redis` (Redis 6)
- `langgraph-postgres` (pgvector/pg16 with vector extension)
- `langgraph-api` (the app)
- Optional `langgraph-debugger`

## `langgraph build` — Docker Image

```bash
langgraph build --config langgraph.json --tag my-graph:latest
```

Generates Dockerfile and pipes to `docker build -f - -t <tag>`. Supports Python and JS/TS.

## `langgraph dev` — In-Process Dev Server

```bash
langgraph dev --port 2024 --config langgraph.json
```

Requires `langgraph-cli[inmem]`. No Docker. Calls `langgraph_api.cli.run_server()` directly.

Options: `--host`, `--port`, `--no-reload`, `--tunnel` (Cloudflare), `--debug-port`, `--allow-blocking`.

## `langgraph new` — Project Scaffolding

| Template | Languages |
|----------|-----------|
| New LangGraph Project | Python, JS |
| ReAct Agent | Python, JS |
| Memory Agent | Python, JS |
| Retrieval Agent | Python, JS |
| Data-enrichment Agent | Python, JS |

Downloads from GitHub archive URLs.

## Docker Integration

`DockerCapabilities` detects:
- Docker version (v25+ for `healthcheck.start_interval`)
- Compose type: `"plugin"` (docker compose) vs `"standalone"` (docker-compose)

Custom minimal YAML serializer (no PyYAML dependency).

## Dockerfile Generation

`config_to_docker()` generates multi-stage Dockerfiles supporting:
- Python 3.11-3.13, Node.js 20+
- `uv` or `pip` as package installer
- `debian`, `wolfi`, `bookworm` base distros
- Custom `dockerfile_lines` injection
- Build context for local dependencies
- Cleanup of build tools from final image
