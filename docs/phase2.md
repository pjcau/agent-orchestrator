# Phase 2 — Middleware, decomposition guards, namespace hierarchy, sandbox metrics

Four additional research-scout proposals landed as Phase 2, each with
tests and meaningful metrics.

## Verification Gate Middleware (PR #59)

Drop a quality-gate into the skill middleware chain. When a skill emits
a result, the matching validator decides pass/fail; failure converts the
result into an error so downstream code sees a clear rejection.

```python
from agent_orchestrator.core.skill import verification_middleware

def review_ok(result):
    if len(result.output) < 50:
        return (False, "review shorter than 50 chars")
    return True

registry.use(verification_middleware({"code_review": review_ok}, metrics=reg))
```

Metrics (per skill):
- `verification_total` — lookups passed through a validator
- `verification_pass_total`
- `verification_fail_total`
- `verification_duration_seconds` — histogram

**Pass rate** (pass/total) tells you whether your validators are useful
or drowning in false positives. **p95 duration** guards against slow
validators pushing latency.

## Atomic Task Decomposition Validator (PR #59)

`validate_atomic_tasks(assignments)` lints team-lead plans for tasks
that:
- Exceed `max_chars` (default 800) — too long to be atomic
- Have more than `max_imperatives` (default 5) distinct action verbs
- Contain more than `max_conjunctions` (default 1) sequencing phrases
  like "and then", "and also"

Wired into `dashboard/agent_runner.run_team`: after the team-lead
decomposes, any issues are emitted on the event bus as an `agent.step`
event (no hard gate). Counter `tasks_rejected_too_complex_total` is
populated when a MetricsRegistry is supplied.

## Context Loader Middleware (PR #61)

Injects the concatenated content of all `*.md` files under a directory
into `request.metadata[metadata_key]` before skill execution. Safer than
mutating params because unknown params break LLM tool schemas.

```python
registry.use(context_loader_middleware(
    "references/",
    target_skills={"code_review"},
    metadata_key="context",
    max_bytes=50_000,
    metrics=reg,
))
```

Metrics (per skill): `context_files_loaded_total`,
`context_bytes_injected`.

## Hierarchical Namespaces (PR #81)

The store already supported tuple namespaces; this phase adds
ergonomic helpers:

```python
from agent_orchestrator.core.store import (
    path_to_namespace,
    namespace_to_path,
    descends_from,
    namespace_depth,
)

ns = path_to_namespace("project.alice.tasks")   # ("project", "alice", "tasks")
namespace_to_path(ns)                           # "project.alice.tasks"
descends_from(("project","alice","x"), ("project",))  # True
namespace_depth(ns)                             # 3
```

`BaseStore` gains path-based convenience methods:
- `aget_path(path, key)`
- `aput_path(path, key, value, ttl=None)`
- `asearch_path(path_prefix, ...)` — prefix-scoped across any depth

## Raw Verbatim Checkpoint Log (PR #81)

`Checkpoint` now has an optional `raw_log: str | None` field.
Both `InMemoryCheckpointer` and `SQLiteCheckpointer` persist and
restore it. The Postgres checkpointer's table gains a `raw_log TEXT`
column (added via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` so the
migration is automatic). This lets callers store a verbatim transcript
alongside the compacted state for fidelity/debugging without affecting
the primary state column.

## Sandbox Live CPU/Memory (follow-up)

`Sandbox.get_stats()` queries `docker stats --no-stream --format "{{json .}}"`
and parses CPU%, mem bytes/limit/percent, net rx/tx. Returns zeros when
docker is unavailable — never raises.

New endpoint: `GET /api/sandbox/{session}/stats`.

New frontend widget in the Sandbox **Status** tab: two sparklines
(CPU and memory percent, 30 samples at 3s interval) plus live
memory and network counters. Renders only while the container is
running and the session has an active sandbox.
