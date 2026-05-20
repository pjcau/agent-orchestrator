---
sidebar_position: 0
title: v1.5 — Workspace Repair Loop
---

# v1.5 P1 — Workspace Repair Loop (Q3 2026, in progress)

A **workspace-level** verify-and-retry pipeline that wraps `run_team()`. Motivated by `docs/learning-path-tests/2026-05-16_task-tracker.md`: a single `psycopg<3` dep typo cascaded through Build → Runtime → Functional and erased 48 points (confidence 32.5/100 vs the 79.01 baseline). A 5-attempt repair loop fed by the failing tool's stderr would have fixed it in one retry.

Distinct from the existing per-skill `verification_middleware` (PR #59) which validates a single `SkillResult` — the two live side by side.

| Layer | Existing | New (v1.5 P1) |
|---|---|---|
| **Skill** (single tool call) | `core/skill.verification_middleware` | — |
| **Workspace** (after a team run) | — | `core/repair_loop.py` |

Design: [`docs/architecture-repair-loop.md`](https://github.com/pjcau/agent-orchestrator/blob/main/docs/architecture-repair-loop.md).

## Phase plan (7 phases)

| Phase | Status | Deliverable | Files | Tests |
|---|---|---|---|---|
| 1 | ✅ Done | Design doc | `docs/architecture-repair-loop.md` | — |
| 2 | ✅ Done | `VerificationGate` + 3 verifiers (Syntax / Encoding / Dependency) | `core/verification_gate.py`, `core/verifiers/{syntax,encoding,dependency}.py` | `tests/test_verification_gate.py`, `tests/test_verifiers.py` |
| 3 | ✅ Done | `RepairLoop` (verify-and-retry harness) | `core/repair_loop.py` | `tests/test_repair_loop.py` |
| 4 | ✅ Done | `FailurePatternRegistry` + bundled YAML | `core/failure_patterns.py`, `core/failure_patterns.yaml` | `tests/test_failure_patterns.py` |
| 5 | ✅ Done | Wiring into `/api/team/run` (opt-in) | `dashboard/agent_runtime_router.py`, `dashboard/events.py`, `orchestrator.yaml.example` | `tests/test_repair_loop_wiring.py` |
| 6 | ✅ Done | Feature maps + roadmap sync | `docs/website/architecture-map.yaml`, `*-map.json`, this file, `sidebars.js` | — |
| 7 | ✅ Done | Learning-path validation run + 3 follow-ups | `docs/learning-path-tests/2026-05-16b_repair-loop.md`; 1st run scored 49/100 (vs 32.5 baseline), exposed gaps closed below | end-to-end |
| 7.1 | ✅ Done | `ImportVerifier` + `missing_dep_in_requirements` pattern | `core/verifiers/imports.py`, `core/failure_patterns.{py,yaml}` | `tests/test_verifiers.py` + `tests/test_failure_patterns.py` |
| 7.2 | ✅ Done | `WorkspaceCoherenceVerifier` | `core/verifiers/coherence.py` | `tests/test_verifiers.py` |
| 7.3 | ✅ Done | Surface `repair: {…}` block in React UI | `frontend/src/api/types.ts`, `frontend/src/hooks/useWebSocket.ts` | `frontend/src/test/teamComplete.test.tsx` |
| 7.4 | ✅ Done | Default verifier chain: 3 → 5 (Syntax + Encoding + Dependency + Import + Coherence) | `dashboard/agent_runtime_router.py`, `orchestrator.yaml.example` | `tests/test_repair_loop_wiring.py::test_build_repair_loop_includes_all_five_verifiers` |
| 7.5 | ✅ Done | Benchmark re-run with the 5-verifier chain (71.2/100, +22.2 vs run (b)) | `docs/learning-path-tests/2026-05-16c_repair-loop-v2.md` | end-to-end |
| 7.6 | ✅ Done | `ImportVerifier` alias-map fix (psycopg2 ↔ psycopg2-binary) | `core/verifiers/imports.py`, `core/failure_patterns.yaml` | regression tests in `tests/test_verifiers.py` |
| 7.7 | ✅ Done | Abstraction-level fix: `RuntimeSmokeVerifier` (ground-truth tier — actual venv + `pip install` + `python -c "import X"`) + post-condition revert guard in `RepairLoop._try_auto_fix` (snapshots + reverts every touched file when failure count strictly increases) | `core/verifiers/runtime_smoke.py`, `core/repair_loop.py`, `core/failure_patterns.py` | `tests/test_verifiers.py` (4) + `tests/test_repair_loop.py` (2) |
| 7.8 | ✅ Done | Re-run weather-portal benchmark (run e): 74.2/100 on iter 0 alone (+25.4 vs (d)); iter 1 hung — exposes cache-miss-per-retry when requirements.txt mutates | `docs/learning-path-tests/2026-05-16e_weather-portal-v2.md` | end-to-end |
| 7.9 | ✅ Done | All 3 findings from (e) closed: `max_wall_s` cap + `aborted_time` status (a), per-verifier `duration_ms` telemetry (b), smoke verifier canonical-set cache + strict-subset delta install via `cp -a --reflink=auto` (c) | `core/repair_loop.py`, `core/verification_gate.py`, `core/verifiers/runtime_smoke.py` | `tests/test_repair_loop.py` +2, `tests/test_verification_gate.py` +1, `tests/test_verifiers.py` +3 |
| 7.10 | ✅ Done | Benchmark re-run (f): **82.0/100** new all-time high. 6/6 iters; iter-1 (e) hang gone; 1 auto-fix triggered. | `docs/learning-path-tests/2026-05-16f_weather-portal-v3.md` | end-to-end |
| 7.11 | ✅ Done | (a) `EntrypointVerifier` (launches Dockerfile CMD / compose `command:` with health probe) + (b) `E2ESmokeVerifier` (opt-in headless Playwright). Closes the 2026-05-16(g) failure class. Chain: 6 → 8. | `core/verifiers/entrypoint.py`, `core/verifiers/e2e_smoke.py` | `tests/test_verifiers.py` +6 |
| 7.12 | ⏳ Pending | Re-run three.js-space-app benchmark with Entrypoint + E2E active | `docs/learning-path-tests/2026-05-XX_space-threejs-v2.md` | end-to-end |

## TL;DR architecture

```
team_run() → VerificationGate
              │  SyntaxVerifier   (~1s)   py_compile + json.loads
              │  EncodingVerifier (~1s)   literal-\n heuristic
              │  DependencyVerifier (~5s) pip dry-run / known-bad pins
              ▼
       VerificationReport
              │  failed?
              ▼
   FailurePatternRegistry  ← YAML registry (no LLM call)
              │  any failure auto-resolved?  → re-verify
              │  still failing?
              ▼
        RepairLoop.retry(task + top-3 failures + history)
              │  max_attempts=5, max_cost_usd=0.50
              │  signature memory → escalate when same failure recurs
              ▼
   passed | partial | aborted_cost | aborted_budget
```

## Configuration

**ON by default** since Phase 7. Opt out by setting `REPAIR_LOOP_ENABLED=false`.

| Env var | Default | Purpose |
|---|---|---|
| `REPAIR_LOOP_ENABLED` | `true` | Master switch — set to `false` to opt out |
| `REPAIR_LOOP_MAX_ATTEMPTS` | `5` | Hard cap on team-run invocations |
| `REPAIR_LOOP_MAX_COST_USD` | `0.50` | Hard cumulative cost cap |

YAML mirror in `orchestrator.yaml.example`:

```yaml
repair_loop:
  enabled: true
  max_attempts: 5
  max_cost_usd: 0.50
  patterns: core/failure_patterns.yaml
  verifiers: [syntax, encoding, dependency]
```

## Expected impact on the 2026-05-16 baseline

| Category | Before | After (estimate) | Why |
|---|---:|---:|---|
| Structure | 10.0 | 10.0 | unchanged |
| Syntax | 13.5 | 15.0 | `package.json` literal-`\n` repaired by `EncodingVerifier` + `unicode_unescape` pattern |
| Build | 0.0 | 20.0 | `psycopg<3` auto-fixed by `pip_pin_repair` pattern → `psycopg2-binary>=2.9` |
| Runtime | 0.0 | ~15.0 | depends on app booting after the fix |
| Functional | 0.0 | ~15.0 | endpoints reachable after runtime |
| LLM-judge | 9.0 | ~10.0 | small bump from a cleaner repo |
| **Total** | **32.5** | **~85** | **+~52** |

Validated end-to-end in Phase 7.

## Event stream

New `EventType` values surfaced to the dashboard WebSocket:

- `verification.started` / `verifier.started` / `verifier.finished` / `verification.finished`
- `repair.started` / `repair.attempt_started` / `repair.attempt_finished`
- `repair.escalated` (same signature recurred → more file context in next prompt)
- `repair.auto_fixed` (a deterministic pattern resolved the failure — zero LLM cost)
- `repair.aborted` (cost or attempt cap hit)
- `repair.finished` (terminal — carries `status` + cumulative cost)

## Related work in this repo

- `core/skill.verification_middleware` — per-skill output validation (PR #59). Complementary, not redundant.
- `core/resilience.RetryPolicy` — per-LLM-call retry with backoff. The RepairLoop is the same idea **one level up** (per-team-run).
- `core/loop_detection.py` — anti-loop within a single agent run. Orthogonal: the RepairLoop adds anti-loop across team runs via `signature_set` comparison.
- `core/tool_recovery.recover_dangling_tool_calls` — already invoked by one of the bundled patterns.
- `docs/phase2.md` — the older skill-level verification gate; explicitly differentiated above.
