# Architecture — Workspace Repair Loop

> **Status**: Design doc (Phase 1 of 7). No code yet.
> **Source motivation**: `docs/learning-path-tests/2026-05-16_task-tracker.md` (confidence 32.5/100, vs 79.01 baseline). A single `psycopg<3` dep typo cascaded through Build → Runtime → Functional and erased 48 points. A 5-attempt repair loop fed by the failing tool's stderr would have fixed it in one retry.

This document specifies a **workspace-level** verify-and-repair pipeline that wraps `run_team()`. It is distinct from the existing **skill-level** `verification_middleware` (which validates a single `SkillResult`); the two live side by side and address orthogonal concerns.

| Layer | Existing | New (this doc) |
|---|---|---|
| **Skill** (single tool call) | `core/skill.verification_middleware` (PR #59) | — |
| **Workspace** (after a team run) | — | `core/repair_loop.py` (this doc) |

---

## TL;DR

After every `run_team()`, run a chain of cheap-to-expensive verifiers on the produced workspace:

```
SyntaxVerifier (1s) → DependencyVerifier (5s) → EncodingVerifier (1s) → BuildVerifier (30s) → SmokeTestVerifier (10s)
```

If any verifier fails, the **RepairLoop** re-invokes the same team with a structured failure context (tool, exit code, top-3 errors, past attempts) up to **K = 5** times. Known failure signatures (e.g. *"Could not find a version that satisfies X"*) are short-circuited via the **FailurePatternRegistry** without burning an LLM call.

**Expected effect on the 2026-05-16 baseline run**: 32.5 → ~80 / 100 (recovers Build + Runtime + Functional + part of Syntax).

---

## Goals

1. **Close the loop**. `team_run` today reports `success: true` even when the produced repo cannot `pip install`. Stop that.
2. **Pluggable verifiers**. Adding a new check is a single class + one entry in a list. No fork of `team_run`.
3. **Bounded cost**. Retries respect a hard `max_attempts` (default 5) and a hard `max_cost_usd` (default $0.50). Aborts surface in the dashboard.
4. **Determinism where possible**. Failure signatures that match a registry pattern get a deterministic fix (no LLM call). Only novel failures fall back to the LLM.
5. **Opt-in rollout**. Default OFF. Enable via `REPAIR_LOOP_ENABLED=true` for a sprint, watch metrics, flip default after a green run.

## Non-goals

- **Not** a replacement for the existing `verification_middleware`. That validates one skill's output before it's used downstream — typically a single function call. The Repair Loop validates the whole filesystem state at the end of a multi-step team run.
- **Not** a code-execution sandbox. The verifiers shell out to `python -m py_compile`, `pip install --dry-run`, `docker compose build`, etc. The sandboxing story stays in `core/sandbox.py`.
- **Not** a substitute for thoughtful prompts. If the team-lead asks for a feature that breaks the previous iteration, no amount of retry will fix it — the Edit-in-place guard (proposal #1 from the 2026-05-16 report) handles that.

---

## Layer 1 — VerificationGate (Phase 2)

```
┌─────────────────────────────────────────────────────────┐
│  team_run() produces files in workdir/                  │
└──────────────────────────┬──────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────┐
│  WorkspaceVerifier (abstract)                           │
│   def verify(workdir: Path) -> VerificationReport       │
└──────────────────────────┬──────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┬────────────────┐
        ▼                  ▼                  ▼                ▼
  SyntaxVerifier   DependencyVerifier   EncodingVerifier   BuildVerifier
  (~1s)            (~5s, dry-run)       (~1s)              (~30s)
```

### Signatures

```python
# core/verification_gate.py
@dataclass(frozen=True)
class VerifierFailure:
    verifier: str            # "syntax", "deps", "encoding", ...
    severity: str            # "error" | "warning"
    category: str            # "py_syntax", "pypi_resolve", "json_escape", ...
    message: str             # one-line human summary
    detail: str              # full stderr / tool output, capped at 4096 chars
    file: str | None         # path relative to workdir, if known
    exit_code: int | None
    signature: str           # sha256(category + normalized_message) — for dedup

@dataclass(frozen=True)
class VerificationReport:
    passed: bool
    failures: tuple[VerifierFailure, ...]
    duration_ms: int

    def signature_set(self) -> frozenset[str]:
        return frozenset(f.signature for f in self.failures)

class WorkspaceVerifier(Protocol):
    name: str
    cost_estimate_s: float   # used for ordering — cheaper runs first
    async def verify(self, workdir: Path) -> VerificationReport: ...

class VerificationGate:
    def __init__(self, verifiers: list[WorkspaceVerifier], *, fail_fast: bool = True): ...
    async def verify(self, workdir: Path) -> VerificationReport: ...
```

### Bundled verifiers

| Verifier | What it runs | Fails when | Catches the 2026-05-16 issue |
|---|---|---|---|
| `SyntaxVerifier` | `py_compile`, `json.tool`, `tsc --noEmit` (best-effort, skip if no `tsc`) | parse error in any `*.py`, `*.json`, `*.ts`, `*.tsx` | `frontend/package.json` literal-`\n` |
| `DependencyVerifier` | `pip install --dry-run -r requirements*.txt`, `npm ls --package-lock-only` | unresolvable pin, name not on registry | `psycopg<3` (would mark `pypi_resolve`) |
| `EncodingVerifier` | scans text files for `\\n\\s*"` literal sequences without real newlines in same 200-char window | matches the heuristic | `frontend/package.json` again (defence-in-depth) |
| `BuildVerifier` | `docker compose build --pull=false` if `docker-compose.yml` exists | non-zero exit | catches everything `DependencyVerifier` missed |
| `SmokeTestVerifier` | `pytest --collect-only -q` if `pytest.ini`/`pyproject.toml` configured | collection error (import error counts) | the iter-5 `from main import SessionLocal` (undefined import) |

Ordering: cheapest first, **fail-fast** by default so a syntax error short-circuits the 30 s `docker compose build`.

### Events

```python
EventType.VERIFICATION_STARTED      # workdir, verifier_count
EventType.VERIFIER_STARTED           # name
EventType.VERIFIER_FINISHED          # name, passed, duration_ms, failure_count
EventType.VERIFICATION_FINISHED      # passed, total_duration_ms, signatures
```

### Metrics

```
workspace_verification_total{verifier, result}
workspace_verification_duration_seconds{verifier} (histogram)
workspace_verification_failures_total{category}
```

---

## Layer 2 — RepairLoop (Phase 3)

```
┌────────────────────────────────────────────┐
│  RepairLoop.run(task, max_attempts=5)      │
└──────────────────┬─────────────────────────┘
                   │
                   ▼
   ┌──────────────────────────────────┐
   │  attempt = 1                     │
   └──────────────────────────────────┘
                   │
   ┌───────────────┼──────────────────────────────┐
   ▼               ▼                              ▼
  team_run    VerificationGate.verify         signature in
  (task)      → VerificationReport             past_signatures?
                                                │
                                          ┌─────┴─────┐
                                          │           │
                                       same       new
                                          │           │
                                          ▼           ▼
                              escalate_strategy  augment_task
                              (more context     (failure ctx,
                               or sub-agent      attempt, top-3
                               swap)             errors)
                                          │           │
                                          └────┬──────┘
                                               ▼
                                      attempt += 1, loop
```

### Signature

```python
# core/repair_loop.py
@dataclass
class RepairAttempt:
    attempt: int
    task: str
    workdir: Path
    report: VerificationReport
    cost_usd: float
    duration_s: float

@dataclass
class RepairResult:
    final_workdir: Path
    final_report: VerificationReport
    attempts: list[RepairAttempt]
    status: Literal["passed", "partial", "aborted_budget", "aborted_cost"]

class RepairLoop:
    def __init__(
        self,
        *,
        team_runner: Callable[..., Awaitable[TeamResult]],
        gate: VerificationGate,
        pattern_registry: FailurePatternRegistry | None = None,
        max_attempts: int = 5,
        max_cost_usd: float = 0.50,
        event_bus: EventBus | None = None,
    ): ...

    async def run(self, task: str, **team_kwargs) -> RepairResult: ...
```

### `augment_task` — feeding the LLM a useful retry prompt

```python
def augment_task(
    original: str,
    failures: list[VerifierFailure],   # top-3 only
    attempt: int,
    past_signatures: frozenset[str],   # avoid retrying same fix
    history: list[str],                 # past attempt summaries
) -> str:
    return f"""{original}

────────────────────────────────────────────
REPAIR ATTEMPT {attempt}/5 — previous output failed verification:

{format_failures(failures)}

Past attempts that did NOT resolve the issue:
{format_history(history)}

Do NOT make any change unrelated to the failures above. Address them in
the order listed. If a failure persists after this turn, the loop will
escalate.
"""
```

Critical: only the **top-3 failures** per attempt are forwarded; otherwise the prompt drifts the agent into unrelated fixes.

### Escalation when the same signature repeats

If `report.signature_set()` ⊆ `past_signatures`, the agent is going in circles. Two escalation strategies, picked in order:

1. **Add more context** — include the first 200 lines of the offending file in the prompt, not just the error.
2. **Sub-agent swap** — if `category` ∈ `{pypi_resolve, npm_resolve, docker_build}` and the current team has a `devops` agent that has not been called, force-route the next attempt to it.

### Cost guard

After every attempt, `cumulative_cost = sum(a.cost_usd for a in attempts)`. If `cumulative_cost > max_cost_usd`, the loop aborts with status `aborted_cost`. Same for wall-clock via the optional `max_duration_s`.

### Events

```
EventType.REPAIR_STARTED          # task, max_attempts, max_cost_usd
EventType.REPAIR_ATTEMPT_STARTED  # attempt, signatures_seen
EventType.REPAIR_ATTEMPT_FINISHED # attempt, passed, cost_delta_usd, signature_set
EventType.REPAIR_ESCALATED        # attempt, strategy ("more_context" | "agent_swap")
EventType.REPAIR_FINISHED         # status, attempts_used, cumulative_cost
```

### Metrics

```
repair_attempts_total{outcome}         # passed / partial / aborted_budget / aborted_cost
repair_attempts_per_run (histogram)
repair_cost_usd_per_run (histogram)
repair_signature_repeat_total          # how often same failure persisted across attempts
```

---

## Layer 3 — FailurePatternRegistry (Phase 4)

Deterministic short-circuit for known failure categories. **No LLM call**. Each pattern owns a regex over the failure message + an `auto_fix` action.

### Format

```yaml
# core/failure_patterns.yaml
- name: pypi_unresolvable_pin
  category: pypi_resolve
  pattern: "Could not find a version that satisfies the requirement (?P<pkg>\\S+)"
  auto_fix:
    type: pip_pin_repair
    action: lookup_pypi_releases   # hits https://pypi.org/pypi/{pkg}/json
    suggest_format: "{pkg}{closest_pin}"
  llm_required: false

- name: json_escape_corruption
  category: json_escape
  pattern: "Expecting property name enclosed in double quotes.*line 1"
  auto_fix:
    type: text_transform
    action: unicode_unescape_then_rewrite
    target: "{failure.file}"
  llm_required: false

- name: dangling_tool_call
  category: dangling_tool
  pattern: "tool_call without result"
  auto_fix:
    type: call
    target: core.tool_recovery.recover_dangling_tool_calls
  llm_required: false
```

### API

```python
# core/failure_patterns.py
class FailurePatternRegistry:
    @classmethod
    def from_yaml(cls, path: Path) -> "FailurePatternRegistry": ...

    def match(self, failure: VerifierFailure) -> FailurePattern | None: ...

    async def apply(
        self,
        failure: VerifierFailure,
        workdir: Path,
    ) -> RepairAction | None: ...

@dataclass
class RepairAction:
    kind: Literal["file_rewrite", "deps_update", "noop"]
    file: str | None
    new_content: str | None
    explanation: str          # surfaces in the event log
```

The `RepairLoop` calls `registry.apply(failure, workdir)` **before** invoking the team:

```python
for f in report.failures[:3]:
    action = await registry.apply(f, workdir)
    if action:
        emit("repair.auto_fixed", {...})
        resolved.add(f.signature)
remaining = [f for f in report.failures if f.signature not in resolved]
if not remaining:
    # All failures auto-fixed without an LLM call — go back to verify.
    continue
```

### Bootstrapping the registry

Patterns are seeded from past `docs/learning-path-tests/*.md` reports. Each new run can append patterns it discovered.

---

## Wiring into `team_run` (Phase 5)

`src/agent_orchestrator/dashboard/agent_runtime_router.py::team_run`:

```python
async def team_run(body: dict, request: Request):
    ...
    if os.environ.get("REPAIR_LOOP_ENABLED", "").lower() == "true":
        repair_loop = request.app.state.repair_loop
        async def _run_in_background():
            result = await repair_loop.run(
                task_desc,
                provider=provider, event_bus=bus, working_directory=...,
                ...
            )
            # result.status surfaces in the existing job_logger payload
    else:
        async def _run_in_background():
            result = await run_team(...)   # current behaviour, unchanged
```

`request.app.state.repair_loop` is constructed in `dashboard/app.py` startup once, reading optional config from `orchestrator.yaml`:

```yaml
repair_loop:
  enabled: ${REPAIR_LOOP_ENABLED:-false}
  max_attempts: 5
  max_cost_usd: 0.50
  patterns: core/failure_patterns.yaml
  verifiers:
    - syntax
    - dependency
    - encoding
    - build
```

---

## Mapping to the 2026-05-16 failure modes

| Failure (from 2026-05-16 report) | Caught by | Repair path |
|---|---|---|
| `psycopg<3` invalid pin | `DependencyVerifier` (`pip install --dry-run` exit ≠ 0) | Pattern `pypi_unresolvable_pin` → `lookup_pypi_releases("psycopg")` → suggests `psycopg2-binary` → file rewrite, no LLM call |
| `frontend/package.json` literal-`\n` | `SyntaxVerifier` (`json.tool`) **and** `EncodingVerifier` | Pattern `json_escape_corruption` → `unicode_unescape` rewrite, no LLM call |
| `from main import SessionLocal` undefined (iter 5 test file) | `SmokeTestVerifier` (`pytest --collect-only`) | Falls through to LLM repair (no static pattern); top-3 failure includes the ImportError → agent fixes the import |
| `bare export default` at end of `App.tsx` | `SyntaxVerifier` if `tsc` available, else falls through | LLM repair if `tsc` missing |
| File overwrite regression (5 iterations) | Out of scope — handled by proposal #1 (`EditInPlaceGuard`) in the filesystem skill, **not** by this design. The two complement each other. |

Expected score lift on the 2026-05-16 baseline:

| Category | Before | After (estimate) | Why |
|---|---:|---:|---|
| Structure | 10.0 | 10.0 | unchanged |
| Syntax | 13.5 | 15.0 | `package.json` repaired |
| Build | 0.0 | 20.0 | `psycopg` pin auto-fixed |
| Runtime | 0.0 | ~15.0 | depends on app actually booting after fix |
| Functional | 0.0 | ~15.0 | endpoints reachable after runtime |
| LLM-judge | 9.0 | ~10.0 | small bump from cleaner repo |
| **Total** | **32.5** | **~85** | **+~52** |

The +52 estimate matches the "if the dep typo were fixed" projection in the original report.

---

## Phase plan

| Phase | Owner | Deliverable | Files touched | Test types |
|---|---|---|---|---|
| 1 (this doc) | architect | Design doc | `docs/architecture-repair-loop.md` | — |
| 2 | backend | `VerificationGate` + 3 verifiers | `core/verification_gate.py`, `core/verifiers/*` | unit + integration |
| 3 | backend | `RepairLoop` | `core/repair_loop.py` | unit (mocked gate + team) |
| 4 | backend | `FailurePatternRegistry` | `core/failure_patterns.{py,yaml}` | unit per pattern |
| 5 | backend | wire into `team_run` | `dashboard/agent_runtime_router.py`, `dashboard/app.py`, `orchestrator.yaml.example` | integration via `/api/team/run` |
| 6 | architect | feature maps + roadmap sync | `docs/website/architecture-map.yaml`, `*-map.json`, `docs/roadmap.md`, `docs/unified-roadmap.md`, `docs/website/docs/roadmap/v150-repair-loop.md`, `sidebars.js`, `analysis/ROADMAP.md` | — |
| 7 | (this skill) | `/orchestrator-learning-path-test` validation run with `REPAIR_LOOP_ENABLED=true` | `docs/learning-path-tests/2026-05-XX_repair-loop.md` | end-to-end |

**Mandatory per CLAUDE.md** at every code-bearing phase (2–5): tests + docs/abstractions.md update + (if shipping changes any feature) feature maps regenerated. Phase 6 is the explicit map+roadmap sync.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Verifiers slow down every `team_run` | Cheap-first ordering, fail-fast, `cost_estimate_s` budget per verifier; gate is opt-in via env var until baseline shows median overhead < 15 s |
| `pip install --dry-run` hits the network on every run | Cache the resolver result by `(requirements.txt hash, python version)` for 24 h |
| `docker compose build` is expensive (~30 s) and fails on missing Dockerfiles | Skip `BuildVerifier` if no Dockerfile present; only run on iterations that touched build-related files (detected via diff vs prior state) |
| LLM repair turns into infinite stall on broken signatures | `max_attempts=5` AND `max_cost_usd=0.50` AND `signature repeat ≥ 2` triggers escalation OR `partial` exit |
| Pattern registry rules grow stale | Each pattern carries a `last_validated_at`; CI fails if any pattern is older than 90 days without an explicit "still valid" stamp |
| Conflict with existing per-skill `verification_middleware` | Different scope (skill vs workspace), different names, both run. No code overlap. |

## Open questions (to revisit in Phase 5)

1. **Should the RepairLoop emit a new `EventType.REPAIR_*` family**, or reuse `EventType.TEAM_*` with a `repair: true` flag? — Leaning toward dedicated events for dashboard clarity.
2. **Where does `lookup_pypi_releases` make the HTTP call?** — Synchronously inside the pattern, or as a registered skill the team can use? Sync inside is simpler but couples the registry to outbound HTTP. Reuse `web_reader` skill is purer but slower.
3. **Sub-agent swap** on escalation — does the framework expose enough introspection on team membership for this to be safe? May defer to Phase 6 if not.

---

## Related work in this repo

- `core/skill.verification_middleware` — per-skill output validation (PR #59). Complementary, not redundant.
- `core/resilience.RetryPolicy` — per-LLM-call retry with backoff. The RepairLoop is the same idea **one level up** (per-team-run).
- `core/loop_detection.py` — anti-loop for tool calls within a single agent run. Orthogonal: the RepairLoop adds anti-loop across team runs (via `signature_set` comparison).
- `core/tool_recovery.recover_dangling_tool_calls` — already invoked by Pattern #3 above.
- `docs/phase2.md` — the older skill-level verification gate; explicitly differentiated above.
