---
name: test-runner
description: Run the project test suite via Docker/OrbStack. Use this after code changes to verify correctness, run specific test files, or check coverage.
disable-model-invocation: true
allowed-tools: Bash, Read, Grep
---

# Test Runner — Containerized Testing

Run pytest inside Docker via OrbStack for consistent, isolated test execution.

## Run all tests
```bash
docker compose run --rm test
```

## Run specific test file
```bash
docker compose run --rm app pytest tests/test_specific.py -v
```

## Run with coverage
```bash
docker compose run --rm app pytest --cov=agent_orchestrator --cov-report=term-missing
```

## Run matching tests
```bash
docker compose run --rm app pytest -k "test_pattern" -v
```

## Quick local run (if deps installed locally)
```bash
cd /Users/pierrejonnycau/Documents/WORKS/agent-orchestrator
pytest --tb=short
```

## After Failures

1. Read the failing test and the source code it tests
2. Fix the issue
3. Re-run only the failing test to confirm the fix
4. Run full suite to check for regressions
