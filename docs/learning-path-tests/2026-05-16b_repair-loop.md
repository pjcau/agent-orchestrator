# Learning-path test — 2026-05-16 b (repair loop ON by default)

**Goal** (verbatim, same as the 2026-05-16 baseline for diffability):
> Build a full-stack Task Tracker: FastAPI backend + SQLite + a seed script that
> inserts 50 sample tasks + a React (Vite + TypeScript) frontend that
> lists/creates/deletes tasks + pytest tests for the API + a docker-compose.yml
> that runs everything. All code in a single repo layout: `backend/`,
> `frontend/`, `docker-compose.yml`, `README.md`.

| | |
|---|---|
| **Session** | `20260516_124050_3e1ac0` |
| **Model** | `qwen/qwen3.5-flash-02-23` (via OpenRouter) |
| **Iterations completed** | **6 / 6** (initial goal + 5 follow-ups) |
| **Cumulative LLM cost** | **$0.2991** |
| **Cumulative wall-clock** | **967 s (16.1 min)** |
| **Repair loop** | ON by default since commit `cd66e5c`; 6 verifier rounds, **0 auto-fixes triggered**, 0 residual failures across all iters |

## Confidence: **49 / 100** (honest)  ·  72 / 100 (what-if with dep gap patched)

| Category | Score | Max | Notes |
|---|---:|---:|---|
| Structure | 10.0 | 10 | all expected paths present |
| Syntax | 15.0 | 15 | py_compile + json clean |
| Build | 10.0 | 20 | `pip install --dry-run` ✓, `docker compose config` ✓, `npm install --dry-run` ✗ (lockfile mismatch) |
| Runtime | **0.0** | 20 | uvicorn fails to start: `models.py` imports `passlib.context`, `crud.py` imports `from jose import jwt` — **neither dep is in `backend/requirements.txt`** |
| Functional | 5.0 | 20 | pytest can't collect (conftest hits the same import error); only the runtime probe smoke gives partial credit |
| LLM-judge | 9.0 | 15 | qwen rated 60/100; main critiques: docker-compose specifies Postgres but goal said SQLite (iter 3 was supposed to add a `db` service alongside the SQLite default); duplicate `api.ts` in frontend |
| **Total** | **49.0** | **100** | |

### What-if: dep gap patched

After `pip install passlib python-jose` (the two missing deps the agent never added to `requirements.txt`), the same harness scores:

| Category | Honest | If deps patched | Δ |
|---|---:|---:|---:|
| Runtime | 0 | **20** | +20 |
| Functional | 5 | 8 | +3 |
| **Total** | **49** | **72** | **+23** |

Runtime probes after patching:
- `GET /` → 200 (48 bytes)
- `GET /docs` → 200 (1015 bytes)
- `GET /tasks` → 200 (**11675 bytes** — seed worked; 50 tasks landed in SQLite)
- `GET /health` → 404 (endpoint never created)

This single class of failure (declared imports without a matching `requirements.txt` entry) cost **23 points**. See proposal 1 below.

## Comparison vs 2026-05-16 baseline (repair OFF)

| Category | Baseline (repair OFF) | This run (repair ON) | Δ | This run, deps patched | Δ patched |
|---|---:|---:|---:|---:|---:|
| Structure | 10.0 | 10.0 | +0.0 | 10.0 | +0.0 |
| Syntax | 13.5 | 15.0 | +1.5 | 15.0 | +1.5 |
| Build | 0.0 | 10.0 | **+10.0** | 10.0 | +10.0 |
| Runtime | 0.0 | 0.0 | +0.0 | **20.0** | +20.0 |
| Functional | 0.0 | 5.0 | +5.0 | 8.0 | +8.0 |
| Judge | 9.0 | 9.0 | +0.0 | 9.0 | +0.0 |
| **Total** | **32.5** | **49.0** | **+16.5** | **72.0** | **+39.5** |

The +16.5 honest lift is real but well below the +52 projected in the design
doc. The lift is concentrated in **Build** (the `pip install --dry-run` pass
caught nothing because the deps that ARE declared do install cleanly). Runtime
is the bottleneck — see proposal 1.

## Iteration log

| Iter | Theme | Cost | Elapsed | repair status | auto-fixed | residual failures |
|---:|---|---:|---:|---|---:|---:|
| 0 | initial goal | $0.0856 | 197 s | passed / 1 attempt | 0 | 0 |
| 1 | data/schema (due_date + /tasks/upcoming) | $0.1048 | 236 s | passed / 1 | 0 | 0 |
| 2 | frontend (upcoming toggle + Due column) | $0.0253 | 110 s | passed / 1 | 0 | 0 |
| 3 | devops (Postgres alongside SQLite) | $0.0175 | 149 s | passed / 1 | 0 | 0 |
| 4 | backend (CSV export) | $0.0291 | 104 s | passed / 1 | 0 | 0 |
| 5 | non-functional (pytest-cov ≥80%) | $0.0368 | 171 s | passed / 1 | 0 | 0 |

