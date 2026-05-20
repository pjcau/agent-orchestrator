# Orchestrator Learning-Path Test — 2026-05-16 (task-tracker)

```
ORCHESTRATOR LEARNING-PATH TEST — REPORT
==========================================
Goal: Build a full-stack Task Tracker: FastAPI backend + SQLite + a seed script
      that inserts 50 sample tasks + a React (Vite + TypeScript) frontend
      that lists/creates/deletes tasks + pytest tests for the API +
      a docker-compose.yml that runs everything.
Session: 20260516_091607_ea5484
Model:   openai/gpt-4o-mini requested (via OpenRouter), but the
         orchestrator's router auto-redirected backend-dev / frontend-dev
         calls to qwen/qwen3.5-flash-02-23. Actual mix:
           gpt-4o-mini  33/49 requests, 317 679 tokens, $0.000 (free tier)
           qwen3.5-flash 16/49 requests, 282 655 tokens, $0.037
         All cost came from qwen.
Iterations completed: 5/5 (no budget/time abort)
Duration: ~12:20 wall-clock for the 6 runs
Cost:    $0.037 total (well under the $2.00 cap)
Tokens:  600 334 (gpt-4o-mini: 317 679 + qwen-routed: 282 655)

Confidence: 32.5/100

Breakdown:
  Structure   10.0/10
  Syntax      13.5/15
  Build        0.0/20
  Runtime      0.0/20
  Functional   0.0/20
  LLM-judge    9.0/15
```

## Iteration requests

0. `Build a full-stack Task Tracker: FastAPI backend + SQLite + a seed script that inserts 50 sample tasks + a React (Vite + TypeScript) frontend that lists/creates/deletes tasks + pytest tests for the API + a docker-compose.yml that runs everything. All code in a single repo layout: backend/, frontend/, docker-compose.yml, README.md.`
1. `BACKEND FEATURE — add a 'status' column (todo/in-progress/done, default todo), GET /tasks/{id} (200+404), PUT /tasks/{id} (partial update), update test_main.py to cover the new endpoints, seed 50 tasks with mixed statuses. Idempotent ALTER TABLE.`
2. `DATA/SCHEMA — migrate from SQLite to PostgreSQL: add postgres service to docker-compose with healthcheck, use SQLAlchemy + psycopg, preserve schema, restore the 3 endpoints (GET list / POST / DELETE) that iter 1 lost.`
3. `FRONTEND — fix App.tsx field mismatch (name→title), add status-badge colours, add a status filter dropdown, add 20/page pagination, add status dropdown to create form, configure vite proxy /api→backend:8000. Restore backend+frontend services in docker-compose (iter 2 had dropped them). Add uvicorn to requirements.txt.`
4. `DEVOPS — backend/Dockerfile (python:3.12-slim + uvicorn), frontend/Dockerfile (node:20-alpine build → nginx:alpine serve), nginx.conf with /api → backend:8000 proxy, top-level README.md, tsconfig.json. Restore the create form + delete button that iter 3 had dropped from App.tsx.`
5. `NON-FUNCTIONAL — add pytest-cov + backend/pyproject.toml with --cov-fail-under=80, rewrite test_main.py for full coverage of all 5 endpoints, update top-level README. Restore the status filter + pagination that iter 4 had dropped from App.tsx (fix bare "export default" syntax error).`

## Per-iteration timings & cost

| Iter | Status     | Elapsed | Files in session (cumulative) |
|------|------------|---------|-------------------------------|
| 0    | completed  | 230s    | 18                            |
| 1    | completed  |  60s    | 20                            |
| 2    | completed  | 170s    | 21                            |
| 3    | completed  |  60s    | 21                            |
| 4    | completed  |  90s    | 29                            |
| 5    | completed  | 130s    | 32                            |

Total: ~740 s of agent work · 49 LLM requests across 11 distinct agents
(frontend-dev, backend-dev, devops, ai-engineer, migration-helper,
team-lead in plan/summary/validation phases, plus per-iteration backend /
frontend specialists).

