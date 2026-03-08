#!/usr/bin/env python3
"""Run the research scout — process unread bookmarks and analyze content.

Reads bookmarks from .claude/bookmarks.json, filters unprocessed ones
within the lookback window, fetches their content, and logs findings
to .claude/research-scout-state.json.

For GitHub URLs, fetches README via the GitHub API (no aiohttp needed).
For other URLs, falls back to WebReaderSkill (requires aiohttp).
"""

import json
import os
import re
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
USAGE_FILE = Path(".claude/research-scout-usage.json")
MAX_URLS_PER_RUN = 10

# Model pricing per 1M tokens (USD) — update as needed
MODEL_PRICING = {
    "qwen/qwen3-coder:free": {"input": 0.0, "output": 0.0},
    "default": {"input": 0.50, "output": 1.50},
}


class UsageTracker:
    """Track token usage and costs across the run."""

    def __init__(self):
        self.github_api_calls = 0
        self.chars_fetched = 0
        self.llm_input_tokens = 0
        self.llm_output_tokens = 0
        self.llm_model = ""
        self.llm_cost_usd = 0.0

    def add_fetch(self, char_count: int):
        self.github_api_calls += 1
        self.chars_fetched += char_count

    def add_llm_usage(self, model: str, input_tokens: int, output_tokens: int):
        self.llm_model = model
        self.llm_input_tokens += input_tokens
        self.llm_output_tokens += output_tokens
        pricing = MODEL_PRICING.get(model, MODEL_PRICING["default"])
        self.llm_cost_usd += (
            input_tokens * pricing["input"] + output_tokens * pricing["output"]
        ) / 1_000_000

    def summary(self) -> str:
        lines = [
            "\n--- USAGE REPORT ---",
            f"GitHub API calls:  {self.github_api_calls}",
            f"Characters fetched: {self.chars_fetched:,}",
        ]
        if self.llm_input_tokens or self.llm_output_tokens:
            total_tokens = self.llm_input_tokens + self.llm_output_tokens
            lines.extend(
                [
                    f"LLM model:         {self.llm_model}",
                    f"LLM input tokens:  {self.llm_input_tokens:,}",
                    f"LLM output tokens: {self.llm_output_tokens:,}",
                    f"LLM total tokens:  {total_tokens:,}",
                    f"LLM cost:          ${self.llm_cost_usd:.4f}",
                ]
            )
        else:
            lines.append("LLM tokens:        0 (keyword analysis only)")
            lines.append("LLM cost:          $0.0000")
        lines.append("--------------------")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "github_api_calls": self.github_api_calls,
            "chars_fetched": self.chars_fetched,
            "llm_model": self.llm_model,
            "llm_input_tokens": self.llm_input_tokens,
            "llm_output_tokens": self.llm_output_tokens,
            "llm_total_tokens": self.llm_input_tokens + self.llm_output_tokens,
            "llm_cost_usd": round(self.llm_cost_usd, 6),
        }


usage = UsageTracker()

# Regex to extract owner/repo from GitHub URLs
_GH_REPO_RE = re.compile(r"github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/|$)")


def _fetch_github_readme(owner: str, repo: str) -> dict:
    """Fetch repo info + README from GitHub API using stdlib only."""
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "AgentOrchestrator-ResearchScout/1.0",
    }
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # Fetch repo metadata
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
            readme_text = resp.read().decode("utf-8", errors="replace")[:15000]
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
        f"Language: {language}",
        f"Stars: {stars}",
        f"Topics: {', '.join(topics)}",
        "",
        readme_text,
    ]

    total_chars = sum(len(p) for p in text_parts)
    usage.add_fetch(total_chars)

    return {
        "title": title,
        "text": "\n".join(text_parts),
        "char_count": total_chars,
    }


def _fetch_url(url: str) -> dict:
    """Fetch content from a URL. Uses GitHub API for GitHub repos, WebReaderSkill otherwise."""
    match = _GH_REPO_RE.search(url)
    if match:
        owner, repo = match.group(1), match.group(2)
        return _fetch_github_readme(owner, repo)

    # Non-GitHub URLs: try WebReaderSkill
    try:
        import asyncio
        from agent_orchestrator.skills.web_reader import WebReaderSkill

        reader = WebReaderSkill(max_chars=15_000, timeout=15)
        result = asyncio.get_event_loop().run_until_complete(reader.execute({"url": url}))
        if result.success:
            return result.output
        return {"error": result.error}
    except ImportError:
        return {"error": "aiohttp not available and URL is not a GitHub repo"}


