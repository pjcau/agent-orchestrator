# Learning-Path Test — 2026-04-14 (run c) — Task Tracker full-stack

Third run on the same Task Tracker goal, immediately after run **b**, to test
whether stricter generic prompt rules push the score past 71 toward 95.

**TL;DR: the third run scored 61/100 — lower than run b's 71. Pure prompt
engineering has hit a plateau; pushing harder on prompts makes things worse.
The next point gain must come from structural changes in the orchestrator
(actual smoke-test execution inside `run_team`), not from more LLM instructions.**

## What changed between run b and run c

Three generic rule additions were tried, and two of them had to be rolled back
mid-run because they actively regressed output:

1. **G1 — "EXECUTE the smoke test, not just write it"** (added, rolled back).
   Required agents to run `python -c "import main"` via shell_exec with exit
   code 0 before declaring done. Problem: the import requires deps installed,
   and `pip install` is NOT in `allowed_commands`. Agents burned their step
   budget retrying `pip install fastapi` (silently rejected) and **iter 0
   produced 0 code files** — only `STATUS.md` and `smoke_test.py`.

2. **G2 — "use `py_compile` / `node --check` (deps-free) as smoke test"**
   (added, rolled back). Softer version of G1 that doesn't require deps.
   Problem: agents interpreted the elaborate rule block as "your job is to
   validate, not to build", and **still produced fewer code files than run b**.
   Iter 0 produced 6 files including STATUS.md reports explaining why they
   couldn't smoke-test.

3. **G3 — "wiring integrity: verify every router/blueprint/command you define
   is registered in the entry point"** (added, kept in final). Short, concrete
   rule. No visible regression. Small visible effect: agents split code into
   `backend/routers/` more often.

4. **G4 — team-lead validation checklist expansion** (added, kept in final).
   Adds four explicit checks (wiring, deps coherence, smoke-test evidence,
   single layout). Didn't visibly hurt or help — the validation step isn't
   where most aberrations get caught.

Final shipped prompt state after both rollbacks: the run-b rule set, plus one
line about wiring integrity and the extended team-lead validation checklist.

## Run metadata

| Key | Value |
|---|---|
| Sessions | split across 2 dashboard restarts: `20260414_202107_2fc7bc` (iter 0) + `20260414_202740_629bc7` (iter 1-5) |
| Iterations completed | 5 / 5 |
| LLM wall-clock | ~15 min |
| Total cost | **$0.135** (run b: $0.155, run a: $0.675) |
| Total tokens | ~2.5M |
| Model | `qwen/qwen3.5-flash-02-23` throughout |

### Per-iteration breakdown

| # | Topic | Duration | Cost | Files | Agents | Notes |
|---|-------|---------:|-----:|------:|--------|-------|
| 0 | initial goal | 218s | $0.048 | 17 | backend×3, frontend, devops | wiring-check visible: `backend/routers/tasks.py` split out |
| 1 | schema extension | 124s | $0.018 | 11 | backend | clean completion |
| 2 | JWT auth (prompt explicitly reminds `include_router`) | 112s | $0.014 | 10 | backend | single agent, finished in-budget |
| 3 | frontend filters | 97s | $0.012 | 7 | frontend | clean |
| 4 | Postgres migration | 193s | $0.029 | **1** | devops, backend×4 | **over-decomposed**: 5 agents, only 1 file produced |
| 5 | CI + coverage | 145s | $0.014 | 3 | devops, backend | clean |

## Confidence: **61 / 100** (run b was 71 / 100)

| Category   | LPT #2 (b) | LPT #3 (c) | Δ | Evidence |
|------------|-----------:|-----------:|---:|----------|
| Structure  | 10.00 | 10.00 |  0   | 15/15 expected files present (same as b) |
| Syntax     | 15.00 | 15.00 |  0   | 23 Python files, 0 py_compile errors |
| Build      | 18.00 | 19.00 | +1   | pip install passes; `email-validator` now correctly listed (iter 2 prompt mentioned it) |
| Runtime    | 10.00 |  5.00 | **−5** | import fails — mixed `from backend.X` (main.py) and `from X` (routers/tasks.py) in the same tree. Not caught by wiring-check (wiring is about registrations, not import-path consistency) |
| Functional |  8.00 |  3.00 | **−5** | can't start the app → can't probe endpoints |
| LLM-judge  | 10.00 |  9.00 | −1   | same structural quality, one extra inconsistency category |

## Why it went down — analysis

The raw delta is −10 points. Breaking down:

