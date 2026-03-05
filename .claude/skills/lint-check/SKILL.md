---
name: lint-check
description: Run linting and formatting checks via Docker/OrbStack. Use this before commits to ensure code quality, fix style issues, and enforce project standards.
disable-model-invocation: true
allowed-tools: Bash, Read, Edit
---

# Lint Check — Code Quality

Run ruff linting and formatting checks inside Docker via OrbStack.

## Check lint errors
```bash
docker compose run --rm lint
```

## Check formatting
```bash
docker compose run --rm format
```

## Auto-fix lint errors
```bash
docker compose run --rm app ruff check --fix src/ tests/
```

## Auto-format code
```bash
docker compose run --rm app ruff format src/ tests/
```

## Quick local run
```bash
cd /Users/pierrejonnycau/Documents/WORKS/agent-orchestrator
ruff check src/ tests/
ruff format --check src/ tests/
```

## Rules

- Line length: 100 chars
- Target: Python 3.11
- Config: `pyproject.toml` [tool.ruff] section
