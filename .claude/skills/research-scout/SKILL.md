---
name: research-scout
description: Read bookmarks from .claude/bookmarks.json, fetch each URL, analyze content, and propose orchestrator improvements.
disable-model-invocation: false
allowed-tools: WebFetch, WebSearch, Read, Write, Edit, Bash
user-invocable: true
---

# Research Scout — Bookmark Analysis for Orchestrator Improvements

Read URLs from `.claude/bookmarks.json`, fetch their content, analyze it, and propose concrete improvements to the agent orchestrator.

## Phase 1: Load Bookmarks & State

1. Read `.claude/bookmarks.json` to get the list of URLs
2. Read `.claude/research-scout-state.json` to see which URLs have already been processed
3. Filter out:
   - Already processed URLs (present in state file)
   - URLs with `added` date older than 7 days
4. Report how many new URLs to process

## Phase 2: Fetch & Analyze Each URL

For each unprocessed URL (max 10 per run):

1. **Fetch the content** using `WebFetch`:
   - For X/Twitter URLs (`x.com`, `twitter.com`): convert to `api.vxtwitter.com` first
   - For all other URLs: fetch directly
   - If fetch fails, try `WebSearch` with the URL as query

2. **Analyze the content** looking for ideas that could improve:
   - **Memory** — state persistence, context optimization, cross-session knowledge
   - **Router** — task routing algorithms, cost optimization, complexity heuristics
   - **Agents** — new roles, better prompting patterns, coordination improvements
   - **Skills** — new tool ideas, workflow patterns, error recovery
   - **Tools** — API integrations, plugins, MCP extensions

3. **Score relevance** (0-1):
   - Applicable: relevant to AI orchestration? (> 0.5 required)
   - Novel: adds something we don't already have?
   - Actionable: can be implemented in our codebase?

4. **Propose improvements** with:
   - Target component (memory/router/agents/skills/tools)
   - Title and description
   - Files to modify
   - Expected benefit

## Phase 3: Update State

After processing all URLs, update `.claude/research-scout-state.json`:

```bash
# Read current state
cat .claude/research-scout-state.json
```

Then use `Edit` or `Write` to add each processed URL to the `processed` dict:

```json
{
  "processed": {
    "https://example.com/article": {
      "processed_at": "2026-03-08T12:00:00Z",
      "summary": "Article about multi-agent routing patterns",
      "improvements": ["Adaptive routing based on task history"]
    }
  },
  "last_run": "2026-03-08T12:00:00Z"
}
```

## Phase 4: Report & PR

Print a summary report:

```
RESEARCH SCOUT REPORT
=====================
Bookmarks:  X total, Y new, Z skipped (old/processed)
Processed:  N URLs

Improvements Found:
  [memory]  Title — description (files: ...)
  [router]  Title — description (files: ...)
  [agents]  Title — description (files: ...)

State: updated .claude/research-scout-state.json
```

**If improvements were found**, create a PR:

1. Create a branch named `research-scout/YYYY-MM-DD`
2. Commit the updated state file and findings
3. Create a PR with the findings as the body:

```bash
BRANCH="research-scout/$(date +%Y-%m-%d)"
git checkout -b "$BRANCH"
git add .claude/research-scout-state.json .claude/bookmarks.json .claude/research-scout-findings.md
git commit -m "research-scout: findings from $(date +%Y-%m-%d)"
git push -u origin "$BRANCH"
gh pr create --title "research-scout: findings $(date +%Y-%m-%d)" --body "$(cat .claude/research-scout-findings.md)"
```

If no improvements were found, just commit state updates to the current branch.

## Rules

- **Max 10 URLs per run** — quality over quantity
- **Max 5 improvements per URL** — only the most relevant
- **Skip irrelevant content** — if a URL has nothing useful for the orchestrator, mark it as processed with empty improvements and move on
- **Don't modify code** — only propose improvements, don't implement them
- **Always create a PR when findings exist** — never push findings directly to main
- **All output in English**
