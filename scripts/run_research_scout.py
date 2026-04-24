#!/usr/bin/env python3
"""Research scout — analyze ONE starred repo per run, propose code improvements.

Flow:
  1. Pick the oldest unprocessed starred repo (30-day lookback)
  2. Fetch its README via GitHub API
  3. Call LLM (claude CLI locally, OpenRouter on CI) for concrete improvements
  4. Parse the JSON response and write a findings file
  5. PR creation is handled by the CI workflow (nightly-research.yml)

LLM backend:
  - Local: `claude` CLI (auto-detected, no API key needed)
  - CI/GitHub Actions: OpenRouter API (set OPENROUTER_API_KEY)
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_orchestrator.core.bookmark_tracker import (
    cleanup_old_entries,
    filter_unprocessed,
    load_bookmarks,
    load_state,
    mark_processed,
    save_state,
)

STATE_FILE = Path(".claude/research-scout-state.json")
BOOKMARKS_FILE = Path(".claude/bookmarks.json")
FINDINGS_FILE = Path(".claude/research-scout-findings.md")
LOOKBACK_DAYS = 30
MAX_IMPROVEMENTS = 30  # cap per repo; ranked by value_score desc

# Regex to extract owner/repo from GitHub URLs
_GH_REPO_RE = re.compile(r"github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/|$)")

# OpenRouter config (used on CI when OPENROUTER_API_KEY is set)
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "qwen/qwen3.5-flash-02-23"

# Our codebase summary — kept short to save tokens
CODEBASE_SUMMARY = """\
Project: agent-orchestrator — provider-agnostic AI agent orchestration framework.

Key modules (src/agent_orchestrator/core/):
- provider.py: LLM provider abstraction (complete, stream, tools)
- agent.py: Agent base class (role, tools, max_steps, anti-stall)
- skill.py: Skill registry with middleware (retry, logging, timeout)
- orchestrator.py: Task decomposition, agent coordination
- router.py: Smart task routing (6 strategies: local-first, cost-optimized, complexity-based)
- cooperation.py: Inter-agent messaging (delegation, results, conflict)
- graph.py: StateGraph engine (nodes, edges, parallel, HITL)
- checkpoint.py: InMemory + SQLite + Postgres checkpointers
- store.py: Cross-thread persistent key-value store (namespace, filter, TTL)
- cache.py: Task-level result caching (InMemory, TTL, cached_node)
- channels.py: Typed channels (LastValue, Topic, Barrier, Ephemeral)
- rate_limiter.py: Per-provider rate limiting
- health.py: Provider health monitoring, auto-failover
- usage.py: Cost tracking & budget enforcement

Providers: anthropic, openai, google, openrouter, local (Ollama/vLLM)
Dashboard: FastAPI + WebSocket, real-time agent monitoring
23 agents across 5 categories (software-eng, data-science, finance, marketing, tooling)
"""


def _fetch_github_readme(owner: str, repo: str) -> dict:
    """Fetch repo info + README from GitHub API using stdlib only."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "AgentOrchestrator-ResearchScout/1.0",
    }
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    repo_url = f"https://api.github.com/repos/{owner}/{repo}"
    req = Request(repo_url, headers=headers)
    try:
        with urlopen(req, timeout=15) as resp:
            repo_data = json.loads(resp.read().decode())
    except URLError as exc:
        return {"error": str(exc)}

    # Fetch README
    readme_text = ""
    readme_url = f"https://api.github.com/repos/{owner}/{repo}/readme"
    readme_headers = {**headers, "Accept": "application/vnd.github.v3.raw"}
    req2 = Request(readme_url, headers=readme_headers)
    try:
        with urlopen(req2, timeout=15) as resp:
            readme_text = resp.read().decode("utf-8", errors="replace")[:10000]
    except URLError:
        pass

    description = repo_data.get("description", "") or ""
    topics = repo_data.get("topics", [])
    language = repo_data.get("language", "") or ""
    stars = repo_data.get("stargazers_count", 0)

    title = f"{owner}/{repo}"
    text_parts = [
        f"# {title}",
        f"Description: {description}",
        f"Language: {language} | Stars: {stars}",
        f"Topics: {', '.join(topics)}",
        "",
        readme_text,
    ]

    total_chars = sum(len(p) for p in text_parts)
    return {"title": title, "text": "\n".join(text_parts), "char_count": total_chars}


