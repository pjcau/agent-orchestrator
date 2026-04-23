# Orchestrator Learning-Path Test — 2026-04-21 (task-tracker)

```
ORCHESTRATOR LEARNING-PATH TEST — REPORT
==========================================
Goal: Build a full-stack Task Tracker: FastAPI backend + SQLite + a seed script
      that inserts 50 sample tasks + a React (Vite + TypeScript) frontend
      that lists/creates/deletes tasks + pytest tests for the API +
      a docker-compose.yml that runs everything.
Session: 20260421_193549_f9f4e2
Iterations completed: 5/5
Duration: ~19:00    Cost: $0.277    Tokens: 4,018,201 (in + out)

Confidence: 79.01/100

Breakdown:
  Structure   8.18/10
  Syntax     15.00/15
  Build      20.00/20
  Runtime    10.00/20
  Functional 18.33/20
  LLM-judge   7.50/15
```

## Iteration requests

0. `Build a full-stack Task Tracker: FastAPI backend + SQLite + a seed script that inserts 50 sample tasks + a React (Vite + TypeScript) frontend that lists/creates/deletes tasks + pytest tests for the API + a docker-compose.yml that runs everything. All code in a single repo layout: backend/, frontend/, docker-compose.yml, README.md.`
1. `Add JWT authentication to the backend: /auth/signup and /auth/login endpoints that return a JWT, a User model with email+hashed_password, protect POST /tasks and DELETE /tasks/{id} with a Depends(get_current_user), and add pytest tests for signup/login happy path + rejecting unauthenticated POST. Update README with the new endpoints.`
2. `Enhance the React frontend: add a 'completed/pending/all' filter dropdown above the task list, a page-size selector (10/25/50), and Previous/Next pagination controls. Backend GET /tasks should accept ?status=&limit=&offset= query params (default limit=25). Wire the frontend to send these params. Add a 'No tasks' empty state. Update the Playwright-free tests in test_api.py to cover the new filters.`
3. `Migrate the backend database from SQLite to PostgreSQL: add a postgres service to docker-compose.yml, set DATABASE_URL, update requirements with psycopg/asyncpg, update seed.py to be idempotent against Postgres, update test fixture to use a dedicated DB or rollback transaction, update README.`
4. `Add a GitHub Actions CI workflow at .github/workflows/ci.yml with parallel backend + frontend jobs, postgres service for backend tests, docker compose build smoke check, and a status badge in README.`
5. `Bring test coverage to ≥80% on the backend: add pytest-cov, create backend/.coveragerc, configure pytest with --cov-fail-under=80, add missing tests for JWT edge cases and pagination edge cases, update CI to upload coverage.xml artifact, document the threshold in README.`

## Per-iteration timings & cost

| Iter | Status     | Cost    | Tokens    | Elapsed |
|------|------------|---------|-----------|---------|
| 0    | completed  | $0.047  | 670,098   | 136s    |
| 1    | completed  | $0.030  | 424,708   | 158s    |
| 2    | completed  | $0.033  | 439,242   | 187s    |
| 3    | completed  | $0.070  | 1,039,910 | 183s    |
| 4    | completed  | $0.014  | 173,408   | 93s     |
| 5    | completed  | $0.083  | 1,270,835 | 202s    |

All iterations reported `success: true`. The orchestrator framework exited
cleanly every time, well within the $2.00 budget cap.

## Failures / surprises

1. **Iterations 3, 4, 5 claimed success but wrote zero new files.**
   Concrete evidence:
   - `docker-compose.yml` still uses `DATABASE_URL=sqlite:///./tasks.db`
     and has no `postgres` service block (iter 3 was supposed to add it).
   - `backend/requirements.txt` has no `psycopg`, no `pytest-cov` — only
     the iter 0 + iter 1 deps (`fastapi`, `python-jose`, `passlib`, etc.).
   - No `.github/workflows/ci.yml` exists anywhere in the session artifact
     tree (iter 4 was supposed to create it).
   - No `backend/.coveragerc`, and `pytest.ini` still has the iter 0
     `addopts = -v --tb=short` — no `--cov-fail-under=80` (iter 5).
   - The team-lead's `output` narrative for iter 3/4/5 describes *plans*
     with file paths, but the sub-agents never invoked `file_write`.

2. **Iteration 2 landed on the backend but not the frontend.**
   `GET /tasks?status=pending&limit=5` returns HTTP 200 — so the query
   params are at least accepted — but the response still contains all
   51 rows. The filter is parsed and ignored. On the frontend side,
   `frontend/src/App.tsx` has no filter dropdown, no page-size selector,
   and no Previous/Next pagination controls. It remained the iter 0
   basic CRUD view.

