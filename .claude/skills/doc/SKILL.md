---
name: doc
description: Review production code and update all documentation under docs/ to match the current codebase state.
disable-model-invocation: true
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent
user-invocable: true
---

# Doc — Full Documentation Sync

Review the entire production codebase and bring all docs under `docs/`, `docs/website/docs/`, `CLAUDE.md`, and `README.md` in sync with the actual code.

**Optional argument**: a hint string suggesting which area to focus on (e.g., "dashboard API", "graph engine", "deployment"). If provided, prioritize that area but still do a full pass.

**STOP on any failure** — do not continue if a phase fails.

---

## Phase 1: Build Source-of-Truth Map

Read the actual codebase to understand what exists right now:

1. Read `CLAUDE.md` (the canonical project reference)
2. Scan `src/agent_orchestrator/core/` — list all modules, public classes, and key functions
3. Scan `src/agent_orchestrator/dashboard/` — list endpoints, WebSocket handlers, event types
4. Scan `src/agent_orchestrator/providers/` — list all providers
5. Scan `src/agent_orchestrator/skills/` — list all skills
6. Scan `docker/`, `docker-compose*.yml` — list all services and their config
7. Scan `terraform/` — list all modules
8. Scan `.claude/agents/` — list all agents by category
9. Scan `.claude/skills/` — list all skills
10. Scan `tests/` — count test files and total test classes

Build a mental map of: modules, abstractions, APIs, services, agents, skills, and their relationships.

If the user provided a **focus hint**, note which docs are most relevant to that area.

---

## Phase 2: Audit Each Doc File

For each documentation file, compare its content against the source-of-truth map. Check for:

### Staleness (content that was true but isn't anymore)
- Modules/classes that were renamed, moved, or deleted
- APIs/endpoints that changed signatures or behavior
- Docker services that were added/removed
- Agent counts or names that changed
- Configuration options that were added/removed

### Gaps (features that exist in code but aren't documented)
- New modules with no docs
- New API endpoints not mentioned anywhere
- New event types, skills, or abstractions
- New deployment steps or infrastructure changes

### Inaccuracies (content that contradicts the code)
- Wrong file paths or module locations
- Incorrect command examples
- Outdated sequence diagrams or architecture descriptions
- Wrong agent-to-category mappings

### Scope of docs to audit

**Always audit** (these are the primary targets):

| File | What to check |
|------|--------------|
| `CLAUDE.md` | Project structure, modules, abstractions, agents, skills, dashboard sections |
| `README.md` | Overview, quick start, features list |
| `docs/architecture.md` | Core abstractions, patterns, module relationships |
| `docs/components.md` | Dashboard components, API endpoints, sequence diagrams |
| `docs/deployment.md` | Docker services, deploy steps, environment variables |
| `docs/security.md` | Auth, RBAC, network, secrets |
| `docs/cache-strategy.md` | Cache integration points, implementation status |
| `docs/roadmap.md` | Completed vs planned features |

**Also audit** (website mirror — should match `docs/`):

| File | Mirror of |
|------|-----------|
| `docs/website/docs/architecture/overview.md` | `docs/architecture.md` |
| `docs/website/docs/architecture/components.md` | `docs/components.md` |
| `docs/website/docs/architecture/agents.md` | Agent structure |
| `docs/website/docs/architecture/skills.md` | Skills list |
| `docs/website/docs/architecture/providers.md` | Provider list |
| `docs/website/docs/architecture/graph-engine.md` | Graph/StateGraph docs |

If the user provided a **focus hint**, audit those docs first and in more detail.

---

## Phase 3: Report Findings

Before making any changes, produce a **DOC AUDIT REPORT**:

```
DOC AUDIT REPORT
================
Focus: <user hint or "full review">
Files audited: <count>

STALE (needs update):
  - <file>: <what's wrong>
  - ...

GAPS (missing documentation):
  - <topic>: should be documented in <file>
  - ...

INACCURATE (contradicts code):
  - <file>: <what's wrong>
  - ...

UP TO DATE:
  - <file>: OK
  - ...
```

Use the Agent tool with `subagent_type=Explore` for deep codebase exploration when needed.

---

## Phase 4: Apply Fixes

For each finding from Phase 3:

1. **Update the doc file** to match the actual code
2. Keep the existing structure and style of each doc
3. Do NOT add speculative/planned features — only document what exists
4. Do NOT rewrite entire files — make surgical edits
5. If a doc has a website mirror (`docs/website/docs/`), update both

### Rules:
- Keep prose concise and factual
- Code examples must be runnable (verify paths, commands, imports)
- Sequence diagrams must match actual API flow
- Agent/skill counts must match `.claude/agents/` and `.claude/skills/`
- Module lists must match `src/agent_orchestrator/core/`
- Docker services must match `docker-compose*.yml`

---

## Phase 5: Verify

After all edits:

1. Run a quick sanity check — grep for any remaining obviously wrong counts or paths:

```bash
# Verify agent count in CLAUDE.md matches actual agents
find .claude/agents -name "*.md" -not -path "*/team-lead.md" | wc -l
grep -c "agents" CLAUDE.md || true
```

2. Check that no docs reference files that don't exist:

```bash
# Spot-check: extract file paths from docs and verify they exist
grep -ohP 'src/agent_orchestrator/\S+\.py' docs/*.md CLAUDE.md | sort -u | while read f; do
  [ ! -f "$f" ] && echo "MISSING: $f"
done
```

3. If any issues found, fix them.

---

## Phase 6: Commit & Push

1. Run `git status` and `git diff --stat`
2. Stage all changed doc files
3. Commit:

```bash
git commit -m "$(cat <<'EOF'
docs: sync documentation with current codebase

<summary of what was updated>

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

4. Push:

```bash
git push
```

---

## Output Format

```
DOC SYNC REPORT
===============
Focus:         <user hint or "full review">
Files Audited: <count>
Files Updated: <count>
Files OK:      <count>

Changes:
  - <file>: <what was fixed>
  - ...

Commit: <hash> <message>
Push:   [OK]
```
