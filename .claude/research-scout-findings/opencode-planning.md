# Research Scout Findings: opencode-planning-toolkit

**Source:** https://github.com/IgorWarzocha/opencode-planning-toolkit
**Author:** IgorWarzocha
**Language:** TypeScript (100%)
**License:** MIT
**Stars:** 72 | **Forks:** 7
**Last updated:** 2026-03-08
**Scouted:** 2026-03-08

---

## 1. What Does It Do?

The opencode-planning-toolkit is an OpenCode plugin that gives AI agents persistent,
repo-scoped task planning across sessions. It addresses a fundamental limitation of
session-scoped agents: state is lost between restarts, and parallel subagents have no
shared view of what work is in progress.

The plugin exposes **five tools** that agents call during their workflow:

| Tool | Purpose |
|------|---------|
| `create_spec` | Write a reusable requirement/standard document (`docs/specs/*.md`) |
| `create_plan` | Write a new work plan with at least 5 implementation steps (`docs/plans/*.md`) |
| `append_spec` | Link an existing spec into a plan's `SPECS_START/SPECS_END` block |
| `read_plan` | Read a plan and inline all linked spec content for agent context |
| `mark_plan_done` | Transition plan `status: active` to `status: done` in frontmatter |

Everything is stored as plain markdown files inside the project repository. No database,
no daemon, no network call — persistence comes from the files being committed to git.

---

## 2. Cross-Session Persistence: How It Works

### Storage location

```
your-project/
└── docs/
    ├── specs/      # *.md  — reusable standards, scope: repo | feature
    └── plans/      # *.md  — active work plans, status: active | done
```

Files live inside the project directory and are written with `Bun.write`. Because they
are ordinary files on disk (and typically tracked in git), they survive process restarts,
machine reboots, and agent re-launches with zero special infrastructure.

### Session-start protocol

Every agent is instructed (via the bundled `plans-and-specs` skill and the injected
`<available_plans>` system-prompt block) to **call `read_plan` before doing major work**.
This means the agent re-hydrates its context from disk at the start of each session
rather than relying on in-memory state.

### Concurrent-agent awareness

When parallel subagents start, each reads the same `docs/plans/` directory. The plan
frontmatter exposes `plan status: active | done`, and the implementation step list shows
which items are in progress. Agents use this to avoid stepping on each other's work.
The `shared.ts` `writeWithMerge` function retries up to 5 times with verify-after-write
to handle concurrent spec appends safely.

---

## 3. File Formats

### Plan file (`docs/plans/{name}.md`)

```markdown
---
plan name: my-feature
plan description: Add login flow
plan status: active
---

## Idea
Detailed description of the goal.

## Implementation
- Step 1: ...
- Step 2: ...
- Step 3: ...
- Step 4: ...
- Step 5: ...

## Required Specs
<!-- SPECS_START -->
- api-conventions
- auth-standards
<!-- SPECS_END -->
```

**Rules enforced by the plugin:**

- Name: `[A-Za-z0-9-]`, max 3 hyphen-separated words.
- Description: 3–10 words, must not overlap with the name.
- Steps: minimum 5 items.
- Status: `active` or `done` (no in-progress at step granularity — that is handled
  by the agent reading the step list and tracking mentally or via notes).
- `<!-- SPECS_START -->` / `<!-- SPECS_END -->` HTML comments delimit the linked-spec
  block. `append_spec` merges into this block with concurrent-write retry logic.

When `read_plan` is called the plugin inlines the full content of every linked spec
under an `## Associated Specs` section, so the agent receives one self-contained
context document.

### Spec file (`docs/specs/{name}.md`)

```markdown
# Spec: api-conventions

Scope: repo

Content here (free-form markdown). Describes standards that apply to
all plans (Scope: repo) or a single plan (Scope: feature).
```

Repo-scope specs are automatically detected and offered for linking whenever a new plan
is created.

---

## 4. Relevance to Agent Orchestrator

### What we have today

| Component | Description | Gap |
|-----------|-------------|-----|
| `core/task_queue.py` | In-memory priority queue (`QueuedTask`, status: pending / running / completed / failed) | Lost on restart; no agent narrative context |
| `core/checkpoint.py` | `InMemoryCheckpointer`, `SQLiteCheckpointer` — snapshot graph state (`thread_id`, `node_id`, metadata) | Graph state, not human-readable task plans |
| `core/checkpoint_postgres.py` | Async Postgres checkpointer for graph state | Same scope as SQLite checkpointer |
| `dashboard/usage_db.py` | PostgreSQL usage stats (token counts, costs) | Operational metrics, not task planning |

### The gap this toolkit addresses

**"What was agent X working on last session?"** — currently unanswerable.

When the orchestrator restarts (or when a new agent session begins) there is no
mechanism to tell an agent:
- What high-level plan it was executing.
- Which implementation steps are done vs. pending.
- What architectural constraints (specs) apply to the current plan.
- Whether another agent is currently handling an overlapping task.

The toolkit fills exactly this gap with file-backed, human-readable, git-trackable plans.

### Concrete scenarios where this helps

1. **Agent restart recovery** — backend agent can read `docs/plans/add-oauth.md` and
   resume from the correct step rather than starting over or asking the user.
2. **Multi-agent coordination** — frontend and backend agents share a plan file; each
   reads `plan status` and the implementation list before starting, reducing duplicate
   work and merge conflicts.