**Repair loop summary**: every iteration's `VerificationGate` returned `passed`
on the first attempt. **The pattern registry was never consulted** (no
`auto_fixed_signatures`), because none of the failures the gate cares about
(syntax, JSON encoding, known-bad pins) actually fired. The failure that
mattered here (missing `passlib` / `jose` in `requirements.txt`) is **invisible
to all three current verifiers** — they would each return `passed` on this
workspace.

## Iteration prompts (verbatim)

### 0 — initial goal
```
Build a full-stack Task Tracker: FastAPI backend + SQLite + a seed script that inserts 50 sample tasks + a React (Vite + TypeScript) frontend that lists/creates/deletes tasks + pytest tests for the API + a docker-compose.yml that runs everything. All code in a single repo layout: backend/, frontend/, docker-compose.yml, README.md.
```

### 1 — data/schema change
```
Add a 'due_date' field (ISO 8601 datetime, optional/nullable) to the Task model + Pydantic schemas + CRUD. Update seed.py so the 50 sample tasks have due_dates spread across the next 30 days. Add a new endpoint GET /tasks/upcoming?days=7 returning tasks with due_date within the next N days (default 7). Update existing pytest tests + add 2 new tests for the upcoming endpoint. Do NOT touch the frontend, devops, or auth modules.
```

### 2 — frontend feature
```
Frontend only: in frontend/src/App.tsx (and any helper component), add a 'Show upcoming (next 7 days)' toggle that calls the existing GET /tasks/upcoming endpoint instead of GET /tasks, and a 'Due' column displaying the due_date (formatted YYYY-MM-DD, dash if null). Update frontend/src/types/task.ts. Do NOT touch backend, devops, or tests.
```

### 3 — devops / infra change
```
DevOps only: modify docker-compose.yml to add a 'db' service running postgres:16-alpine with POSTGRES_USER=tasks, POSTGRES_PASSWORD=tasks, POSTGRES_DB=tasks. Change backend.environment.DATABASE_URL to postgresql+psycopg2://tasks:tasks@db:5432/tasks. Add db to backend.depends_on. Update backend requirements.txt to add psycopg2-binary. DO NOT change application code beyond requirements.txt. Make sure the existing SQLite default in backend/database.py still works when DATABASE_URL is unset.
```

### 4 — backend feature
```
Backend only: add a new endpoint GET /tasks/export.csv that streams a CSV of all tasks (columns: id, title, description, completed, due_date) using StreamingResponse. Add 1 pytest test asserting Content-Type is text/csv and the body has a header row + one data row when a task exists. Do NOT touch the frontend or docker-compose.
```

### 5 — non-functional
```
Backend only: add pytest coverage configuration. Add 'pytest-cov' and 'coverage' to backend/requirements.txt. Update backend/pytest.ini (or add pyproject.toml [tool.pytest.ini_options]) to enable --cov=. --cov-report=term-missing --cov-fail-under=80. Add any missing tests required to reach 80% coverage on routers/, crud.py, models.py. Do NOT touch the frontend or docker-compose.
```

## Failures / surprises

1. **The repair loop never triggered a single auto-fix or retry** across 6 iterations. That is consistent with the verifier output (no failures detected), not with reality (the produced repo cannot run without two extra `pip install`s). The verifiers and the actual runtime are checking different things.
2. **`pip install --dry-run` is not enough.** It only verifies that the declared deps resolve. It cannot tell that `models.py` imports `passlib.context` if `passlib` was never declared.
3. **`docker compose config` validates YAML, not behaviour.** The iter-3 prompt asked for Postgres in addition to the SQLite default; the agent replaced SQLite outright in `docker-compose.yml` while leaving `backend/database.py` defaulting to SQLite. The two diverge silently — neither verifier catches it.
4. **The ZIP `/api/jobs/{id}/download` endpoint was flattening to top-level files only** — a real bug found while running this validation. Fixed in this same PR (`gateway_api.py::jobs_download_zip` now walks recursively + skips `__pycache__`); previously the verification only saw 9 files instead of the actual 50, dropping the Structure score to 3.3/10 in the first pass.
5. **The team-run report dict gained a `repair: {...}` block**, but no UI surfaces it yet. The data is there (status, attempts, auto-fixed signatures, residual failures); the React dashboard doesn't render it.

## Improvement proposals

