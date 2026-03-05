---
name: docker-build
description: Build and manage Docker containers via OrbStack. Use this to build images, run containers, check service health, and manage docker-compose services.
disable-model-invocation: true
allowed-tools: Bash, Read, Grep
---

# Docker Build — OrbStack Container Management

Build, run, and manage project containers using OrbStack (docker/docker-compose).

## Prerequisites

- OrbStack must be running (`orb status` or check menu bar)
- docker-compose.yml must exist in project root

## Commands

### Build all services
```bash
docker compose build
```

### Build specific service
```bash
docker compose build <service-name>
```

### Run a one-off command in a service
```bash
docker compose run --rm <service-name> <command>
```

### Check running services
```bash
docker compose ps
```

### View logs
```bash
docker compose logs <service-name>
```

### Tear down
```bash
docker compose down -v
```

## Error Handling

1. If build fails, check Dockerfile syntax and base image availability
2. If OrbStack is not running: `open -a OrbStack` and wait 5 seconds
3. If port conflicts: `docker compose down` first, then rebuild