def analyze_content(text: str, url: str) -> dict:
    """Analyze web content for orchestrator improvement ideas."""
    components = {
        "memory": ["memory", "state", "persistence", "context", "session", "cache", "store"],
        "router": ["routing", "router", "dispatch", "load balance", "strategy", "cost", "optimize"],
        "agents": ["agent", "multi-agent", "coordination", "orchestrat", "delegation", "role"],
        "skills": ["skill", "tool", "capability", "workflow", "pipeline", "chain"],
        "tools": ["api", "integration", "sdk", "cli", "plugin", "extension", "mcp"],
    }

    text_lower = text.lower()
    improvements = []

    for component, keywords in components.items():
        matches = [kw for kw in keywords if kw in text_lower]
        if len(matches) >= 2:
            improvements.append(
                {
                    "component": component,
                    "keywords_found": matches,
                    "relevance": min(len(matches) / len(keywords), 1.0),
                }
            )

    improvements.sort(key=lambda x: x["relevance"], reverse=True)
    return {
        "url": url,
        "method": "keyword",
        "improvements": improvements[:5],
    }


def _write_findings(findings: list[dict]) -> None:
    """Write findings to a markdown file for PR creation."""
    if not findings:
        FINDINGS_FILE.unlink(missing_ok=True)
        return

    lines = ["## Research Scout Findings\n"]
    total = sum(len(f["improvements"]) for f in findings)
    lines.append(
        f"Found **{total}** potential improvement(s) from **{len(findings)}** source(s).\n"
    )

    for finding in findings:
        url = finding["url"]
        title = finding.get("title", url)
        lines.append(f"### [{title}]({url})\n")
        for imp in finding["improvements"]:
            comp = imp.get("component", "?")
            imp_title = imp.get("title", imp.get("keywords_found", ""))
            desc = imp.get("description", "")
            benefit = imp.get("benefit", "")
            lines.append(f"- **[{comp}]** {imp_title}")
            if desc:
                lines.append(f"  - {desc}")
            if benefit:
                lines.append(f"  - Benefit: {benefit}")
        lines.append("")

    FINDINGS_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"Findings written to {FINDINGS_FILE}")


def main():
    lookback = int(os.environ.get("LOOKBACK_DAYS", "7"))
    print(f"Research Scout — lookback: {lookback} days, max URLs: {MAX_URLS_PER_RUN}")

    # Load state and bookmarks
    state = load_state(STATE_FILE)
    bookmarks = load_bookmarks(BOOKMARKS_FILE)
    print(
        f"Loaded {len(bookmarks)} bookmark(s), {len(state.get('processed', {}))} already processed"
    )

    # Filter to unprocessed within lookback window
    to_process = filter_unprocessed(bookmarks, state, lookback_days=lookback)
    to_process = to_process[:MAX_URLS_PER_RUN]
    print(f"Processing {len(to_process)} new bookmark(s)")

    if not to_process:
        print("No new bookmarks to process. Done.")
        save_state(STATE_FILE, state)
        return

    all_findings: list[dict] = []

    for i, bm in enumerate(to_process, 1):
        url = bm["url"]
        print(f"\n[{i}/{len(to_process)}] Fetching: {url}")

        content = _fetch_url(url)
        if "error" in content:
            print(f"  Failed: {content['error']}")
            mark_processed(state, url, summary=f"fetch-error: {content['error']}")
            continue

        title = content.get("title", "")
        char_count = content.get("char_count", 0)
        print(f"  Title: {title} ({char_count} chars)")

        # Analyze content
        analysis = analyze_content(content.get("text", ""), url)
        improvements = analysis.get("improvements", [])
        print(f"  Found {len(improvements)} potential improvement(s)")

        for imp in improvements:
            comp = imp.get("component", "?")
            title_imp = imp.get("title", imp.get("keywords_found", ""))
            print(f"    - [{comp}] {title_imp}")

        if improvements:
            all_findings.append({"url": url, "title": title, "improvements": improvements})

        # Mark as processed
        mark_processed(
            state,
            url,
            summary=title,
            improvements=[
                imp.get("title", str(imp.get("keywords_found", ""))) for imp in improvements
            ],
        )

    # Cleanup old entries
    removed = cleanup_old_entries(state, max_age_days=30)
    if removed:
        print(f"\nCleaned up {removed} old entries (>30 days)")

    # Generate findings file if there are improvements
    _write_findings(all_findings)

    # Save state
    save_state(STATE_FILE, state)

    # Write usage report
    print(usage.summary())
    USAGE_FILE.write_text(json.dumps(usage.to_dict(), indent=2), encoding="utf-8")
    print(f"Done. State saved to {STATE_FILE}")


if __name__ == "__main__":
    main()
