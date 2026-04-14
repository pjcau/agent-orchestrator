# Learning-Path Test — 2026-04-14 — Task Tracker full-stack

## Goal

> Build a full-stack Task Tracker: FastAPI backend + SQLite + a seed script
> that inserts 50 sample tasks + a React (Vite + TypeScript) frontend that
> lists/creates/deletes tasks + pytest tests for the API + a docker-compose.yml
> that runs everything. Layout: `backend/`, `frontend/`, `docker-compose.yml`,
> `README.md`.

## Run metadata

| Key | Value |
|---|---|
| Sessions | `20260414_180433_06ae1d` (iter 0-2), `20260414_182820_f85d08` (iter 3-5) |
| Iterations completed | 5 / 5 |
| LLM wall-clock | 20m41s |
| Total cost | **$0.675** / $2.00 budget |
| Total tokens | 7,323,315 |
| Models | iter 0 → `qwen/qwen3-coder-next` ($0.12/$0.75 per M) · iter 1-5 → `qwen/qwen3.5-flash-02-23` ($0.06/$0.30 per M) |
| Dashboard auth | `ALLOW_DEV_MODE=true` (local) |
| Sandbox | `SANDBOX_ENABLED=true`, unused — agents wrote directly via skills |

### Per-iteration breakdown

| # | Topic | Duration | Cost | Tokens | Agents | Success flag | Notes |
|---|-------|---------:|-----:|-------:|--------|:------------:|-------|
| 0 | initial goal (full project) | 425s | $0.338 | 2,394,821 | backend, frontend, devops, test-runner, content-strategist | ✅ | all 5 agents hit `max steps without completing` · duplicate backend layouts emitted |
| 1 | schema extension + layout cleanup | 228s | $0.117 | 1,721,734 | backend, migration-helper, test-runner | ✅ | 2 of 3 agents hit max steps · `Stalled: too many retries on shell_exec` |
| 2 | JWT auth (User model + signup/login + protect POST/DELETE) | 175s | $0.096 | 1,450,671 | backend, code-reviewer, test-runner | ✅ | all 3 hit max steps · output LOST (see finding #1) |
| 3 | frontend toolbar + filters + search | 81s | $0.006 | 63,749 | frontend | ✅ | only iteration with a clean exit — 16 steps, well under cap |
| 4 | migrate SQLite → Postgres | 154s | $0.068 | 1,007,303 | devops, backend, code-reviewer, test-runner | ✅ | 2 of 4 hit max steps |
| 5 | CI + coverage (non-functional) | 178s | $0.049 | 685,037 | devops, backend ×2, frontend, code-reviewer | ⚠️ | 5 agents selected, only 1 file produced (CODE_REVIEW_ITERATION5.md); ci.yml/pytest.ini showed up in other sessions |

## Confidence: **43.6 / 100**

| Category | Score | Max | Evidence |
|----------|------:|----:|----------|
| Structure  | 9.33  | 10 | 14 of 15 expected files present (missing: root-level seed_tasks.py — it lives at `backend/seed_tasks.py`) |
| Syntax     | 12.00 | 15 | Python: 0 errors on 23 files · TS/TSX typecheck deferred (no npm install in verify) |
| Build      | 5.00  | 20 | `requirements.txt` pins `pydantic==2.5.6` — **version does not exist on PyPI** |
| Runtime    | 7.00  | 20 | app imports only after 2 manual fixes: `Base.metadata.create_all()` missing `bind=engine`; `email-validator` not in requirements |
| Functional | 5.00  | 20 | `GET /api/v1/tasks` → 200 `[]` · `POST /api/v1/tasks` → 500 `ResponseValidationError` |
| LLM-judge  | 5.25  | 15 | delivered a structural skeleton but not a runnable app; lost iter 1/2 work to pollution; no `/auth/*` endpoints in final |

## Findings

### 1. [CRITICAL] Agents wrote outside the session workspace — found and fixed mid-run

Iterations 0-2 emitted files with absolute paths (`/workspace/backend/x.py`) or
relative paths resolved against the dashboard process CWD (`/workspace`). The
project volume mount is `.:/workspace`, so those writes landed in
**`/Users/.../agent-orchestrator/backend/`** on the host — 37 MB of pollution
including a full venv.

**Root cause:**
- `FileWriteSkill` / `FileReadSkill` / `GlobSkill` treated `working_directory`
  as a hint: relative paths were joined against it, but absolute paths
  bypassed it entirely.
- `ShellExecSkill` sets `cwd=_cwd` on the subprocess, so `mkdir backend` from
  an agent would correctly land in the session dir; but `cd /workspace &&
  mkdir backend` still escaped.
- `run_agent()` never told the LLM what its working directory was — the
  system role was just `"You are {agent_name}. Be concise and practical."`

**Fix (applied during this run, untracked):**
- `src/agent_orchestrator/skills/filesystem.py` — new `_confine(cwd, raw)`
  helper that remaps absolute paths escaping `cwd` under `cwd` chroot-style.
  Applied to all three filesystem skills.
- `src/agent_orchestrator/dashboard/agent_runner.py` — appends the working
  directory + a "use relative paths" rule to every default agent role.
- `tests/test_filesystem_confinement.py` — 5 regression tests, all green.

Runs after the fix (iter 3-5) wrote exclusively into the session dir. Zero
host-project pollution.

**Still TODO** (deferred, not done this run):
- Second defence layer in `ShellExecSkill` to reject/remap absolute paths
  in commands (or run commands under `unshare --root` / a real sandbox).
- An integration test that spins up a real team run and asserts
  `git status` stays clean except for session dirs.

### 2. Agents routinely hit `max_steps` without a clean "done" signal

Of 22 agent executions across the 6 runs, 14 ended with either
`Agent reached max steps without completing` or `Stalled: too many retries on
shell_exec`. File output is still collected (via tool calls), so the run
doesn't crash, but the agent never emits a final structured summary — the
`team-lead (summary)` step has to reconstruct everything from tool logs.

Symptoms consistent with a tool-loop: agents keep trying small shell commands
(install, cd, ls, mkdir) instead of making progress. `qwen3.5-flash` and
`qwen3-coder-next` both suffer from this.

### 3. README routed to `content-strategist` (marketing)

On iter 0 the team-lead plan assigned the README to `content-strategist`
(a marketing agent). Technical documentation is in software-engineering's
territory. Keyword-driven category routing seems to fire on "README" via
soft word overlap with marketing content.

### 4. LLM hallucinated a dependency version

`backend/requirements.txt` pins `pydantic==2.5.6` — that exact version has
never been released. No validation catches this: no `pip install` runs during
the orchestrator's own flow, no doc-sync step, no CI (the CI workflow that
iter 5 was supposed to add mostly failed to land).

