---
name: fetch-star-repos
description: Fetch recently starred GitHub repos and merge them into .claude/bookmarks.json
disable-model-invocation: true
allowed-tools: Bash, Read
user-invocable: true
---

# Fetch Bookmarks — Import GitHub Stars

Fetches your recently starred GitHub repos and merges them into `.claude/bookmarks.json`.
Only stars from the last 7 days are imported.

## Steps

1. Load env vars from `.env.local` and run the fetch script:

```bash
set -a && source .env.local && set +a && python3 scripts/fetch_github_stars.py
```

Required env vars in `.env.local`:
- `GITHUB_USERNAME` — Your GitHub username (or auto-detected from token)
- `GITHUB_TOKEN` — (optional) Personal access token for higher rate limits

2. Show the updated bookmarks count:

```bash
cat .claude/bookmarks.json | python3 -c "import sys,json; bm=json.load(sys.stdin); print(f'Total bookmarks: {len(bm)}'); [print(f'  - [{b.get(\"source\",\"?\")}] {b[\"url\"][:80]}') for b in bm[-10:]]"
```

3. After fetching, automatically run the research scout to analyze new bookmarks:

```bash
set -a && source .env.local && set +a && python3 scripts/run_research_scout.py
```

4. If findings were generated (`.claude/research-scout-findings.md` exists), create a PR:

```bash
BRANCH="research-scout/$(date +%Y-%m-%d)"
git checkout -b "$BRANCH"
git add .claude/research-scout-state.json .claude/bookmarks.json .claude/research-scout-findings.md
git commit -m "research-scout: findings from $(date +%Y-%m-%d)"
git push -u origin "$BRANCH"
gh pr create --title "research-scout: findings $(date +%Y-%m-%d)" --body "$(cat .claude/research-scout-findings.md)"
```

## Output

Report how many repos were fetched, the total bookmark count, and whether a PR was created.