1. **Add an `ImportVerifier` to close the dep-vs-import gap.** Cheap and direct: after `pip install -r requirements.txt`, run `python -c "import <each top-level module under backend/>"` in a subprocess and capture stderr. Any `ModuleNotFoundError` surfaces as a `VerifierFailure` with `category="missing_dep"` and `message="No module named 'X'"`. Add a paired `FailurePattern` (`missing_dep_install`) that parses the module name out of the error and appends it to `requirements.txt` deterministically (just like `pip_pin_repair` already does for known-bad pins). This is exactly the class of failure that cost us 23 points on this run and is the single highest-ROI verifier I can think of for the next iteration of the design. Files: new `core/verifiers/imports.py`, extend `core/failure_patterns.yaml`.

2. **Add a `WorkspaceCoherenceVerifier` for cross-file contradictions.** When iter 3 asked to add a `db` service while keeping the SQLite default, the agent removed `sqlite` defaults from `docker-compose.yml` but left `backend/database.py` defaulting to SQLite. A specialised verifier could parse the `services.*.environment.DATABASE_URL` from `docker-compose.yml` and confirm `backend/database.py` honours both code paths. Generalising: any time iter N depends on iter N-1's state, a verifier that diffs the workspace against the previous iter's state would catch drift. Files: new `core/verifiers/coherence.py`, possibly hooked off the new `repair.escalated` event so it only runs when the basic gate already passed.

3. **Surface the `repair` block in the dashboard React UI**, and add a small "Workspace verification" panel showing the per-verifier outcome of each attempt. The data is already in `result.repair`; today nothing renders it, so users have no visual signal whether the repair loop actually did anything. Even a single-row badge on the team-run card would close the loop. Files: `frontend/src/components/team/TeamRunCard.tsx` (or wherever the team status lives), `frontend/src/types/teamRun.ts`.

Bonus follow-up (out of scope for the repair-loop sprint but worth filing):
**`pip install --dry-run` is too coarse a build check.** Replacing it with an actual `pip install` inside a throwaway venv would catch all install-time errors plus the import-time ones from proposal 1 in a single shot. The cost is one cold venv create per verification (~10 s) — well within the repair-loop budget.

## Verification details (top failures)

### Build — `npm install --dry-run` failure tail
```
npm warn EBADENGINE Unsupported engine {
npm warn EBADENGINE   package: 'vite@5.x',
npm warn EBADENGINE   required: { node: '^18.0.0 || >=20.0.0' },
npm warn EBADENGINE   current:  { node: 'v22.x', npm: 'x' }
...
```
(Lock-file mismatch / engine warning — non-blocking, but the dry-run exits non-zero.)

### Runtime — uvicorn never reached binding stage
```
ModuleNotFoundError: No module named 'passlib'
```
After `pip install passlib`:
```
ModuleNotFoundError: No module named 'jose'
```
After `pip install python-jose`: app boots, `/`, `/docs`, `/tasks` all 200.

### LLM-judge raw reply
```json
{"score": 60, "reasons": [
  "Docker Compose specifies PostgreSQL database but initial requirement explicitly states SQLite",
  "Cannot verify Iter 1 due_date field implementation without file contents - may be missing from models.py/schemas.py",
  "Duplicate API client files exist (frontend/src/api.ts AND frontend/src/services/api.ts) indicating inconsistent code organization",
  "Auth router exists (backend/routers/auth.py) with no corresponding auth implementation in frontend docker-compose setup - unexplained feature",
  "Severed prompt at Iter 1 ('Add a ne...') - cannot verify if the new feature was implemented",
  "Multiple test files suggest extensive testing but cannot verify if required API tests from Iter 0 exist and pass",
  "Backend volume mount (/app/data) doesn't match PostgreSQL volume location (/var/lib/postgresql/data) in docker-compose - potential data persistence issues"
]}
```

## Bug found & fixed during the run

While downloading the session ZIP for verification, `/api/jobs/{id}/download`
returned only top-level files — all subdirectories (`backend/`, `frontend/`,
`backend/routers/`, etc.) were silently dropped. Initial verification scored
23/100 because of this; after fixing the endpoint to walk recursively (and skip
`__pycache__`), the same artifacts scored 49/100. Fix + regression test
included in this commit (`tests/test_dashboard.py::TestExplorerEndpoints::test_jobs_download_zip`).

## Resources left running

- Dashboard stack (`agent-orchestrator-{dashboard,postgres}-1`) on host.
  Repair loop ON by default in this container's env.
- Session artifacts on disk: `~/.../job_20260516_124050_3e1ac0/` (kept for inspection).
- Verification workdir + venv: `/tmp/lpt_phase7/` (kept for inspection).
- `docker-compose.yml` has a temporary patch: sandbox port range `9100-9119`
  instead of `9000-9019` (portainer was on 9000). Revert when portainer not running.

No teardown performed.