### 5. Broken runtime code shipped under a "success" flag

- `main.py`: `Base.metadata.create_all()` called without `bind=engine` → crashes on first import.
- `main.py` imports `from api.v1.routers import task as task_router`; the
  tasks module also exists under `api/v1/endpoints/tasks.py` → two parallel
  layers, no clear source of truth.
- The task schema response triggers `fastapi.exceptions.ResponseValidationError`
  on POST — the DB model and the Pydantic out-schema are out of sync
  (probably the `priority` enum shape mismatches).

Every iteration reported `success: True`. No iteration actually ran the app.

### 6. Iter 2 (JWT auth) output was lost

Most of iter 2's work went into the polluted `/workspace/backend` host
directory (before the fix landed). That directory was deleted during cleanup
before the verification ran. The final merged repo has **zero `/auth/*`
routes** despite a "successful" iter 2 run.

### 7. Iter 5 (CI + coverage) degenerated into a review document

Team-lead selected 5 agents (devops, backend×2, frontend, code-reviewer).
Only `code-reviewer` produced a tangible output: `CODE_REVIEW_ITERATION5.md`.
The requested `.github/workflows/ci.yml` and `backend/pytest.ini` were
present in the merged workspace (created by other agents in earlier sessions
that coincidentally handled similar topics), but the actual iter-5 prompt
mostly idled.

## Improvement proposals

### P1 — Treat `working_directory` as a hard root, not a hint

**Status:** filesystem half done (this run). Shell half pending.

Files to edit next:
- `src/agent_orchestrator/skills/shell.py` — parse the command string, reject
  or rewrite absolute paths that escape `_cwd`, or prefix with `chroot`/`unshare`.
- New test in `tests/test_shell_confinement.py` — assert shell commands can't
  write outside `_cwd`.
