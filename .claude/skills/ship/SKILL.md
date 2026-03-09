---
name: ship
description: Full ship pipeline — run tests, update docs, commit, and push in one go.
disable-model-invocation: true
allowed-tools: Bash, Read, Edit, Grep, Glob
user-invocable: true
---

# Ship — Test, Doc, Commit & Push

One command to ship changes: test, lint, docs check, commit, push.

**STOP on any failure** — do not continue to the next phase if the current one fails.

## Phase 1: Write/Update Tests

Before running the test suite, check that tests cover the current changes:

1. Run `git diff --name-only` to see changed source files
2. For each changed file in `src/`, check if corresponding tests exist in `tests/`
3. If tests are missing or outdated for the changes, **write or update them first**
4. Every new feature, bug fix, or refactor MUST have test coverage

## Phase 2: Run Test Suite

```bash
source .venv/bin/activate && pytest --tb=short
```

If tests fail, STOP and report which tests failed. Do NOT continue.

## Phase 3: Lint & Format

```bash
source .venv/bin/activate && ruff check src/ tests/
source .venv/bin/activate && ruff format --check src/ tests/
```

If lint or format fails, fix automatically (`ruff check --fix`, `ruff format`) and re-run. If unfixable, STOP.

## Phase 4: Documentation Sync

Check that docs are in sync with code changes:

1. Read `git diff --name-only` to see changed files
2. If any source files in `src/` changed, check that relevant docs (CLAUDE.md, README.md, docs/) reflect the changes
3. If docs need updating, update them before committing

## Phase 5: Commit

1. Run `git status` and `git diff --stat` to review changes
2. Stage all relevant files (NOT .env or credentials)
3. Write a concise commit message summarizing the changes
4. Commit with `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>`

## Phase 6: Push

```bash
git push
```

## Output Format

After all phases, produce a ship report:

```
SHIP REPORT
===========
Tests:   [PASS/FAIL] (X passed, Y warnings)
Lint:    [PASS/FAIL]
Format:  [PASS/FAIL]
Docs:    [SYNCED/UPDATED] (list files if updated)
Commit:  [hash] message
Push:    [OK/FAIL]
```
