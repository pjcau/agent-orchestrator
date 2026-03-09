---
name: fetch-star-repos
description: Fetch GitHub starred repos, analyze one with LLM, and propose improvements via PR
disable-model-invocation: true
allowed-tools: Bash, Read
user-invocable: true
---

# Fetch Stars & Analyze — GitHub Stars to Improvement PRs

Fetches your recently starred GitHub repos (last 30 days), then runs the research
scout to analyze ONE repo and propose concrete code improvements via PR.

## Steps

1. Load env vars from `.env.local` and fetch starred repos:

```bash
set -a && source .env.local && set +a && LOOKBACK_DAYS=30 python3 scripts/fetch_github_stars.py
```

Required env vars in `.env.local`:
- `GITHUB_USERNAME` — Your GitHub username
- `OPENROUTER_API_KEY` — For LLM analysis

2. Show the updated bookmarks count:

```bash
cat .claude/bookmarks.json | python3 -c "import sys,json; bm=json.load(sys.stdin); print(f'Total starred repos: {len(bm)}'); [print(f'  - [{b.get(\"source\",\"?\")}] {b[\"url\"][:80]}') for b in bm[-10:]]"
```

3. Run the research scout (analyzes ONE unprocessed repo via LLM):

```bash
set -a && source .env.local && set +a && LOOKBACK_DAYS=30 python3 scripts/run_research_scout.py
```

4. If improvements were found (`.claude/research-scout-findings.md` exists), create a PR:

```bash
BRANCH="research-scout/$(date +%Y-%m-%d-%H%M)"
git checkout -b "$BRANCH"
git add .claude/research-scout-state.json .claude/bookmarks.json .claude/research-scout-findings.md
git commit -m "research-scout: improvement proposal from $(date +%Y-%m-%d)"
git push -u origin "$BRANCH"
gh pr create --title "research-scout: improvement proposal $(date +%Y-%m-%d)" --body "$(cat .claude/research-scout-findings.md)" --label "research-scout"
git checkout main
```

## Output

Report: how many repos were fetched, which repo was analyzed, what improvements
were proposed, and whether a PR was created.
