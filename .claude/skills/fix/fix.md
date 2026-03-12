# Fix — Bug Fix with Mandatory Regression Tests

One command to fix a bug properly: diagnose, fix, test, lint, verify.

**STOP on any failure** — do not continue to the next phase if the current one fails.

## Phase 1: Diagnose

1. Read the relevant source files to understand the bug
2. If logs/errors are provided, trace the root cause
3. If on EC2/production, check container logs: `ssh -i ~/.ssh/agent-orchestrator ec2-user@agents-orchestrator.com "cd /opt/agent-orchestrator && docker compose -f docker-compose.prod.yml --env-file .env.prod logs --tail=100 <service>"`

## Phase 2: Fix

1. Apply the minimal fix to resolve the issue
2. Do NOT over-engineer — fix the bug, nothing more

## Phase 3: Write Regression Tests (MANDATORY)

**This phase is NON-NEGOTIABLE.** Every fix MUST have tests.

1. Check if tests exist for the affected module in `tests/`
2. Write regression tests that:
   - Reproduce the exact failure condition
   - Verify the fix works
   - Prevent the bug from recurring
3. Test naming: `test_<module>_<bug_description>`

```bash
source .venv/bin/activate && pytest tests/test_<module>.py -v --tb=short
```

If tests fail, fix them. Do NOT skip this phase.

## Phase 4: Lint & Format

```bash
source .venv/bin/activate && ruff check src/ tests/
source .venv/bin/activate && ruff format --check src/ tests/
```

If lint/format fails, fix automatically and re-run.

## Phase 5: Full Test Suite

```bash
source .venv/bin/activate && pytest --tb=short -q
```

If any tests fail, STOP and fix.

## Phase 6: Deploy (if production fix)

If the fix is for a production issue:

1. Rsync changed files to EC2
2. Rebuild and restart affected container
3. Verify with health check

## Output Format

After all phases, produce a fix report:

```
FIX REPORT
==========
Bug:     <one-line description>
Root:    <root cause>
Fix:     <what was changed>
Tests:   [PASS] (X new tests, Y total passed)
Lint:    [PASS]
Deploy:  [DEPLOYED/LOCAL-ONLY]
```