def _fetch_url(url: str) -> dict:
    """Fetch content from a URL. GitHub API for repos, skip non-GitHub URLs."""
    match = _GH_REPO_RE.search(url)
    if match:
        return _fetch_github_readme(match.group(1), match.group(2))
    return {"error": "Only GitHub repo URLs are supported"}


def _call_claude(prompt: str) -> dict:
    """Call claude CLI in non-interactive mode. Returns {"content": str} or {"error": str}."""
    # Remove CLAUDECODE env var to allow nested invocation
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            return {"error": f"claude CLI failed (exit {result.returncode}): {stderr[:200]}"}
        content = result.stdout.strip()
        if not content:
            return {"error": "claude CLI returned empty output"}
        return {"content": content}
    except FileNotFoundError:
        return {
            "error": "claude CLI not found — install with: npm install -g @anthropic-ai/claude-code"
        }
    except subprocess.TimeoutExpired:
        return {"error": "claude CLI timed out after 120s"}


def _call_openrouter(prompt: str) -> dict:
    """Call OpenRouter API. Used on CI when OPENROUTER_API_KEY is set."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    model = os.environ.get("SCOUT_MODEL", OPENROUTER_MODEL)
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2000,
            "temperature": 0.3,
        }
    ).encode()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/pjcau/agent-orchestrator",
    }

    req = Request(OPENROUTER_API_URL, data=payload, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode())
    except URLError as exc:
        return {"error": f"OpenRouter API error: {exc}"}

    choice = data.get("choices", [{}])[0]
    content = choice.get("message", {}).get("content", "")
    if not content:
        return {"error": "OpenRouter returned empty response"}
    return {"content": content}


def _call_llm(prompt: str) -> dict:
    """Call LLM — claude CLI locally, OpenRouter on CI."""
    if os.environ.get("OPENROUTER_API_KEY") and os.environ.get("CI"):
        print("  Using OpenRouter API (CI mode)")
        return _call_openrouter(prompt)
    print("  Using claude CLI (local mode)")
    return _call_claude(prompt)


def _parse_improvements(llm_output: str) -> list[dict]:
    """Parse structured improvements from LLM output.

    Expected format: JSON array with objects containing:
      - component, title, description, file, code, benefit
    """
    # Strip markdown fences
    cleaned = re.sub(r"```(?:json)?\s*", "", llm_output).strip().rstrip("`")

    # Find JSON array
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []

    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    def _clamp(raw, default: float = 5.0, lo: float = 0.0, hi: float = 10.0) -> float:
        try:
            return max(lo, min(hi, float(raw)))
        except (TypeError, ValueError):
            return default

    improvements = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        desc = str(item.get("description", "")).strip()
        if not title or not desc:
            continue
        impact = _clamp(item.get("impact"))
        effort = _clamp(item.get("effort"))
        risk = _clamp(item.get("risk"))
        # value_score: LLM-provided if present, else derived from components.
        # Derivation: high impact, low effort, low risk → high value.
        if "value_score" in item:
            value_score = _clamp(item.get("value_score"))
        else:
            value_score = max(0.0, min(10.0, impact - 0.3 * effort - 0.5 * risk))
        improvements.append(
            {
                "component": str(item.get("component", "general")).strip(),
                "title": title,
                "description": desc,
                "file": str(item.get("file", "")).strip(),
                "code": str(item.get("code", "")).strip(),
                "benefit": str(item.get("benefit", "")).strip(),
                "impact": impact,
                "effort": effort,
                "risk": risk,
                "value_score": value_score,
            }
        )

    # Rank by value_score desc, then cap at MAX_IMPROVEMENTS
    improvements.sort(key=lambda imp: imp["value_score"], reverse=True)
    return improvements[:MAX_IMPROVEMENTS]


def _write_findings(repo_title: str, repo_url: str, improvements: list[dict]) -> None:
    """Write findings to a markdown file for the PR body."""
    lines = [
        f"## Research Scout: improvements from [{repo_title}]({repo_url})\n",
        f"Analyzed [{repo_title}]({repo_url}) and found "
        f"**{len(improvements)}** actionable improvement(s) for the orchestrator.\n",
    ]

    for i, imp in enumerate(improvements, 1):
        score = imp.get("value_score")
        score_str = f" — value `{score:.1f}/10`" if isinstance(score, (int, float)) else ""
        lines.append(f"### {i}. {imp['title']}{score_str}\n")
        lines.append(f"**Component:** `{imp['component']}`")
        if imp.get("file"):
            lines.append(f"**File:** `{imp['file']}`\n")
        scoring_parts = []
        for key in ("impact", "effort", "risk"):
            val = imp.get(key)
            if isinstance(val, (int, float)):
                scoring_parts.append(f"{key} `{val:.0f}`")
        if scoring_parts:
            lines.append(f"**Scoring:** {' · '.join(scoring_parts)}\n")
        lines.append(f"{imp['description']}\n")
        if imp.get("code"):
            lines.append("```python")
            lines.append(imp["code"])
            lines.append("```\n")
        if imp.get("benefit"):
            lines.append(f"**Benefit:** {imp['benefit']}\n")

    FINDINGS_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"Findings written to {FINDINGS_FILE}")


def _create_pr(findings_file: Path) -> bool:
    """Create a PR branch with findings (local only, CI uses workflow step).

    Returns True if PR was created.
    """
    from datetime import datetime, timezone

    branch = f"research-scout/{datetime.now(timezone.utc).strftime('%Y-%m-%d-%H%M')}"
    files_to_add = [
        str(STATE_FILE),
        str(BOOKMARKS_FILE),
        str(findings_file),
    ]

    try:
        subprocess.run(
            ["git", "checkout", "-b", branch], check=True, capture_output=True, text=True
        )
        subprocess.run(["git", "add"] + files_to_add, check=True, capture_output=True, text=True)

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        subprocess.run(
            ["git", "commit", "-m", f"research-scout: improvement proposal from {date_str}"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", branch],
            check=True,
            capture_output=True,
            text=True,
        )

        # Use --body-file to avoid shell escaping issues with backticks in markdown
        result = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--title",
                f"research-scout: improvement proposal {date_str}",
                "--body-file",
                str(findings_file),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        pr_url = result.stdout.strip()
        print(f"PR created: {pr_url}")

        subprocess.run(["git", "checkout", "main"], check=True, capture_output=True, text=True)
        return True

    except subprocess.CalledProcessError as exc:
        print(f"  PR creation failed: {exc.stderr or exc}")
        subprocess.run(["git", "checkout", "main"], capture_output=True, text=True)
        return False


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        help="Reprocess a specific GitHub repo URL (bypasses bookmarks + "
        "lookback window). Useful for re-running failed analyses or "
        "regenerating findings for an existing research-scout PR.",
    )
    parser.add_argument(
        "--skip-state",
        action="store_true",
        help="With --url, do not mark the repo as processed in the state file.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    args = _parse_args(argv)
    lookback = int(os.environ.get("LOOKBACK_DAYS", str(LOOKBACK_DAYS)))
    print(f"Research Scout — lookback: {lookback} days, mode: single-repo, LLM: claude CLI")

    state = load_state(STATE_FILE)

    def _record(url: str, **kwargs) -> None:
        """Mark URL as processed and flush state — skipped when --skip-state is set."""
        if args.skip_state:
            return
        mark_processed(state, url, **kwargs)
        save_state(STATE_FILE, state)

    if args.url:
        url = args.url
        print(f"\nReprocessing specific URL: {url}")
    else:
        bookmarks = load_bookmarks(BOOKMARKS_FILE)
        print(
            f"Loaded {len(bookmarks)} bookmark(s), "
            f"{len(state.get('processed', {}))} already processed"
        )

        # Filter to unprocessed within lookback window
        to_process = filter_unprocessed(bookmarks, state, lookback_days=lookback)
        print(f"Unprocessed repos in window: {len(to_process)}")

        if not to_process:
            print("No new repos to process. Done.")
            FINDINGS_FILE.unlink(missing_ok=True)
            if not args.skip_state:
                save_state(STATE_FILE, state)
            return

        # Pick ONE — the oldest unprocessed repo
        bm = to_process[0]
        url = bm["url"]
        print(f"\nAnalyzing: {url}")

    # Step 1: Fetch content
    content = _fetch_url(url)
    if "error" in content:
        print(f"  Failed to fetch: {content['error']}")
        _record(url, summary=f"fetch-error: {content['error']}")
        FINDINGS_FILE.unlink(missing_ok=True)
        return

    title = content.get("title", url)
    text = content.get("text", "")
    print(f"  Title: {title} ({len(text)} chars)")

    # Step 2: Quick relevance check (keyword pre-filter to skip irrelevant repos)
    relevance_keywords = [
        "agent",
        "orchestrat",
        "llm",
        "multi-agent",
        "workflow",
        "pipeline",
        "routing",
        "tool",
        "skill",
        "provider",
        "model",
    ]
    text_lower = text.lower()
    keyword_hits = sum(1 for kw in relevance_keywords if kw in text_lower)
    if keyword_hits < 2:
        print(f"  Low relevance ({keyword_hits} keyword hits). Skipping.")
        _record(url, summary=f"low-relevance: {title}")
        FINDINGS_FILE.unlink(missing_ok=True)
        return

    # Step 3: Call claude CLI for analysis
    print("  Calling LLM for analysis...")
    prompt = f"""\
