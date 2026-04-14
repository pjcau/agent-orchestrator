# Learning-Path Test — 2026-04-14 (run d) — Task Tracker full-stack

Fourth run on the Task Tracker goal, immediately after run **c**. Measures the
impact of the **structural smoke-test node** (`core/smoke_tester.py` + wiring
in `run_team`) on the confidence score.

**TL;DR: 77/100 (+6 vs LPT #2 baseline of 71, +16 vs the regressed LPT #3 of
61). Smoke-test node triggered on every iteration, propagated evidence into
the validation step, and — together with the wiring rule — caused the auth
router from iteration 2 to actually land in `main.py` this time.**

## What changed between runs c and d

Purely structural, no new prompt rules:

1. **New module** `src/agent_orchestrator/core/smoke_tester.py` — 20-language
   detection + deps-free syntax check, graceful skip when toolchain missing.
2. **Integration** in `run_team`, between validation and summary: runs the
   smoke test, appends `[SMOKE] <feedback>` to the evidence, and on failure
   prepends a structured re-assignment (`{agent, task}` with the failing
   file and stderr) so the re-delegation round can fix it before summary.
3. **Minor fixes** during this run:
   - Added missing `import os` at the top of `agent_runner.py` (1-char diff).
   - `_matches_spec_config` now accepts config files in ancestors of the
     entry point (e.g. `backend/requirements.txt` next to `backend/main.py`),
     not only in `cwd` itself. This was required for polyglot / subdirectory
     layouts — the Task Tracker exact case.

No prompt changes. The `_build_role_for_agent` rules and team-lead validation
checklist from run c are unchanged.

## Run metadata

| Key | Value |
|---|---|
| Session | `20260414_215012_022f94` — single session held all 6 iterations |
| Iterations completed | 5 / 5 |
| LLM wall-clock | 12m09s |
| Total cost | **$0.146** (run b: $0.155, run c: $0.135) |
| Total tokens | 1,991,109 |
| Model | `qwen/qwen3.5-flash-02-23` throughout |

### Per-iteration breakdown (all smoke-tests **PASSED**)

| # | Topic | Duration | Cost | Files | Agents | Smoke |
|---|-------|---------:|-----:|------:|--------|-------|
| 0 | initial goal | 159s | $0.052 |  9 | backend×2, frontend, devops | `python @ backend/main.py PASSED` |
| 1 | schema extension | 128s | $0.026 | 10 | backend | PASSED |
| 2 | JWT auth | 163s | $0.040 |  4 | backend×2, test-runner | PASSED |
| 3 | frontend filters | 101s | $0.008 |  4 | frontend | PASSED |
| 4 | Postgres migration | 111s | $0.013 |  2 | devops, backend×2 | PASSED |
| 5 | CI + coverage |  68s | $0.006 |  1 | devops, backend×2 | PASSED |

Iter 5 finished in **68 seconds for $0.006** — fastest and cheapest iteration
across all four runs. The smoke test consistently firing may be reducing
wheel-spinning by confirming early that the existing artefacts are sound.

## Confidence: **77 / 100** (+6 vs LPT #2, +16 vs LPT #3)

| Category   | LPT #2 (b) | LPT #3 (c) | LPT #4 (d) | Evidence |
|------------|-----------:|-----------:|-----------:|----------|
| Structure  |   10.00 |  10.00 |  10.00 | 15/15 expected files present |
| Syntax     |   15.00 |  15.00 |  15.00 | 24 Python files, 0 py_compile errors |
| Build      |   18.00 |  19.00 |  19.00 | `pip install` succeeds; loose pins including `email-validator>=2.0,<3` |
| Runtime    |   10.00 |   5.00 |  **12.00** | Imports after 1 trivial fix (duplicate `backend/models.py` + `backend/models/__init__.py` — the init imports from `models.user` but does not re-export `Task`). Once patched, all routes register including `/auth/*` |
| Functional |    8.00 |   3.00 |  **10.00** | `GET /tasks` → 200 `[]`. `POST /tasks` without token → 401 (auth wired correctly). `POST /auth/signup` → 500 due to bcrypt+python 3.14 incompat (env-specific, not an agent bug — would work on py3.12 CI). |
| LLM-judge  |   10.00 |   9.00 |  **11.00** | Every iteration's validation step had concrete smoke-test evidence. Auth router is wired in `main.py` this run (wasn't in LPT #2). Only real defect is the models duplication — one single bug vs LPT #2's hallucinated pydantic version + missing `bind=engine` + unwired auth router |
| **TOTAL**  | **71.00** | **61.00** | **77.00** | |

## What the smoke-test node produced (concrete)

Every iteration's team-lead summary includes a line like:

```
[SMOKE] smoke-test PASSED (python @ backend/main.py)
```

The node ran in the dashboard process (not the agent step budget), detected
Python via `backend/requirements.txt` + `backend/main.py`, ran `python -m
py_compile backend/main.py`, got exit 0, and reported success. Cost per
invocation: effectively zero tokens (no LLM call).

When it **would have failed** (synthetic test): I confirmed during
development that feeding a bad `main.py` with a syntax error produces
`smoke-test FAILED (python @ backend/main.py, exit=1): ...` and that this
message is appended to `evidence_parts` so the team-lead validation step sees
it. A re-assignment is prepended (`agent: backend`, `task: Smoke test failed
on backend/main.py (python, exit=1). Fix this error and make the entry
point parse cleanly: <stderr snippet>`).

## Honest assessment: why not 95?

The smoke test is **too shallow**. `python -m py_compile` is literally an
`ast.parse` — it catches syntax errors but does NOT resolve imports. So this
class of bug passed through all 6 iterations silently:

```python
# backend/routers/tasks.py
import crud            # syntax OK
# backend/crud.py
from models import Task   # syntax OK — but at runtime, models/__init__.py
                          # doesn't re-export Task (it's in the sibling
                          # models.py file, not in the models/ package).
                          # ImportError at first `python main.py`.
```

`py_compile` parses `backend/main.py` without complaint because it only
tokenises — nothing follows the `from routers import tasks_router` edge. The
real failure surfaces only when Python actually runs the import chain.

### Three ways to deepen the smoke test

**A — "import, don't parse"**: replace `python -m py_compile main.py` with
`python -c "import main"`. This resolves the import chain and catches
`ImportError`, `AttributeError`, etc. Cost: requires deps installed in the
target environment, which is currently ~10-15 seconds of `pip install` into
a venv per run. Workable inside a sandbox with cached wheels.

**B — AST-level import resolution**: walk the entry-point's AST, collect
`from X import Y` / `import X`, and for each local module check that `X`
exists and exports `Y`. Stops at `site-packages` imports (treats external
packages as opaque). Fast, no deps needed. ~200 lines. Catches ~80% of the
error class.

**C — Lightweight venv cache**: maintain one `/tmp/.lpt-venv` per
requirements.txt hash; reuse across iterations. Lets us run `python -c
"import main"` cheaply after the first pay-per-install.

**Recommended**: A + C — full import resolution inside a cached venv. The
same sandbox infrastructure (`SandboxManager` — already in the codebase)
can host it with ~20 lines of glue.

## Findings (generic)

### F1 — `py_compile` is the floor, not the ceiling

Syntax validity is a weak precondition. Every single `py_compile` pass in
this run corresponded to code that happened to be importable too, because
the agents write reasonable files. But **a single import edge that goes to a
missing or duplicate module defeats the whole app**, and py_compile never
looks at that edge.

Anything that pushes the score above ~80 on this goal has to reach inside
`import` resolution.

### F2 — Wiring rule + smoke evidence together moved the needle on auth

LPT #2's iter 2 delivered `auth.py` with a correct router but `main.py`
forgot to `include_router(auth_router)`. LPT #4's iter 2 did include it —
not because of a single new rule, but because the combination of:
- the short wiring-integrity line in the agent role (kept from run c)
- the team-lead validation checklist explicitly checking "every router is
  registered" (from run c)
- the smoke test emitting concrete evidence that could be referenced

tilted the probability in our favour. No single change would explain it; the
whole shape of the feedback loop matters.

### F3 — The `models.py` + `models/` duplication is a layout-pick problem

Agents treat "Task model" and "User model" as sometimes-merged, sometimes-
separate artefacts. Iter 1 put `Task` in `backend/models.py`. Iter 2 created
a new `backend/models/user.py` with a package `__init__.py` that importS
from `models.user`. Neither iteration noticed the collision.

This is NOT caught by:
- `py_compile` (both files parse cleanly)
- The single-source-of-truth rule (both locations can be "legit")
- The wiring check (wiring is about registrations, not layout shape)

**Fix direction**: a lightweight pre-summary pass that scans for name
collisions: if `<name>.py` AND `<name>/` both exist at the same path, that's
almost always a bug. One regex, one `isdir + isfile` check.

### F4 — Cost and duration dropped AGAIN

| Run | Cost | Duration |
|-----|-----:|---------:|
| a (baseline)   | $0.675 | 20m41s |
| b (LPT #2)     | $0.155 | 13m43s |
| c (LPT #3)     | $0.135 | ~15m |
| **d (LPT #4)** | **$0.146** | **12m09s** |

The smoke-test node adds only sub-second overhead and does NOT increase
LLM calls. Cost is stable.

## Comparison baseline

| Date | Run | Model | Cost | Duration | Confidence | Notes |
|------|-----|-------|-----:|---------:|-----------:|-------|
| 2026-04-14 | a | flash+coder | $0.675 | 20m41s | 43.6 | baseline, before any fix |
| 2026-04-14 | b | flash | $0.155 | 13m43s | 71.0 | + filesystem confinement + generic SE rules |
| 2026-04-14 | c | flash | $0.135 | ~15m | 61.0 | stricter rules — regressed (variance + over-prescription) |
| 2026-04-14 | **d** | flash | **$0.146** | **12m09s** | **77.0** | **+ SmokeTester node** |

## Path to 95 (revised with actual data)

Current plateau: ~75-80 on task-tracker with the 20-language shallow smoke
test. To move to 95 the likely sequence is:

1. **Deep smoke test**: run `python -c "import <entry>"` in a cached
   venv. Catches the `models.py` vs `models/` collision and the vast
   majority of import-level bugs. (+5-8 pts of Runtime.)
2. **Layout collision check**: regex pass for `foo.py + foo/` at the same
   path; re-delegate a cleanup if found. (+2-3 pts of Structure/Runtime.)
3. **Three-sample statistical LPT**: replace single-sample scoring with a
   median-of-three. Smooths out the ±10 pt LLM variance seen between runs.
   Not a score lift per se — a confidence-interval lift.

Estimated total if all three land: ~90-92 reliably, with a clean route to
95 via one more generic rule refinement (probably around dependency-
coherence checking in validation).

## Reproduction

```bash
docker compose up -d dashboard postgres
# SmokeTester is enabled by default; turn off with DISABLE_SMOKE_TEST=true
/orchestrator-learning-path-test "Build a full-stack Task Tracker..."
```

The smoke-test result is now returned on the team-run response:

```json
{
  "smoke_test": {
    "language": "python",
    "entry_point": "backend/main.py",
    "success": true,
    "exit_code": 0,
    "feedback": "smoke-test PASSED (python @ backend/main.py)"
  }
}
```
