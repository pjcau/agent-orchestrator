---
name: orchestrator-learning-path-test
description: Learning-path test for the orchestrator. Pick a project goal, run it through the local stack, issue 5 follow-up iterations (modifications or new features), verify the final output against the accumulated requirements, and append a dated log under docs/learning-path-tests/ so runs can be compared over time. Produces a confidence score (0-100) with breakdown and 3 concrete improvement proposals.
allowed-tools: Bash, Read, Write, Edit, Grep, Glob
user-invocable: true
argument-hint: [optional initial project topic]
---

# Orchestrator Learning-Path Test

Runs a realistic multi-turn project through the orchestrator and verifies what came out. The assistant decides iterations on-the-fly based on previous output, and judges the final result against the full goal + iteration history. After reporting, the run is logged under `docs/learning-path-tests/` so future runs can be diffed against this one.

## Hard limits

- **Budget cap**: $2.00 total LLM spend across goal + 5 iterations. Abort iterations early (leave remaining for report) if cumulative cost exceeds $1.80 or if any single iteration exceeds $0.60.
- **Time cap**: 30 min wall-clock for the whole run. Abort iterations if exceeded.
- **Iterations**: exactly 5 unless budget/time abort.

## Phase 0 — Pick the goal

If the user passed a topic as argument, use it as-is.

Otherwise default to this goal (chosen for breadth — touches backend, DB, seed, API, frontend, docker, tests):

> **"Build a full-stack Task Tracker: FastAPI backend + SQLite + a seed script that inserts 50 sample tasks + a React (Vite + TypeScript) frontend that lists/creates/deletes tasks + pytest tests for the API + a docker-compose.yml that runs everything. All code in a single repo layout: `backend/`, `frontend/`, `docker-compose.yml`, `README.md`."**

Announce the chosen goal to the user before starting.

## Phase 1 — Boot local stack

1. Verify OrbStack/Docker is up: `docker ps >/dev/null 2>&1`
2. Start dashboard: `docker compose up -d dashboard postgres`
3. Poll health up to 60s: `curl -skf https://localhost:5005/health || curl -sf http://localhost:5005/health`
4. If health never comes up, run `docker compose logs --tail=50 dashboard` and STOP with the error.

Capture the base URL (`http://localhost:5005` or https if TLS) in a shell var `BASE`.

## Phase 2 — Launch the initial goal

```bash
curl -sf "$BASE/api/team/run" \
  -X POST -H "Content-Type: application/json" \
  -d '{"goal": "<GOAL>", "category": "software-engineering"}'
```

Response contains `job_id` (aka `session_id`). Save it as `SESSION`.

Poll `$BASE/api/team/status/$SESSION` every 10s until `status` in `{completed, failed}`. Max 10 min per iteration.

Log to a scratch file `/tmp/stress-test-$SESSION.log`:
- iteration index
- prompt sent
- final status
- tokens / cost delta (from `GET $BASE/api/usage/session/$SESSION` if available, otherwise from the team response payload)
- wall-clock duration

## Phase 3 — Five iterations (on-the-fly)

For each iteration i=1..5:

1. **Pull current artifacts** to understand what the agents actually built:
   `curl -sf "$BASE/api/jobs/$SESSION/files"` — list.
   Read 3-5 of the most-relevant files (README, main API file, docker-compose) to ground the next request.