## Our codebase
{CODEBASE_SUMMARY}

## Repository to analyze
{text[:8000]}

## Task
Analyze this repository and find up to {MAX_IMPROVEMENTS} concrete improvements
we could apply to our agent-orchestrator codebase. Quality over quantity — only
include improvements that:
- Are clearly inspired by a pattern/technique from this repo
- Map to a specific file in our codebase
- Include a code snippet showing the improvement

We rank proposals by **value_score** (higher = apply first), so be honest with
the scoring so the top candidates reflect real impact, not enthusiasm.

Respond with ONLY a JSON array, no other text. Each item must have:
- "component": which part of our codebase (router, agent, skill, provider, graph, etc.)
- "title": short title (max 10 words)
- "description": 2-3 sentences explaining the improvement and how it's inspired by the repo
- "file": path to modify (e.g. "src/agent_orchestrator/core/router.py")
- "code": Python code snippet showing the improvement (function or class to add/modify)
- "benefit": one sentence on the expected benefit
- "impact": integer 1-10, user/system improvement magnitude (10 = transformative)
- "effort": integer 1-10, implementation cost (1 = trivial, 10 = large refactor)
- "risk": integer 1-10, chance of regressions or breaking current behaviour (1 = safe)
- "value_score": integer 1-10, your overall "apply-this-first" priority —
  roughly `impact - 0.3*effort - 0.5*risk`, but adjust for strategic fit

