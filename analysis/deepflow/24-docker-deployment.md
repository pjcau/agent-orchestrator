# 24 - Docker Deployment

## Docker Compose Architecture

### Development (`docker-compose-dev.yaml`)
- Hot-reload enabled
- Source mounts
- `langgraph dev` with `--no-browser`

### Production (`docker-compose.yaml`)
```yaml
services:
  nginx:        # Reverse proxy (port 2026)
  frontend:     # Next.js production build
  gateway:      # FastAPI Gateway API
  langgraph:    # LangGraph server
  provisioner:  # Optional K8s sandbox manager
```

## Service Details

### nginx
- `nginx:alpine` image
- Port: `${PORT:-2026}`
- Routes: `/api/langgraph/*` → langgraph, `/api/*` → gateway, `/*` → frontend

### frontend
- Multi-stage build (prod target)
- Environment: `BETTER_AUTH_SECRET`
- No exposed ports (nginx proxies)

### gateway
- Custom Dockerfile
- `uvicorn app.gateway.app:app --workers 2`
- Volumes: config.yaml, extensions_config.json, skills, .deer-flow data, Docker socket
- DooD (Docker-out-of-Docker) for sandbox containers

### langgraph
- Same Dockerfile as gateway
- `uv run langgraph dev --no-browser --allow-blocking --no-reload`
- Same volume mounts
- LangSmith tracing disabled by default

### provisioner (optional)
- Separate Dockerfile
- K8s sandbox management
- kubeconfig mount
- Profile-gated (only started for K8s mode)

## Docker-out-of-Docker (DooD)

For sandbox containers, the gateway mounts the host Docker socket:
```yaml
volumes:
  - ${DEER_FLOW_DOCKER_SOCKET}:/var/run/docker.sock
```

Environment variables for path translation:
```yaml
- DEER_FLOW_HOST_BASE_DIR=${DEER_FLOW_HOME}
- DEER_FLOW_HOST_SKILLS_PATH=${DEER_FLOW_REPO_ROOT}/skills
- DEER_FLOW_SANDBOX_HOST=host.docker.internal
```

## Makefile Commands

```bash
make docker-init    # Pull sandbox image
make docker-start   # Start dev services (mode-aware)
make docker-stop    # Stop services
make up             # Build + start production
make down           # Stop + remove production
```

## Key Differences from Our Setup

| Aspect | DeerFlow | Our Orchestrator |
|--------|----------|-----------------|
| Services | 4-5 containers | 8+ containers |
| Database | None (file-based) | PostgreSQL |
| Cache | None | Redis |
| Monitoring | None built-in | Prometheus + Grafana |
| Tracing | LangSmith (optional) | OpenTelemetry + Tempo |
| Reverse Proxy | nginx | nginx |
| Runtime | Docker/K8s | OrbStack |

DeerFlow is simpler — no database, no monitoring stack. Our setup is more production-ready but heavier.
