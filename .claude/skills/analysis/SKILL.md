---
name: analysis
description: Deep-dive analysis of an open-source repository. Clones the repo, explores architecture/patterns/internals, and produces up to 30 structured Markdown files in analysis/<repo-name>/. Use when analyzing a GitHub repo for learnings, comparison, or adoption roadmap.
disable-model-invocation: false
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, WebFetch, WebSearch, Agent
user-invocable: true
argument-hint: <github-url-or-repo-name>
---

# Repository Deep Analysis

Analyze an open-source repository and produce up to 30 structured Markdown files under `analysis/<repo-name>/`.

## Critical Rules

1. **All output in English** (project convention)
2. **Max 30 files** — numbered `00-topic.md` through `29-topic.md` (or fewer if the repo is small)
3. **Always include a `README.md`** as the index file with a table linking all analysis files
4. **Output goes to `analysis/<repo-name>/`** — create the subfolder, never write outside it
5. **Compare to our orchestrator** — at least one file must compare the repo's patterns to ours
6. **End with learnings** — the last file must be actionable takeaways for our project
7. **Do NOT commit or push** — just write the files, the user decides when to commit

## Phase 1: Setup & Clone

1. Parse the argument to extract the repo URL and short name:
   - Full URL: `https://github.com/org/repo` → name is `repo`
   - Short form: `org/repo` → name is `repo`
   - If no argument provided, ask the user for a repo URL

2. Create the output directory:
   ```bash
   mkdir -p analysis/<repo-name>
   ```

3. Clone the repo into a temporary directory (shallow clone to save space):
   ```bash
   git clone --depth 1 <repo-url> /tmp/analysis-<repo-name>
   ```
   If clone fails, try with `--depth 5`. If still fails, report and stop.

## Phase 2: Reconnaissance

Before writing any files, explore the cloned repo to understand its structure:

1. **Directory tree** — `find /tmp/analysis-<repo-name> -type f | head -200` to get the layout
2. **README** — read the repo's README for high-level context
3. **Package/build files** — `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, etc.
4. **Source structure** — identify the main source directories
5. **Config files** — `.env.example`, docker-compose, CI configs
6. **Tests** — test directory structure and approach
7. **Documentation** — docs folder, architecture docs

Take notes on what deserves a dedicated analysis file.

## Phase 3: Plan the Analysis Structure

Based on reconnaissance, plan up to 30 files. Use this template as a starting guide, adapting topics to what the repo actually contains:

### Core (files 00-09)
| # | Suggested Topic |
|---|----------------|
| 00 | Project overview — goals, positioning, stats |
| 01 | Architecture — high-level design, component diagram |
| 02 | Tech stack — languages, frameworks, dependencies |
| 03 | Core abstraction 1 (e.g., agent system, state machine) |
| 04 | Core abstraction 2 (e.g., middleware, plugins) |
| 05 | Core abstraction 3 (e.g., tool system, skills) |
| 06 | Core abstraction 4 |
| 07 | Core abstraction 5 |
| 08 | Core abstraction 6 |
| 09 | Core abstraction 7 |

### Infrastructure (files 10-19)
| # | Suggested Topic |
|---|----------------|
| 10 | Configuration system |
| 11 | State/persistence |
| 12 | API layer |
| 13 | Streaming/real-time |
| 14 | Frontend/UI |
| 15 | Integrations |
| 16 | Security |
| 17 | Testing strategy |
| 18 | Error handling |
| 19 | Deployment/Docker |

### Insights (files 20-29)
| # | Suggested Topic |
|---|----------------|
| 20-25 | Additional deep dives (as needed) |
| 26 | Comparison — vs our agent-orchestrator |
| 27 | Strengths — what this repo does well |
| 28 | Weaknesses — gaps and limitations |
| 29 | Learnings — actionable takeaways and adoption roadmap |

**Adapt this plan** to the actual repo. A frontend framework needs different files than a backend engine. Skip irrelevant sections, add repo-specific ones. The comparison + strengths + weaknesses + learnings files (26-29) are mandatory.

## Phase 4: Write Analysis Files

For each planned file:

1. **Read the relevant source code** from the cloned repo — don't guess, read actual files
2. **Analyze patterns, design decisions, trade-offs**
3. **Include code snippets** where they illustrate key patterns (keep snippets short, < 30 lines)
4. **Write the file** to `analysis/<repo-name>/NN-topic.md`

### File Format

Each analysis file should follow this structure:

```markdown
# NN - Topic Title

## Overview
Brief summary of what this file covers.

## [Main sections — vary by topic]
Detailed analysis with code snippets, diagrams, observations.

## Key Patterns
- Pattern 1: description
- Pattern 2: description

## Relevance to Our Project
How this relates to or could improve agent-orchestrator.
```

### Quality Standards

- **Read before you write** — every claim must be backed by actual source code
- **Be specific** — cite file paths, function names, line numbers from the cloned repo
- **Show, don't tell** — include short code snippets for important patterns
- **Be opinionated** — state what's good, what's bad, what we should adopt
- **Cross-reference** — link between analysis files where topics connect

## Phase 5: Write README.md

After all analysis files are written, create `analysis/<repo-name>/README.md`:

```markdown
# <Repo Name> Analysis

**Repository**: [org/repo](https://github.com/org/repo)
**Analysis Date**: YYYY-MM-DD
**Version Analyzed**: <version or commit hash>

## Key Stats
- Language: ...
- License: ...
- Stars: ... (if known)

## Analysis Structure (N files)

| # | File | Topic |
|---|------|-------|
| 00 | [00-topic.md](00-topic.md) | Description |
| 01 | [01-topic.md](01-topic.md) | Description |
...

## Quick Start

Start with **[00-overview](00-overview.md)** for the big picture,
then **[26-comparison](26-comparison.md)** for how it relates to our project,
and **[29-learnings](29-learnings.md)** for actionable next steps.
```

## Phase 6: Cleanup

1. Remove the cloned repo:
   ```bash
   rm -rf /tmp/analysis-<repo-name>
   ```

2. Report summary:
   ```
   ANALYSIS COMPLETE
   =================
   Repository:  org/repo
   Output:      analysis/<repo-name>/
   Files:       N analysis files + README.md

   Key findings:
     - Finding 1
     - Finding 2
     - Finding 3

   Start reading: analysis/<repo-name>/README.md
   ```

## Parallelization Strategy

To speed up analysis, use Agent tool to parallelize independent file writes:
- Files 00-09 (core) can often be written in parallel batches of 3-5
- Files 26-29 (insights) depend on earlier files — write these last, sequentially
- The README must be written last (it references all other files)

## Error Handling

- **Clone fails**: Try HTTPS if SSH fails. Try without `--depth`. Report if still fails.
- **Repo is huge**: Focus on the most important directories. Skip vendored/generated code.
- **Repo is tiny**: Produce fewer files (minimum 5). Combine related topics.
- **Private repo**: Report that access is denied and ask the user for credentials or a local path.
- **Local path**: If the argument is a local directory path instead of a URL, skip cloning and analyze directly.
