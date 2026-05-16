# Orchestrator Learning-Path Tests

End-to-end runs of the orchestrator on realistic multi-turn projects, used
to measure how the system behaves under accumulated context, budget pressure,
and a mix of agent categories. Runs are driven by the
`/orchestrator-learning-path-test` skill
(`.claude/skills/orchestrator-learning-path-test/SKILL.md`).

Each run has its own dated file in this directory. Use them to:

- spot regressions between orchestrator versions
- compare models (cost vs quality vs completion rate)
- track the effect of fixes on the confidence score over time

## How to add a new run

1. Launch `/orchestrator-learning-path-test [optional topic]`.
2. When the report prints, copy its content into a new file:
   `docs/learning-path-tests/YYYY-MM-DD_<short-slug>.md`.
3. Fill in the **Before / After** section if the run followed a fix.
4. Commit both the log and any code patches it produced.

## Score format

Confidence is out of 100, broken down into 6 categories:

| Category   | Weight | What it measures |
|------------|-------:|------------------|
| Structure  | 10 | files the goal + iterations required are present |
| Syntax     | 15 | Python/TS parse without errors |
| Build      | 20 | `pip install` / `npm ci` / `docker compose build` succeed |
| Runtime    | 20 | app boots, health endpoint responds, DB+seed reachable |
| Functional | 20 | CRUD endpoints return expected responses |
| LLM-judge  | 15 | Opus rates adherence to goal + iteration prompts |

## Runs

| Date | File | Topic | Model(s) | Cost | Confidence |
|------|------|-------|----------|------|------------|
| 2026-04-14 | [task-tracker (a)](2026-04-14_task-tracker.md) | Task Tracker full-stack | qwen3-coder-next (iter 0) + qwen3.5-flash-02-23 (iter 1-5) | $0.675 | 43.6 / 100 |
| 2026-04-14 | [task-tracker (b)](2026-04-14b_task-tracker.md) | Same goal, after filesystem-confinement fix + generic SE prompt rules | qwen3.5-flash-02-23 | **$0.155** | **71.0 / 100** (+27.4) |
| 2026-04-14 | [task-tracker (c)](2026-04-14c_task-tracker.md) | Same goal, stricter rules attempt (wiring-check + validation checklist); shows the prompt-engineering plateau | qwen3.5-flash-02-23 | $0.135 | **61.0 / 100** (−10.0, variance + over-prescription) |
| 2026-04-14 | [task-tracker (d)](2026-04-14d_task-tracker.md) | Same goal, with structural 20-language SmokeTester node in run_team | qwen3.5-flash-02-23 | $0.146 | **77.0 / 100** (+6.0 vs run b) |
| 2026-04-21 | [task-tracker (e)](2026-04-21_task-tracker.md) | Same goal, after Phase 1/2/3 merges (prompt registry + markers + compaction metrics + verification gate + atomic-task lint + hierarchical namespaces + modality detection + hybrid preload). Surfaced silent-success failure mode: iters 3/4/5 reported success but wrote zero files. | qwen3.5-flash-02-23 | $0.277 | **79.01 / 100** (+2.01 vs d) |
| 2026-05-16 | [task-tracker](2026-05-16_task-tracker.md) | Same goal, post-v1.3.0 sprint. Surfaced 3 new failure modes: file-overwrite regression every iteration, zip-download endpoint skips subdirs, `\n` literally written into source files. Single dep-name typo (`psycopg<3`) cascaded Build→Runtime→Functional to 0. | gpt-4o-mini requested → 67 % gpt-4o-mini + 33 % qwen3.5-flash (router auto-redirected backend-dev / frontend-dev) | $0.037 | **32.5 / 100** (−46.5 vs e) |
| 2026-05-16 (b) | [2026-05-16b_repair-loop.md](2026-05-16b_repair-loop.md) | Same goal, v1.5 P1 Phase 7: repair loop ON by default. All 6 iters passed verifiers on first attempt with zero auto-fixes; runtime still failed because `models.py`/`crud.py` declare imports for `passlib` + `python-jose` that were never added to `requirements.txt` — a class of failure no current verifier catches. What-if score with the 2 deps patched: 72 / 100. ZIP endpoint sub-dir bug also surfaced and fixed inline. | qwen3.5-flash-02-23 | $0.299 | **49.0 / 100** (+16.5 vs prior run; +39.5 with dep gap patched) |
| 2026-05-16 (c) | [2026-05-16c_repair-loop-v2.md](2026-05-16c_repair-loop-v2.md) | Same goal, v1.5 P1 Phase 7.5: bundled chain extended to 5 verifiers (Syntax + Encoding + Dependency + Import + Coherence). Iter 3+4 timed out at driver poll cap so the score reflects 3/5 iterations — but with deps cleanly declared by qwen this time, runtime jumped 0 → 20/20. New verifiers did not fire (no failure mode triggered); validation of `requirements_append` auto-fix end-to-end still pending. | qwen3.5-flash-02-23 | $0.124 | **71.2 / 100** (+22.2 vs run (b); +38.7 vs baseline) |