3. **The generated `docker-compose.yml` healthcheck targets `/health` but
   `backend/main.py` never defines that endpoint.** Compose reports the
   container as unhealthy after 10s while the app is in fact serving.
   All other routes (including `GET /`) respond 200. The seed script
   (`docker compose exec backend python seed.py`) works and inserts the
   expected 50 tasks, but is not wired to startup — the DB is empty on
   first boot.

4. **Sub-agent budget variance is high.** Iter 5 spent $0.083 / 202s /
   1.27 M tokens and produced nothing, while iter 4 spent $0.014 / 93s
   and also produced nothing. Token spend does not correlate with file
   output.

## Improvement proposals (3, concrete + grounded)

### 1. Wire a post-iteration "file-delta" check into `run_team`

**Observation**: iters 3/4/5 all returned `success=true` with detailed
narratives while producing zero file mutations. The existing
`SmokeTester` node (`src/agent_orchestrator/core/smoke_tester.py`) runs
after agents finish but only validates syntax of already-existing files.

**Proposal**: add a `FileDeltaCheck` node that compares the session
directory before and after the sub-agent batch and, if the delta is
empty *while the task mentions file paths*, re-assigns with a
prescriptive prompt ("The previous pass did not write any files. You
MUST invoke `file_write` to create/modify: <paths from task>. Write the
files now; do not plan further."). Re-use the detection pattern from
`tests/test_phase2.py::TestAtomicTaskValidator` — extract file paths
via regex on the task text (backtick, slash, extension).

**File to touch**: `src/agent_orchestrator/dashboard/agent_runner.py`
around line 1210 (the existing `re_assignments` branch), or extract as
a middleware.

### 2. Make JWT auth the default session fixture for pytest

**Observation**: the sub-agents write correct JWT code (iter 1 passed
all five auth functional checks), but none of them thought to put a
`token` fixture in `conftest.py`. Iter 5 would have needed one to
actually raise coverage — instead the agent just padded the task list.

**Proposal**: when team-lead detects an auth-protected endpoint in the
existing codebase (regex for `Depends(` + `get_current_user`), inject
a hint in the sub-agent system prompt: "Tests that POST/DELETE on
protected routes must obtain a token via `/auth/login` (or the
`auth_token` fixture if present) and pass it as `Authorization: Bearer
<token>`. If the fixture is missing, create it in `conftest.py`."

**File to touch**: `src/agent_orchestrator/dashboard/agent_runner.py`
— extend the agent catalog prompt with a `detected_auth` flag built
from a quick regex pass over `backend/*.py`.

### 3. Surface "silent success" in the dashboard as a warning event

**Observation**: the UX tells the user `status=completed` without any
hint that three iterations wrote nothing. Users will only discover
this when they open the file explorer. Cumulative spend becomes
money-for-nothing.

**Proposal**: emit a `team.silent_success` event when `run_team`
finishes and the session file count is unchanged from the previous
iteration. Render it as an orange badge on the iteration card in the
history sidebar ("⚠ no files changed"). The counter
`team_silent_success_total` can be added to the existing metrics
registry alongside `tasks_rejected_too_complex_total` (phase 2).

**File to touch**: `src/agent_orchestrator/dashboard/agent_runner.py`
(emit event at end of `run_team`), and the new
`frontend/src/components/layout/HistorySidebar.tsx` that was fixed in
Phase 1 (add the badge next to the record header).

## Comparison baseline

Previous same-goal runs (task-tracker series):

| File | Confidence | Structure | Runtime | Functional | Cost  |
|------|------------|-----------|---------|------------|-------|
| [a](2026-04-14_task-tracker.md) | 43.6 | — | — | — | $0.675 |
| [b](2026-04-14b_task-tracker.md) | 71.0 | — | — | — | $0.155 |
| [c](2026-04-14c_task-tracker.md) | 61.0 | — | — | — | $0.135 |
| [d](2026-04-14d_task-tracker.md) | 77.0 | — | — | — | $0.146 |
| **this run**                     | **79.01** | 8.18 | 10.00 | 18.33 | $0.277 |

**Δ vs run (d)**: +2.01 points overall. This run exercised more
iterations (5 vs likely fewer) at ~1.9× the cost. The +2 improvement
is within noise given measurement variance, so the change is **not a
regression** against the SmokeTester baseline from run (d), but does
not represent meaningful progress either. The headline finding —
three silent-success iterations — is a new observation not present in
earlier runs, and motivates proposal #1 above.

## Resources still alive after this run

- Dashboard stack: `postgres`, `dashboard` containers (via
  `docker-compose.yml` in this repo).
- Verification stack: `task-tracker-backend` container (port 8000,
  SQLite volume `./backend/data`).
- Session artifacts on the dashboard: `/api/jobs/20260421_193549_f9f4e2/*`
  (~44 files).
- Working tree for verification: `/tmp/lpt-verify/` (~45 MB — node
  modules + venv).

Nothing destroyed automatically. See the teardown prompt at the end of
this run.
