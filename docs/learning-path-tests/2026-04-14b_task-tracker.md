# Learning-Path Test — 2026-04-14 (run b) — Task Tracker full-stack

Second run on the same Task Tracker goal, executed immediately after run **a**
to measure the impact of two changes shipped between the runs:

- **Filesystem confinement fix** — commit `90447e9` (paths remapped under the
  session working directory chroot-style; agent prompts now state their cwd).
- **Generic software-engineering rules injected into the prompts** —
  uncommitted at run time, patch in `src/agent_orchestrator/dashboard/agent_runner.py`:
  - Dependency pins must be loose (no hallucinated exact versions).
  - Smoke-test (`python -c "import main"` / `node --check`) before declaring done.
  - Single source of truth — extend existing layout, never create parallel trees.
  - Finish cleanly — stop instead of looping on shell commands.
  - On step-budget exhaustion, write a `STATUS.md` handoff.
  - Team-lead routing rule: technical docs (README, CHANGELOG, docstrings)
    go to engineering agents, never to `content-strategist`.
  - Don't over-decompose: if one agent can do it, assign to one.

Same goal, same 5 iteration prompts, same model (`qwen/qwen3.5-flash-02-23`)
as run **a** after iter 0, so the comparison is directly meaningful.

## Run metadata

| Key | Value |
|---|---|
| Session | `20260414_194236_8ae186` — single session for all 6 runs |
| Iterations completed | 5 / 5 |
| LLM wall-clock | **13m43s** (vs 20m41s baseline — **−34%**) |
| Total cost | **$0.155** / $2.00 budget (vs **$0.675** baseline — **−77%**) |
| Total tokens | **2,157,005** (vs 7,323,315 baseline — **−71%**) |
| Model | `qwen/qwen3.5-flash-02-23` for all iterations ($0.06/$0.30 per M) |

### Per-iteration breakdown

| # | Topic | Duration | Cost | Tokens | Agents | Max-steps | Notes |
|---|-------|---------:|-----:|-------:|--------|:---------:|-------|
| 0 | initial goal | 136s | $0.038 | 549K | backend, frontend, devops | backend | **no content-strategist** (baseline had 5 agents incl. marketing for README) |
| 1 | schema extension | 110s | $0.021 | 301K | backend | backend | team-lead picked **1 agent** (no over-decomposition) |
| 2 | JWT auth | 176s | $0.039 | 568K | backend, test-runner | backend | backend created `test_import.py` at root — smoke-test rule attempted |
| 3 | frontend filters | 120s | $0.019 | 253K | frontend | — | **clean exit**, 28 steps, under cap |
| 4 | Postgres migration | 168s | $0.027 | 370K | devops, backend, backend | backend | devops clean in 4 steps; a backend hit cap |
| 5 | CI + coverage | 113s | $0.010 | 115K | devops, backend, backend | — | **ALL AGENTS CLEAN**, landed ci.yml + pytest.ini + loose-pin pytest-cov |

## Confidence: **71 / 100** (+27.4 vs baseline)

| Category   | Baseline | LPT #2 | Δ | Evidence |
|------------|---------:|-------:|---:|----------|
| Structure  |   9.33   |  10.00 | +0.67 | **15/15 expected files** present (baseline missed root-level seed_tasks.py — acceptable) |
| Syntax     |  12.00   |  15.00 | +3.00 | 18 Python files, 0 errors (baseline had 23 files clean but TS was penalised) |
| Build      |   5.00   |  18.00 | **+13.00** | `pip install -r backend/requirements.txt` succeeds end-to-end. **All pins loose** (`fastapi>=0.109,<1`, `pydantic>=2.0,<3`, etc.). Zero hallucinated versions — rule #1 worked perfectly |
| Runtime    |   7.00   |  10.00 | +3.00 | App imports after 1 trivial patch (circular `get_db` import between `backend.main` and `backend.api.auth`). Baseline needed 2 patches + a missing dep |
| Functional |   5.00   |   8.00 | +3.00 | `GET /tasks` → 200; `POST /tasks` correctly returns 401 when unauth'd (auth dependency wired). `/auth/signup` 404 — router imported in auth.py but `app.include_router(auth_router)` missing in main.py |
| LLM-judge  |   5.25   |  10.00 | +4.75 | Deliverables match prompts. No pollution. No duplicate layouts. Only 2 real bugs: circular import + missing `include_router` — both one-line fixes. Baseline shipped a structural mess with hallucinated deps |

## What the prompt changes visibly affected

### 1. Dependency pins — **completely solved**

Run a (baseline) shipped this:
```
pydantic==2.5.6     ← does not exist on PyPI
```

Run b shipped this:
```
fastapi>=0.109,<1
uvicorn>=0.27,<1
sqlalchemy>=2.0,<3
pydantic>=2.0,<3
pytest>=7.0,<8
httpx>=0.25,<1
alembic>=1.13,<2
python-jose[cryptography]>=3.3,<4
passlib[bcrypt]>=1.7,<2
psycopg2-binary>=2.9,<3
pytest-cov>=4.0,<5
```

**Build score +13 points purely from this.** `pip install` completes cleanly.

### 2. No content-strategist for technical docs — **solved**

Run a iter 0: team-lead picked `[backend, frontend, devops, test-runner, content-strategist]` (README → marketing agent).
Run b iter 0: team-lead picked `[backend, frontend, devops]`. README delivered by `devops` with a correct technical tone.

