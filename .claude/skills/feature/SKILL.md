---
name: feature
description: End-to-end feature development — implement, iterate with user feedback, test, SOLID review, document, commit & push. Use when building a new feature or making a significant code change that needs quality assurance.
disable-model-invocation: true
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent, AskUserQuestion
user-invocable: true
argument-hint: <feature description>
---

# Feature — Full Feature Development Lifecycle

Build a feature from description to production-ready code in one command.

**Flow**: Implement → User Review Loop → Tests → SOLID & Performance Review → Documentation → Commit & Push

**STOP on any failure** — do not continue to the next phase if the current one fails.

## Critical Rules

1. **NEVER skip the user approval loop** — always ask the user before moving to tests
2. **NEVER push code that fails tests or lint**
3. **All code, comments, and docs MUST be in English**
4. **Use the project's existing patterns** — read similar code before writing new code

---

## Phase 1: Understand & Plan

Before writing any code:

1. Read the feature description provided as argument
2. Identify which files need to be created or modified
3. Read existing related files to understand current patterns and conventions
4. Present a brief plan (3-5 bullet points) of what you'll implement

Do NOT ask for approval of the plan — just show it and start implementing.

---

## Phase 2: Implement

Write the feature code:

1. Follow existing code patterns in the project
2. Keep changes minimal and focused — only what's needed for the feature
3. Prefer editing existing files over creating new ones
4. Do NOT write tests yet (that's Phase 4)
5. Do NOT write documentation yet (that's Phase 6)

---

## Phase 3: User Review Loop

**This is the core feedback loop. Iterate until the user is satisfied.**

After implementing, ask the user for feedback using `AskUserQuestion`:

- Show a summary of what was implemented (files changed, key decisions)
- Ask: "Does this implementation meet your requirements?"
- Options: "Approved — proceed to tests", "Needs changes", "Start over"

### If "Needs changes":
1. Ask what needs to change (or read the user's notes)
2. Make the requested changes
3. Show updated summary
4. Ask again — **repeat until approved**

### If "Start over":
1. Revert changes (`git checkout -- <modified files>`, remove new files)
2. Ask the user what was wrong with the approach
3. Go back to Phase 1 with the new understanding

### If "Approved":
Continue to Phase 4.

**Max iterations**: 5. If after 5 rounds the user is still not satisfied, STOP and suggest breaking the feature into smaller pieces.

---

## Phase 4: Tests

Write and run tests for the new feature:

1. Create or update test file(s) in `tests/` matching the source file structure
2. Cover:
   - Happy path (main functionality works)
   - Edge cases (empty input, boundary values)
   - Error cases (invalid input, expected failures)
3. Run the test suite:

```bash
source .venv/bin/activate && pytest --tb=short -q
```

4. If tests fail, fix them and re-run
5. All tests (existing + new) MUST pass before continuing

**STOP if tests cannot be made to pass after 3 attempts.**

---

## Phase 5: SOLID & Performance Review

Review the implemented code against SOLID principles and performance best practices.

### 5a. SOLID Principles Check

For each modified/created file, verify:

| Principle | Check | Action if Violated |
|-----------|-------|-------------------|
| **S** — Single Responsibility | Does each class/function do exactly one thing? | Split into focused units |
| **O** — Open/Closed | Can behavior be extended without modifying existing code? | Extract interfaces/base classes |
| **L** — Liskov Substitution | Can subclasses replace parents without breaking? | Fix inheritance contracts |
| **I** — Interface Segregation | Are interfaces small and focused? | Split fat interfaces |
| **D** — Dependency Inversion | Do high-level modules depend on abstractions? | Inject dependencies |

### 5b. Performance Check

Review for common performance issues:

- **N+1 queries**: loops with individual DB/API calls → batch them
- **Unnecessary copies**: large data structures copied in loops → use references/generators
- **Missing async**: blocking I/O in async context → use async versions
- **Redundant computation**: same calculation repeated → cache or compute once
- **Memory leaks**: growing collections never cleared → add cleanup/limits

### 5c. Apply Fixes

If any SOLID or performance issues are found:

1. Fix them
2. Re-run tests to make sure fixes don't break anything:

```bash
source .venv/bin/activate && pytest --tb=short -q
```

3. If tests fail after refactoring, revert the problematic refactor and keep the working version

---

## Phase 6: Lint & Format

```bash
source .venv/bin/activate && ruff check src/ tests/
source .venv/bin/activate && ruff format --check src/ tests/
```

If lint or format fails, fix automatically:

```bash
source .venv/bin/activate && ruff check --fix src/ tests/
source .venv/bin/activate && ruff format src/ tests/
```

Re-run checks to confirm. If unfixable, STOP.

---

## Phase 7: Documentation

Update documentation to reflect the new feature:

1. **Code comments**: Add docstrings to new public functions/classes (only if logic isn't self-evident)
2. **CLAUDE.md**: Update if the feature adds new modules, abstractions, skills, or changes architecture
3. **docs/**: **ALWAYS evaluate** which doc files under `docs/` need updating. Review each file in `docs/` and update any that are affected by the feature (architecture, deployment, security, API, cost, migration, infrastructure). This step is mandatory — never skip it.
4. **README.md**: Update if the feature changes setup, usage, or project overview

Only update docs that are actually affected. Do NOT touch docs for unrelated sections. But you MUST review `docs/` every time.

---

## Phase 8: Commit & Push

1. Run `git status` and `git diff --stat` to review all changes
2. Stage all relevant files (NOT .env or credentials):

```bash
git add <specific files>
```

3. Write a commit message following this format:
   - First line: `feat: <short description>` (under 72 chars)
   - Body: what was added and why (2-3 lines)
4. Commit:

```bash
git commit -m "$(cat <<'EOF'
feat: <description>

<body>

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
```

5. Push:

```bash
git push
```

---

## Output Format

After all phases, produce a feature report:

```
FEATURE REPORT
==============
Feature:      <name>
Files Changed: <count> modified, <count> created
Review Rounds: <count> (iterations with user)

Implementation:
  - <file1>: <what changed>
  - <file2>: <what changed>

Tests:     [PASS] (X passed, Y new)
SOLID:     [PASS/FIXED] (issues found and fixed, if any)
Perf:      [PASS/FIXED] (issues found and fixed, if any)
Lint:      [PASS]
Format:    [PASS]
Docs:      [UPDATED] <list of docs updated>
Commit:    <hash> <message>
Push:      [OK]
```
