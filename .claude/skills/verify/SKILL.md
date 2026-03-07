---
name: verify
description: Run comprehensive pre-PR verification (tests, lint, format, docs sync, security scan, diff review). Use before creating PRs or after significant changes.
disable-model-invocation: true
allowed-tools: Bash, Read, Grep, Glob
user-invocable: true
---

# Verify — Pre-PR Quality Gate

Comprehensive verification loop adapted from flatrick/everything-claude-code verification-loop pattern.

## Phase 1: Build / Import Check
```bash
docker compose run --rm app python -c "import agent_orchestrator; print('OK')"
```
If this fails, STOP and fix before continuing.

## Phase 2: Lint
```bash
docker compose run --rm lint
```
Report all lint errors. Fix before continuing.

## Phase 3: Format Check
```bash
docker compose run --rm format
```

## Phase 4: Test Suite
```bash
docker compose run --rm test
```
Report:
- Total tests
- Passed / Failed
- Any warnings

## Phase 5: Security Scan
```bash
# Check for secrets in tracked files
grep -rn "sk-\|api_key\s*=\s*['\"]" --include="*.py" src/ tests/ 2>/dev/null | head -10
# Check for hardcoded passwords
grep -rn "password\s*=\s*['\"]" --include="*.py" src/ 2>/dev/null | grep -v "test\|example\|orchestrator" | head -10
```

## Phase 6: Diff Review
```bash
git diff --stat
git diff HEAD --name-only
```
Review each changed file for:
- Unintended changes
- Missing error handling
- Potential security issues

## Output Format

After running all phases, produce a verification report:

```
VERIFICATION REPORT
==================
Import:    [PASS/FAIL]
Lint:      [PASS/FAIL] (details)
Format:    [PASS/FAIL]
Tests:     [PASS/FAIL] (X passed, Y warnings)
Security:  [PASS/FAIL] (X issues)
Diff:      [X files changed]

Overall:   [READY/NOT READY] for PR

Issues to Fix:
1. ...
```

## When to Run

- Before every PR
- After refactoring
- After adding new dependencies
- After significant code changes
