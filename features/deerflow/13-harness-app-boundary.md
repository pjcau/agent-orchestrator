# Feature: Harness/App Boundary (Library Distribution)

## Context

From DeerFlow analysis (analysis/deepflow/29-learnings.md L3, 25-embedded-client.md).
By enforcing a clear import direction (core → app, never app → core), the core abstractions can be published as a standalone pip package. This enables library distribution and clean architecture.

## What to Build

### 1. Import Boundary Enforcement

Define clear layers:

```
agent_orchestrator/
├── core/           ← HARNESS layer (publishable library)
│   ├── agent.py
│   ├── skill.py
│   ├── provider.py
│   ├── orchestrator.py
│   ├── graph.py
│   ├── ...
├── providers/      ← HARNESS layer (provider implementations)
│   ├── anthropic.py
│   ├── openai.py
│   ├── ...
├── skills/         ← HARNESS layer (built-in skills)
│   ├── filesystem.py
│   ├── shell.py
│   ├── ...
├── dashboard/      ← APP layer (NOT part of library)
│   ├── app.py
│   ├── agent_runner.py
│   ├── ...
├── integrations/   ← APP layer (NOT part of library)
│   ├── slack_bot.py
│   ├── telegram_bot.py
│   └── ...
└── client.py       ← HARNESS layer (embedded client)
```

**Rule**: Files in `core/`, `providers/`, `skills/`, `client.py` MUST NEVER import from `dashboard/` or `integrations/`.

### 2. CI Import Check

```python
# tests/test_import_boundary.py

import ast
import os

HARNESS_DIRS = ["core", "providers", "skills"]
HARNESS_FILES = ["client.py"]
APP_MODULES = ["dashboard", "integrations"]

def test_harness_does_not_import_app():
    """Ensure harness layer never imports from app layer."""
    violations = []
    base = "src/agent_orchestrator"

    for harness_dir in HARNESS_DIRS:
        for root, _, files in os.walk(os.path.join(base, harness_dir)):
            for f in files:
                if not f.endswith(".py"):
                    continue
                filepath = os.path.join(root, f)
                tree = ast.parse(open(filepath).read())
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        module = getattr(node, "module", "") or ""
                        for app_mod in APP_MODULES:
                            if f"agent_orchestrator.{app_mod}" in module:
                                violations.append(f"{filepath} imports {module}")

    assert violations == [], f"Import boundary violations:\n" + "\n".join(violations)
```

### 3. Dual Package Configuration

Update `pyproject.toml` to support publishing just the harness:

```toml
[project]
name = "agent-orchestrator"
# ... existing config

[project.optional-dependencies]
# Core library (harness only, no dashboard)
core = []  # base install = harness

# Full application
dashboard = ["fastapi>=0.115", "uvicorn>=0.34", "websockets>=14", ...]
integrations = ["slack-bolt>=1.20", "python-telegram-bot>=21.0"]
all = ["agent-orchestrator[dashboard,integrations,docs,otel]"]
```

### 4. Fix Existing Boundary Violations

Audit current code for violations and fix them:

- Move any dashboard-specific logic out of `core/`
- Replace direct imports with dependency injection or callbacks
- Use event-based communication (EventBus) for core → dashboard data flow

## Files to Modify

- **Create**: `tests/test_import_boundary.py`
- **Modify**: `pyproject.toml` (reorganize optional dependencies)
- **Modify**: Any `core/` files that import from `dashboard/` (fix violations)

## Tests

- Test import boundary check passes for all harness files
- Test harness can be imported without dashboard dependencies
- Test `from agent_orchestrator.core import *` works standalone
- Test `from agent_orchestrator.client import OrchestratorClient` works standalone
- Test violation detection catches deliberate bad import

## Acceptance Criteria

- [ ] Clear harness/app layer definition
- [ ] CI test enforcing import boundary
- [ ] Zero boundary violations in current code
- [ ] Harness importable without dashboard dependencies
- [ ] pyproject.toml reorganized for dual distribution
- [ ] All tests pass
- [ ] Existing tests still pass