## Failures / surprises

1. **Repeating "overwrite, not edit" regression in every iteration.** The
   agents kept rewriting `backend/main.py` and `frontend/src/App.tsx`
   from scratch instead of editing, silently dropping features the
   previous iteration had just added:
   - Iter 1 deleted GET-list / POST / DELETE while adding GET/PUT-by-id.
   - Iter 2 deleted the `backend` and `frontend` services from
     `docker-compose.yml` while adding the `postgres` service.
   - Iter 3 deleted the create form and delete button from `App.tsx`
     while adding the status filter and pagination.
   - Iter 4 deleted the status filter and pagination while restoring the
     create form and delete button.
   - Iter 5 was needed largely to undo the iter 4 regression — five
     iterations of work, three of them effectively spent fixing what
     the previous one had broken.

2. **`/api/jobs/{session_id}/download` zips only top-level files.**
   The returned `session.zip` (12.5 kB) contained `README.md`,
   `docker-compose.yml`, `tasks.db`, and the six `*_team_run.json`
   records — but **none of `backend/` or `frontend/`**, even though
   both directories are listed by `/api/jobs/.../files`. Had to fall
   back to per-file `GET /api/jobs/.../files/{path}` loop.

3. **Literal `\n` strings written into source files.** `frontend/package.json`
   in the final state is a single line containing `\n` substrings
   instead of actual newlines, making `python -m json.tool` reject it
   and `npm ci` impossible:
   ```
   {\n  "name": "task-tracker-frontend",\n  "version": "1.0.0",\n  ...
   ```
   The same encoding bug hit `frontend/src/App.tsx` in iter 0 and got
   silently corrected when iter 3 rewrote the file. So the bug is
   **intermittent**, which is worse — only later iterations rescue it.

4. **`psycopg>=2.9,<3` dependency does not exist on PyPI.** The agents
   meant `psycopg2-binary` (the v2.x distribution name) or
   `psycopg>=3.0` (the new package). The chosen pin matches **no
   uploaded release** → `pip install` fails → backend Dockerfile fails
   → `docker compose build` fails → no runtime, no functional check.
   Single dependency-name typo cascaded to **40 points of score loss**
   (Build + Runtime + Functional = 60/100 unreachable).

5. **Schema choice contradicted the prompt.** Iter 1 was asked for
   `'todo' | 'in-progress' | 'done'`; the resulting Postgres enum is
   `'todo' | 'in_progress' | 'done'`. The frontend filter (also
   added later) compares `"In Progress".toLowerCase() === task.status`,
   i.e. `"in progress"` vs `"in_progress"` — would silently never match
   the In-Progress filter even if everything else worked.

6. **`team_run` API ignores `session_id` / has no resume primitive.**
   The skill assumed `session_id` could be passed back on subsequent
   `POST /api/team/run` calls to thread iterations into the same run.
   In this codebase the field is `conversation_id`; without one each
   `team_run` starts a fresh `run_team` call. Continuity only happens
   because the dashboard's `JobLogger` reuses the "current" session
   directory — *filesystem-level* threading, not
   *conversation-level*. Iteration context was inlined into each
   prompt instead.

## Improvement proposals (3)

1. **Edit-in-place tool guard for multi-turn refactors.** Add a
   middleware (similar to `core/loop_detection.py`) that watches
   `Edit` / `Write` calls on a file across iterations of the same
   session and **refuses a Write that shortens the file by >30 %**
   unless the agent passes an explicit `intent: "rewrite"` flag.
   Drop it into `src/agent_orchestrator/skills/filesystem.py`'s
   write-path. The five regressions in this run are all consistent
   with `Write` being preferred over `Edit` when the model thinks
   "I'll just regenerate the file." Same root cause as the
   2026-04-14b → 2026-04-14c plateau noted in the previous logs.

