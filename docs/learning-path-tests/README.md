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
| 2026-04-14 | [task-tracker](2026-04-14_task-tracker.md) | Task Tracker full-stack | qwen3-coder-next (iter 0) + qwen3.5-flash-02-23 (iter 1-5) | $0.675 | 43.6 / 100 |