2. **Decide the next request.** Rules for crafting it:
   - Must be concrete (one user-visible change, not "improve everything")
   - Must build on what exists (don't ask for features already done)
   - Mix categories across the 5 iterations: at least 1 backend feature, 1 frontend feature, 1 data/schema change, 1 devops/infra change, 1 non-functional (tests, docs, perf)
   - Examples (adapt to actual state): "add JWT auth with signup/login endpoints"; "add category filter + pagination on the frontend list"; "migrate SQLite to Postgres via docker-compose service, keep schema"; "add a CSV export endpoint and a download button"; "add pytest coverage reporting and reach ≥80% on the backend"

3. **Send** via the same session_id so history carries over:
   ```bash
   curl -sf "$BASE/api/team/run" -X POST -H "Content-Type: application/json" \
     -d "{\"goal\": \"<ITERATION PROMPT>\", \"session_id\": \"$SESSION\", \"category\": \"software-engineering\"}"
   ```

4. Poll status as in Phase 2.

5. **Budget guard** after each iteration:
   - If cumulative cost > $1.80 → stop iterating, jump to Phase 4
   - If cumulative time > 25 min → stop iterating, jump to Phase 4

Record each iteration's prompt verbatim — needed for the LLM-judge phase.

## Phase 4 — Pull final artifacts

1. Create work dir: `WORK=$(mktemp -d)/final`
2. List files: `curl -sf "$BASE/api/jobs/$SESSION/files" > $WORK/../files.json`
3. Download ZIP: `curl -sf "$BASE/api/jobs/$SESSION/download" -o $WORK/../session.zip && unzip -q $WORK/../session.zip -d $WORK`
4. If ZIP endpoint fails, fall back to downloading each file via `GET /api/jobs/$SESSION/files/{name}`.

## Phase 5 — Verify (weighted 0-100)

Run each check independently. A failure in one category sets that category's score to 0 but does not stop the others.

### 5.1 Structure (10%)

Expected for default goal: `backend/`, `frontend/`, `docker-compose.yml`, `README.md`, at least one test file, at least one seed script.

For custom goals: infer expected structure from the goal text.

Score = (files_found / files_expected) * 10.

### 5.2 Syntax / Import (15%)

```bash
# Python syntax
find $WORK -name "*.py" -print0 | xargs -0 -n1 python -m py_compile 2>&1 | tee $WORK/../syntax-py.log
# JS/TS: rely on tsc if tsconfig.json exists, else node --check
```

Score: 15 if zero errors; linear degrade: `15 * max(0, 1 - errors/10)`.

### 5.3 Build (20%)

Spin up a fresh sandbox via the orchestrator's own sandbox API (isolated from the main run):

```bash
SANDBOX_ID="verify-$SESSION"
# copy $WORK into sandbox workspace, then try:
#  - pip install -r backend/requirements.txt (if present)
#  - npm ci --prefix frontend (if present)
#  - docker compose build (if docker-compose.yml present)
```

Score: 20 if all relevant builds pass. Partial credit per step succeeded.

### 5.4 Runtime (20%)

In the same sandbox:
- `docker compose up -d` (or run backend + frontend directly)
- wait 20s, poll backend health endpoint (`/health`, `/`, `/docs`)
- check DB is reachable + seed rows exist (if schema mentions seed): `SELECT COUNT(*) FROM tasks` or equivalent

Score: 20 if app boots and health responds + seed present. 10 if boots but no seed. 0 if crash.

### 5.5 Functional (20%)

Hit 3-5 concrete endpoints based on the goal and iterations. For the default task-tracker:
- `GET /tasks` → 200, non-empty list
- `POST /tasks` with a payload → 201, returns id
- `GET /tasks/{id}` → 200, matches posted
- `DELETE /tasks/{id}` → 204
- features added in iterations (auth, filters, export) → one spot-check each

Score: 20 * (endpoints_passing / endpoints_tested).

### 5.6 LLM-judge (15%)

Build a prompt containing:
- the original goal
- the verbatim list of the 5 iteration prompts (or fewer if aborted)
- the final file tree (paths only, no contents to save tokens)
- the contents of README.md and docker-compose.yml

Ask Opus: "Rate 0-100 how well the delivered repo satisfies the accumulated requirements. List up to 5 specific mismatches."

Score = (judge_score / 100) * 15.

### Teardown — ASK BEFORE DESTROYING

Do NOT tear anything down automatically. After the report, list the resources still alive:
- the dashboard stack (postgres + dashboard)
- the verification sandbox (`$SANDBOX_ID`) and its workspace dir (`$WORK`)
- the generated session artifacts on the dashboard (`/api/jobs/$SESSION/...`)

Then ask the user explicitly which to drop (e.g. "Keep sandbox for inspection? Drop dashboard stack? Delete session?"). Only run destructive commands (`docker compose down -v`, `DELETE /api/sandbox/...`, `DELETE /api/jobs/...`, `rm -rf $WORK`) after the user says yes for that specific resource.

## Phase 6 — Report & log

1. Print the report block below to the user.
2. Save a copy as `docs/learning-path-tests/YYYY-MM-DD_<short-slug>.md`
   (slug derived from the goal, e.g. `task-tracker`, `chat-bot`).
3. Update the `Runs` table in `docs/learning-path-tests/README.md` with the
   new row (date · file · topic · model · cost · confidence).
4. If this run followed an earlier run on the same topic, fill the
   "Comparison baseline" matrix in the new file: Δ Structure, Δ Runtime,
   Δ Functional vs the previous run, plus any finding that was resolved.

```
ORCHESTRATOR LEARNING-PATH TEST — REPORT
==========================================
Goal: <goal, truncated to 120 chars>
Session: <session_id>
Iterations completed: <N>/5    (reason if <5: budget/time)
Duration: <mm:ss>    Cost: $<X.XX>    Tokens: <in/out>

Confidence: <SCORE>/100

Breakdown:
  Structure   <x>/10
  Syntax      <x>/15
  Build       <x>/20
  Runtime     <x>/20
  Functional  <x>/20
  LLM-judge   <x>/15

Iteration requests:
  1. <verbatim prompt>
  2. ...

Failures / surprises:
  - <concrete fact from logs>

Improvement proposals (3):
  1. <e.g. "system prompt under-specifies DB schema — agents invented 3 different ones across iterations. Add a `schema.sql` pinning step in the team-lead template.">
  2. ...
  3. ...
```

Proposals must be:
- **concrete** (mention a file, prompt, agent, or config to change)
- **grounded** in something observed during the run (not generic advice)
- **actionable** as a standalone small PR

## Error handling

- Stack won't boot → STOP, report docker logs tail.
- Orchestrator returns 500 on `/api/team/run` → retry once after 15s; if still failing, STOP and dump the error body.
- Verification sandbox can't be created → skip Build/Runtime/Functional (score 0 each), continue with the rest, note it in the report.
- Budget abort → still run Phase 4/5/6 with what was produced; mark in the report.

## What NOT to do

- Don't edit any project source files — this skill only *runs* the orchestrator and *reads* its output.
- Don't push or commit anything.
- Don't tear down anything (stack, sandbox, generated artifacts) without explicit user confirmation. Leave it running by default — creating is free, destroying is not.