2. **Fix the zip-download endpoint to recurse.** The handler in
   `src/agent_orchestrator/dashboard/gateway_api.py` for
   `/api/jobs/{session_id}/download` clearly only zips immediate
   children of `JobLogger.session_dir`. Switch to
   `os.walk(session_dir)` and write relative paths into the zip.
   Add a single integration test: ingest two files under
   `subdir/`, request the download, assert both are extracted.
   Without this fix every learning-path test silently exercises
   only the fallback per-file loop.

3. **Reject string-escaped newlines at the
   filesystem-skill boundary.** In
   `src/agent_orchestrator/skills/filesystem.py`, before writing a
   text file, scan for `r"\\n"` sequences that do not contain a real
   newline anywhere in the same 200-character window. If detected,
   apply `content.encode().decode('unicode_escape')` once and re-check;
   if still suspicious, **fail the tool call** with a clear error so
   the agent retries instead of silently writing a single-line-with-`\n`
   file. This run lost `frontend/package.json` (and would have lost
   `App.tsx` if iter 3 hadn't rewritten it) to exactly this bug.

## Comparison baseline — vs 2026-04-21 (task-tracker run e)

| Category   | 2026-04-21 (qwen3.5-flash) | 2026-05-16 (gpt-4o-mini) | Δ |
|------------|---------------------------:|-------------------------:|--:|
| Structure  | 8.18                       | 10.00                    | +1.82 |
| Syntax     | 15.00                      | 13.50                    | −1.50 |
| Build      | 20.00                      | 0.00                     | **−20.00** |
| Runtime    | 10.00                      | 0.00                     | **−10.00** |
| Functional | 18.33                      | 0.00                     | **−18.33** |
| LLM-judge  | 7.50                       | 9.00                     | +1.50 |
| **Total**  | **79.01**                  | **32.50**                | **−46.51** |

The 46-point drop is **dominated by a single dependency typo** in
`backend/requirements.txt` (`psycopg<3` — invalid pin name) that
cascaded through Build / Runtime / Functional. Strip that out and the
run scores ~52 — still −27 vs the 2026-04-21 baseline because of the
**file-overwrite regression pattern** (proposal #1) the older run did
not exhibit. Two prior-run findings have NOT been carried forward:

- **Silent-success failure** (iters 3/4/5 in 2026-04-21 reported success
  but wrote zero files) — *not reproduced* here. Every iteration in
  this run wrote new/updated files. Whatever was added between
  Phase 1 and 2026-05 stuck.
- **High-fidelity baseline of 79.01** — *regressed*. Possible causes:
  model swap (`gpt-4o-mini` vs `qwen3.5-flash`), no Phase-2 verification
  gate kicked in for the failing dependency, or the over-prescriptive
  prompt template for iter 5 ("restore + add + cover ≥80%") overloaded
  the agent.

## Resources still alive (NOT torn down by this skill)

Per the skill's rules, no destructive action was taken automatically.
The following remain:

- **Dashboard stack** — `docker compose ps` shows
  `agent-orchestrator-dashboard-1` and `agent-orchestrator-postgres-1`
  still up. `ALLOW_DEV_MODE=true` is currently in `.env.local`
  (backup at `/tmp/lpt-2026-05-16/.env.local.backup`).
- **Portainer** — was stopped at the start of this run
  (`docker stop portainer`) to free port 9000 for the sandbox range.
  Still stopped.
- **Session artifacts on the dashboard** — `20260516_091607_ea5484`
  with 32 files. Reachable via
  `GET /api/jobs/20260516_091607_ea5484/...`.
- **Local scratch workspace** — `/tmp/lpt-2026-05-16/` (≈40 kB:
  client.py, scratch.log, work/final/, judge.txt).

Ask the user which to drop. The defaults to suggest, in order:

1. `docker start portainer` to restore the user's previous environment.
2. Revert `.env.local` (`cp /tmp/lpt-2026-05-16/.env.local.backup .env.local && docker compose up -d --force-recreate dashboard`) to re-enable auth.
3. `docker compose down` if the dashboard stack is no longer needed.
4. `rm -rf /tmp/lpt-2026-05-16/` to wipe the scratch workspace.
5. `DELETE /api/jobs/20260516_091607_ea5484` to drop the session.