- `tests/test_agent_runner.py` — end-to-end: spawn a team run, assert
  `git status --porcelain` on the host project stays empty.

### P2 — Add a `smoke_build` gate before `team-lead (summary)`

Between the agent fan-out and the final summary, run a lightweight build
check in the sandbox:

- Python: `pip install --dry-run -r requirements.txt` and `python -c "import
  main"` (or `uvicorn main:app --help`).
- Node: `npm ci --dry-run`.
- Report failures as structured feedback that gets injected into the summary
  and ideally triggers one retry round.

Would have caught `pydantic==2.5.6` and the `create_all()` bug instantly.

File to edit: `src/agent_orchestrator/dashboard/agent_runner.py` or the
`run_team()` orchestration (wherever `team-lead (validation)` sits today) —
extend it to actually *exercise* the output, not just read it.

### P3 — Fix category routing for README / docs tasks

File: `src/agent_orchestrator/dashboard/graphs.py` (or the plan resolver
inside `run_team()`).

Add a hard rule: technical documentation (README, API docs, CHANGELOG,
docstrings) → `ai-engineer` or `backend`. `content-strategist` is only
routed when the task explicitly contains marketing keywords (campaign,
copy, brand, audience, SEO, social).

## Model insight (bonus)

| Model | Iter | $/M in | $/M out | Iter cost | Tokens | Quality obs |
|-------|-----:|--------:|---------:|----------:|-------:|-------------|
| qwen/qwen3-coder-next | 0 | $0.12 | $0.75 | $0.338 | 2.39M | heavy, all 5 agents maxed 30 steps |
| qwen/qwen3.5-flash-02-23 | 1-5 | $0.06 | $0.30 | $0.337 total | 4.93M | half the cost per token, same max-steps problems |

For this orchestrator's workload (many shell/file tool calls, short
reasoning), **Flash is the pragmatic default**. The coder-next model did
not produce visibly better output in return for 2× the price.

## Files (post-verification merged workspace)

```
./
├── .env.example
├── .github/workflows/ci.yml
├── CODE_REVIEW.md, CODE_REVIEW_FINAL.md, CODE_REVIEW_ITERATION5.md, DEPLOYMENT.md
├── README.md
├── docker-compose.yml
├── setup.sh, test_api.sh
├── backend/
│   ├── Dockerfile, entrypoint.sh, run_tests.sh
│   ├── alembic.ini, pytest.ini, requirements.txt, requirements-dev.txt
│   ├── main.py, seed_tasks.py
│   ├── alembic/{env.py, script.py.mako, versions/}
│   │   └── versions/
│   │       ├── 0001_create_tasks_table.py
│   │       ├── 001_initial_migration.py     ← duplicate of 0001
│   │       ├── 002_add_priority_and_due_date.py
│   │       └── 003_add_users_table.py
│   ├── api/v1/{__init__.py, schemas.py, endpoints/, routers/}
│   ├── database/session.py
│   ├── models/{task.py, user.py}
│   ├── schemas/task.py                        ← also lives at api/v1/schemas.py
│   └── tests/{test_auth.py, test_tasks.py}
└── frontend/
    ├── Dockerfile, index.html, package.json, tsconfig.json, vite.config.ts
    └── src/
        ├── App.tsx, App.css, index.tsx, main.tsx, index.css, types.ts
        └── components/{TaskForm.tsx, TaskItem.tsx, TaskList.tsx, Toolbar.tsx,
                        TaskList.css, Toolbar.css}
```

## Comparison baseline

This is the **first** run in this series, so there's nothing to diff against.
Future runs should fill the matrix below.

| Date | Model | Cost | Confidence | Δ Structure | Δ Runtime | Δ Functional | Finding resolved? |
|------|-------|-----:|-----------:|------------:|----------:|-------------:|-------------------|
| 2026-04-14 | flash+coder | $0.675 | 43.6 | — | — | — | — |

## Reproduction

```bash
# start stack
docker compose up -d dashboard postgres

# run the skill (via Claude Code)
/orchestrator-learning-path-test
# or with a custom goal:
/orchestrator-learning-path-test "Your topic here"
```

The skill drives the run, performs verification, prints a report in the same
format as above, and appends it as a new dated file in this directory.