- **+1 on Build**: the iter-2 prompt reminder "also add `email-validator` to requirements.txt" landed. That's a prompt-level improvement, but it's topic-specific, not a generic rule.
- **−10 on Runtime+Functional**: *a different* class of aberration showed up this time — inconsistent import paths between `main.py` (uses `from backend.X`) and `routers/tasks.py` (uses `from X`). The agent running iter 0 happened to make a different choice than in run b. This is not caused by the rules; it's pure LLM variance.
- Net result: roughly the same overall quality as run b, different failure mode, slightly worse score because of where the failure landed.

**Interpretation**: run-to-run variance at this model/task difficulty is **at
least ±10 points** on the 6-category score. To claim a prompt change moves
the mean, we'd need several runs per prompt version. A single LPT is not a
reliable signal.

## Findings to keep from this run

Despite the score regression, three generic observations are worth recording:

### F1 — Over-prescription collapses agents

The early attempt with 8 numbered rules and "DO NOT STOP until X" language
caused agents to produce *validation theatre* (STATUS.md reports, smoke-test
scripts) instead of code. Less is more in role prompts.

**Keep in mind**: every added constraint shifts agent attention away from
"write code". The dose-response curve bends down fast.

### F2 — Import-path consistency is a separate aberration class

Wiring-check catches `APIRouter` defined but not included. It does NOT catch
`from backend.X` in one file and `from X` in a sibling file. These are
different bugs. A full package-coherence check would need:
- pick one style (package-relative or absolute)
- grep every file for `^from ` / `^import `
- assert they match the chosen style and the package is importable from the
  repo root

This cannot be done reliably by an agent running inside its step budget.
It needs to be a post-step pass in `run_team`.

### F3 — Over-decomposition hasn't been fully solved

Iter 4 selected 5 backend agents for the Postgres migration. Only 1 file
came out. The "don't over-decompose" rule is in the team-lead plan prompt,
but the model still decomposed because the task has 6 numbered points.

**Fix direction**: when the plan has ≥4 assignments, the system could
either (a) auto-collapse duplicates by agent name or (b) re-ask the plan
step with "reduce to ≤3 assignments".

## Path to 95 — structural changes required

Prompt engineering alone plateaus around **70 ± 10** on this goal. To push
toward 95, we need **runtime-time structural help**, not more rules:

### S1 — Smoke-test execution inside `run_team` (post-agents, pre-summary)

**Where:** `src/agent_orchestrator/dashboard/agent_runner.py`, after the
validation step and before the summary step.

**What:** invoke a smoke-test node that:
1. Locates entry-point files (`main.py`, `app.py`, `index.ts`, `package.json`'s `main`).
2. In a sandbox venv (spawn via SandboxManager), runs `pip install -r
   requirements.txt --dry-run` to catch hallucinated versions.
3. Runs `python -c "import <entry_module>"` to catch circular imports and
   missing deps.
4. Any failure is fed back as a **structured error** into an automatic
   re-delegation round ("File X has import error Y on line Z, fix it").

Estimated effect: Runtime 10 → 18, Functional 8 → 16. **Alone, this gets
us ~85/100.**

### S2 — Repeat LPTs to measure statistical movement

Single-sample LPTs are noisy. A "measured" improvement needs at least 3
runs per prompt version to detect a 10-point shift above noise.

### S3 — Investigate shell_exec retry stalls (orthogonal, token cost)

The backend agent still hits max_steps on 30-40% of runs. The "Stalled:
too many retries on shell_exec" class of error was seen again. Likely
causes: `pip`/`npm`/`uvicorn`/`alembic` not in allowed_commands → agent
retries → stalls. A fix would be to widen `allowed_commands` to include
`pip --dry-run`, `alembic`, or to suppress retries faster in the cache
middleware.

This doesn't improve score directly but cuts ~25% off token cost per run.

## Comparison baseline

| Date | Run | Model | Cost | Confidence | Change from prev |
|------|-----|-------|-----:|-----------:|------------------|
| 2026-04-14 | a | flash+coder | $0.675 | 43.6 | — |
| 2026-04-14 | b | flash | $0.155 | 71.0 | +27.4 (filesystem confinement + generic SE rules) |
| 2026-04-14 | c | flash | $0.135 | **61.0** | **−10.0** (stricter rules → over-prescription regression + run variance) |

## Recommendation

**Do NOT push more rules into the prompts.** We are past the point of
diminishing returns; marginal rule additions have ~15-point downside variance.

Instead:
1. Revert any speculative rule expansions; keep the leaner run-b ruleset.
2. Implement **S1** (smoke-test node inside `run_team`) — single structural
   change, testable, landable behind a feature flag.
3. After S1, re-run LPT three times on the same goal, report median
   confidence. Only then consider further prompt adjustments.

## Reproduction

Same as the earlier runs. The prompt state at the time of this run is in
`src/agent_orchestrator/dashboard/agent_runner.py` — specifically the
`_build_role_for_agent()` function (with one extra wiring line vs run b)
and the team-lead validation system prompt (with the expanded checklist).
