---
name: deploy
description: Deploy services using Docker/OrbStack. Use this to build and deploy containers, run health checks, and manage deployments.
disable-model-invocation: true
allowed-tools: Bash, Read, Grep
---

# Deploy — Container Deployment

Build and deploy services via docker-compose on OrbStack.

## Development deploy
```bash
docker compose up -d
docker compose ps
```

## Rebuild and deploy
```bash
docker compose build && docker compose up -d
```

## Health check
```bash
docker compose ps
docker compose logs --tail=20
```

## Rollback
```bash
docker compose down
git stash  # if needed
docker compose up -d
```

## Pre-deploy checklist

1. All tests pass (`/test-runner`)
2. Lint clean (`/lint-check`)
3. Code reviewed (`/code-review`)
4. No uncommitted changes in tracked files