3. **Spec reuse** — a `api-contracts` repo-scope spec is linked into every plan that
   touches the API surface, ensuring agents follow the same conventions across sessions.
4. **Audit trail** — plans committed to git provide a lightweight history of what was
   planned vs. what changed in the code.

---

## 5. Proposed Integration

### Design principle

Rather than porting the TypeScript plugin verbatim, the same file-format convention can
be adopted as a thin Python layer that wraps the existing `TaskQueue` and `Checkpointer`
abstractions. No new dependencies are required; markdown files are the interface.

### Suggested new module: `core/plan_store.py`

```
src/agent_orchestrator/core/
└── plan_store.py          # NEW: persistent plan/spec store (markdown files)
```

Responsibilities:
- `PlanStore.create_plan(name, description, idea, steps)` — write `docs/plans/{name}.md`.
- `PlanStore.read_plan(name)` — return plan content with inlined specs.
- `PlanStore.mark_done(name)` — update `plan status: done` in frontmatter.
- `PlanStore.create_spec(name, scope, content)` — write `docs/specs/{name}.md`.
- `PlanStore.append_spec(plan_name, spec_name)` — merge spec into `SPECS_START` block
  (with retry logic matching the original implementation).
- `PlanStore.list_active()` — list all plans with `plan status: active`.

### Integration with `TaskQueue`

`QueuedTask` already has a `context: dict[str, Any]` field. The bridge is a two-field
extension:

```python
@dataclass
class QueuedTask:
    ...
    plan_name: str | None = None    # links to docs/plans/{plan_name}.md
    spec_names: list[str] = field(default_factory=list)  # linked specs
```

When `TaskQueue.enqueue` is called with a `plan_name`, `PlanStore.create_plan` is
invoked automatically (if the plan does not already exist). When `TaskQueue.complete`
is called, `PlanStore.mark_done` is invoked if all steps are done.

### Integration with `Checkpointer`

Checkpointers capture low-level graph state (`node_id`, `state` dict). Plans operate at
a higher abstraction level (human-readable task intent). They are complementary:

```
Checkpoint (graph state, per-node)  ──▶  what computation was the agent doing?
Plan (markdown, per-feature)        ──▶  why was the agent doing it?
```

No changes to `Checkpoint` or `Checkpointer` are needed. `PlanStore` is a separate
concern that agents consult at session-start via `agent_runner.py`.

### Integration with `agent_runner.py`

At the start of `run_agent` / `run_team`, the runner queries `PlanStore.list_active()`
and prepends the list to the agent's system prompt:

```python
active_plans = plan_store.list_active()
if active_plans:
    plan_summary = "\n".join(f"- {p.name}: {p.description}" for p in active_plans)
    system_prompt = f"<active_plans>\n{plan_summary}\n</active_plans>\n\n" + system_prompt
```

This mirrors the `<available_plans>` injection the original plugin performs via its
system hook.

### Architecture diagram

```
Session N                          Session N+1
──────────────────────────────     ──────────────────────────────
AgentRunner                        AgentRunner
  │                                  │
  ├─ TaskQueue (in-memory)           ├─ TaskQueue (in-memory, empty)
  │   └─ QueuedTask(plan_name="X")  │
  │                                  ├─ PlanStore.list_active()
  ├─ PlanStore                       │   └─ reads docs/plans/X.md  ◀── git-tracked file
  │   └─ create_plan("X", ...)       │
  │   └─ append_spec("X", "api")     └─ system_prompt += <active_plans>
  │                                        agent resumes from correct step
  └─ (restart / crash)
       docs/plans/X.md survives   ──────────────────────────────────────
```

### What would need to change

| File | Change |
|------|--------|
| `core/plan_store.py` | New module (described above) |
| `core/task_queue.py` | Add optional `plan_name`, `spec_names` fields to `QueuedTask` |
| `dashboard/agent_runner.py` | Inject `<active_plans>` at session start |
| `tests/test_plan_store.py` | New test module (unit + integration) |
| `docs/architecture.md` | Document `PlanStore` abstraction |
| `CLAUDE.md` | Add `plan_store.py` to the project structure table |

No changes needed to `checkpoint.py`, `checkpoint_postgres.py`, or `usage_db.py`.

---

## 6. Evaluation

| Criterion | Score | Notes |
|-----------|-------|-------|
| **Applicable** | 0.9 | Directly addresses the missing cross-session task narrative. The gap is real and documented in our codebase (task_queue.py docstring: "in-memory now, Postgres-ready interface for later"). |
| **Quality** | 0.8 | Clean TypeScript, good separation of concerns, concurrent write safety, path traversal protection, well-commented source. Minor concern: no step-level status (active/done per step) — only whole-plan status. |
| **Compatible** | 0.85 | File format is plain markdown. Python re-implementation is straightforward. No external dependencies beyond file I/O. Integrates cleanly with existing `QueuedTask.context` field. |
| **Safe** | 0.95 | Files are written inside the project directory, path traversal is guarded (`getSecurePath`), no network calls, no daemon process, MIT license. The only risk is write conflicts under heavy parallelism — mitigated by the retry+verify pattern. |

**Overall recommendation: Integrate.** The pattern is simple, safe, and fills a genuine
gap. A Python `PlanStore` wrapping the same markdown convention can be built in ~200 LOC
and adds meaningful value to multi-agent sessions without touching any critical existing
code paths.