### 3. Single source of truth — **solved**

Run a: `backend/src/*` AND `backend/api/v1/*` coexisting. Two main.py. Two sets of migrations.
Run b: one `backend/` tree. One `main.py`. Two migrations (001, 002) in a single folder, no duplicates.

### 4. No host-project pollution — **solved**

Run a iter 0-2: 37 MB written to the host `/Users/.../agent-orchestrator/backend/`.
Run b all 6 iterations: `git status` stayed clean for the duration. Confinement patch + cwd-in-role prompt both required.

### 5. Over-decomposition — **solved**

Run a iter 5 selected 5 agents, 4 idled.
Run b iter 5 selected 3 agents, **all three completed under the step cap**.

### 6. Smoke-test habit — **partial**

Agents did start creating smoke-test files (`test_import.py`, `backend_test_import.py`, `backend/test_import.py` — three times across iters). They wrote the file but did not always execute it with shell_exec before declaring done — so the circular-import bug slipped past iter 2.

**Next prompt iteration should say**: "after creating the smoke test, call shell_exec on `python test_import.py` and FIX any error before you stop".

### 7. `STATUS.md` handoff — **adopted even when not needed**

Agents wrote `STATUS.md` in iter 5 despite finishing cleanly. Low cost
(2 duplicate writes seen in files_created), and actually useful as a breadcrumb.
Keep the rule.

## Findings still present (generic)

1. **Backend agent still hits max_steps** on complex iterations (iter 0, 1, 2, 4).
   Output still lands via tool calls and summary reconstructs it, so score
   isn't tanked — but token usage stays 20-30% higher than necessary. See the
   `too many retries on shell_exec` class of stalls.

2. **No wiring step after file creation**: iter 2's `backend/api/auth.py` defined
   a router but no one added `app.include_router(auth_router)` to `main.py`.
   Classic "created files, didn't connect them" failure. A **wiring-check pass**
   (grep for `APIRouter(...)` and verify each is included in the entry point)
   would catch this.

3. **Smoke test exists but isn't always executed**: see finding #6 above.
   Prompt needs to say "run the smoke test, not just create it".

4. **`=1.13,` empty file** at session root — parsing artifact from a malformed
   `pip install alembic>=1.13,<2` where shell quoting split the argument.
   Cosmetic, but indicates the agent ran pip via shell_exec without proper
   quoting. Prompt could nudge "always quote package specs: `pip install
   "alembic>=1.13,<2"`".

## Improvement proposals (3 concrete)

### P1 — Execute the smoke test, don't just write it

Extend the SE rule #2 in `agent_runner.py:_build_role_for_agent()` to mandate
execution:

```python
"2. Smoke-test before declaring done. Create the test (`smoke_test.py` in "
"your working directory). Then RUN IT via shell_exec: "
"`python smoke_test.py`. If exit code != 0, fix the code and re-run. Do NOT "
"stop until the smoke test returns 0."
```

This is the single change that would have pushed Runtime 10 → 20 in this run.

### P2 — Post-build wiring check in `run_team`

Between the sub-agent fan-out and the summary step, inject one more validation
pass that specifically checks whether every APIRouter / sub-module created is
actually wired to the entry point. Pseudocode:

```python
# in run_team, after the validation round, before summary:
grep_result = shell("grep -rn 'APIRouter(' backend/")
for router_file, router_name in parse_routers(grep_result):
    if router_name not in main_py_text:
        re_assignments.append({
            "agent": "backend",
            "task": f"{router_file} defines router but main.py does not include it"
        })
```

Would have caught the `auth_router` miss in this run.

### P3 — Fix the "too many retries on shell_exec" stall class

The backend agent repeatedly hits max_steps with a consistent signature:
a burst of small `pip install`, `cat`, `ls`, `python -c` calls that go
nowhere. Two possible causes to investigate:

- `allowed_commands` list in `agent_runner.py:126-139` may be too restrictive
  (e.g. `pip` is not in the list — check whether pip invocations are being
  rejected silently).
- The shell tool cache might be returning stale results. Inspect the cache
  middleware with a test that asserts sequential shell calls don't get
  cached as one another.

A fix here would cut token usage a further 20-30% and let the backend
agent actually finish within 15 steps instead of 30.

## Comparison baseline

| Date | Model | Cost | Duration | Confidence | Δ structure | Δ build | Δ runtime | Δ functional | Δ judge | Key finding resolved? |
|------|-------|-----:|---------:|-----------:|------------:|--------:|----------:|-------------:|--------:|-----------------------|
| 2026-04-14 a (baseline) | flash+coder | $0.675 | 20m41s | 43.6 | — | — | — | — | — | — |
| 2026-04-14 b (this run) | flash | **$0.155** | **13m43s** | **71.0** | +0.67 | **+13.0** | +3 | +3 | +4.75 | filesystem-pollution ✅, hallucinated deps ✅, content-strategist routing ✅, duplicate layouts ✅ |

## Reproduction

```bash
docker compose up -d dashboard postgres
# with the two prompt patches + confinement fix shipped:
/orchestrator-learning-path-test "Build a full-stack Task Tracker..."
```

This run should copy into a new dated file following the same template. If
the next run targets a new topic, fill the "Comparison baseline" matrix against
whichever prior topic is most comparable.