Prefer fewer, high-value items over many mediocre ones. If you find more than
{MAX_IMPROVEMENTS} candidates, submit only the top {MAX_IMPROVEMENTS} by
value_score. If nothing is worth applying, return an empty array: []
"""

    llm_result = _call_llm(prompt)
    if "error" in llm_result:
        print(f"  LLM error: {llm_result['error']}")
        _record(url, summary=f"llm-error: {llm_result['error']}")
        FINDINGS_FILE.unlink(missing_ok=True)
        return

    llm_content = llm_result["content"]
    print(f"  Claude response: {len(llm_content)} chars")

    # Step 4: Parse improvements
    improvements = _parse_improvements(llm_content)
    print(f"  Parsed {len(improvements)} improvement(s)")

    for imp in improvements:
        print(f"    - [{imp['component']}] {imp['title']}")
        if imp.get("file"):
            print(f"      File: {imp['file']}")

    # Step 5: Write findings and create PR (if any)
    if improvements:
        _write_findings(title, url, improvements)

        # Mark as processed before PR creation (so state is committed too)
        _record(
            url,
            summary=title,
            improvements=[imp["title"] for imp in improvements],
        )

        # Create PR locally; on CI the workflow step handles this.
        # --skip-state disables PR creation too (used for regenerating findings
        # for an existing PR — the caller updates the PR body themselves).
        if not os.environ.get("CI") and not args.skip_state:
            print("\nCreating PR...")
            if _create_pr(FINDINGS_FILE):
                print("PR created successfully.")
            else:
                print("PR creation failed — findings saved locally.")
    else:
        print("  No actionable improvements found.")
        FINDINGS_FILE.unlink(missing_ok=True)
        _record(url, summary=title, improvements=[])

    # Cleanup old entries
    if not args.skip_state:
        removed = cleanup_old_entries(state, max_age_days=60)
        if removed:
            print(f"\nCleaned up {removed} old state entries (>60 days)")
            save_state(STATE_FILE, state)

    print("Done.")


if __name__ == "__main__":
    main()
