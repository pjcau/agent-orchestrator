#!/usr/bin/env python3
"""Research scout — analyze ONE bookmark per run, propose code improvements via PR.

Flow:
  1. Pick the oldest unprocessed bookmark (30-day lookback)
  2. Fetch its README/content
  3. Send to LLM with a summary of our codebase — ask for concrete improvements
  4. Write the LLM's proposed changes to files
  5. Output a PR-ready findings file with explanation

Designed to be token-efficient: one repo per run, one LLM call.
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
LOOKBACK_DAYS = 30

# Regex to extract owner/repo from GitHub URLs
_GH_REPO_RE = re.compile(r"github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/|$)")

# OpenRouter API
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "qwen/qwen3-coder:free"

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

# Model pricing per 1M tokens (USD)
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
        total_tokens = self.llm_input_tokens + self.llm_output_tokens
        lines = [
            "\n--- USAGE REPORT ---",
            f"GitHub API calls:  {self.github_api_calls}",
            f"Characters fetched: {self.chars_fetched:,}",
            f"LLM model:         {self.llm_model or 'none'}",
            f"LLM tokens:        {total_tokens:,} ({self.llm_input_tokens:,}in / {self.llm_output_tokens:,}out)",
            f"LLM cost:          ${self.llm_cost_usd:.4f}",
            "--------------------",
        ]
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
    usage.add_fetch(total_chars)

    return {"title": title, "text": "\n".join(text_parts), "char_count": total_chars}


def _fetch_url(url: str) -> dict:
    """Fetch content from a URL. GitHub API for repos, WebReaderSkill otherwise."""
    match = _GH_REPO_RE.search(url)
    if match:
        return _fetch_github_readme(match.group(1), match.group(2))

    try:
        import asyncio
        from agent_orchestrator.skills.web_reader import WebReaderSkill

        reader = WebReaderSkill(max_chars=10_000, timeout=15)
        result = asyncio.get_event_loop().run_until_complete(reader.execute({"url": url}))
        if result.success:
            return result.output
        return {"error": result.error}
    except ImportError:
        return {"error": "aiohttp not available and URL is not a GitHub repo"}


def _call_llm(prompt: str, system: str) -> dict:
    """Call OpenRouter LLM API. Returns {"content": str, "input_tokens": int, "output_tokens": int}."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return {"error": "OPENROUTER_API_KEY not set"}

    model = os.environ.get("SCOUT_MODEL", DEFAULT_MODEL)
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
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
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
    except URLError as exc:
        return {"error": f"LLM API error: {exc}"}

    choice = data.get("choices", [{}])[0]
    content = choice.get("message", {}).get("content", "")
    token_usage = data.get("usage", {})
    input_tokens = token_usage.get("prompt_tokens", 0)
    output_tokens = token_usage.get("completion_tokens", 0)

    usage.add_llm_usage(model, input_tokens, output_tokens)

    return {"content": content, "input_tokens": input_tokens, "output_tokens": output_tokens}


def _parse_improvements(llm_output: str) -> list[dict]:
    """Parse structured improvements from LLM output.

    Expected format: JSON array with objects containing:
      - component: str (router, agent, skill, etc.)
      - title: str
      - description: str
      - file: str (path to modify)
      - code: str (code snippet to add/modify)
      - benefit: str
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

    improvements = []
    for item in data[:3]:  # Max 3 improvements per repo
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        desc = str(item.get("description", "")).strip()
        if not title or not desc:
            continue
        improvements.append(
            {
                "component": str(item.get("component", "general")).strip(),
                "title": title,
                "description": desc,
                "file": str(item.get("file", "")).strip(),
                "code": str(item.get("code", "")).strip(),
                "benefit": str(item.get("benefit", "")).strip(),
            }
        )

    return improvements


def _write_findings(repo_title: str, repo_url: str, improvements: list[dict]) -> None:
    """Write findings to a markdown file for the PR body."""
    lines = [
        f"## Research Scout: improvements from [{repo_title}]({repo_url})\n",
        f"Analyzed [{repo_title}]({repo_url}) and found "
        f"**{len(improvements)}** actionable improvement(s) for the orchestrator.\n",
    ]

    for i, imp in enumerate(improvements, 1):
        lines.append(f"### {i}. {imp['title']}\n")
        lines.append(f"**Component:** `{imp['component']}`")
        if imp.get("file"):
            lines.append(f"**File:** `{imp['file']}`\n")
        lines.append(f"{imp['description']}\n")
        if imp.get("code"):
            lines.append("```python")
            lines.append(imp["code"])
            lines.append("```\n")
        if imp.get("benefit"):
            lines.append(f"**Benefit:** {imp['benefit']}\n")

    if usage.llm_input_tokens or usage.llm_output_tokens:
        total = usage.llm_input_tokens + usage.llm_output_tokens
        lines.append("---")
        lines.append(
            f"*Scout used {total:,} tokens ({usage.llm_model}) — ${usage.llm_cost_usd:.4f}*"
        )

    FINDINGS_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"Findings written to {FINDINGS_FILE}")


def main():
    lookback = int(os.environ.get("LOOKBACK_DAYS", str(LOOKBACK_DAYS)))
    print(f"Research Scout — lookback: {lookback} days, mode: single-repo")

    state = load_state(STATE_FILE)
    bookmarks = load_bookmarks(BOOKMARKS_FILE)
    print(
        f"Loaded {len(bookmarks)} bookmark(s), {len(state.get('processed', {}))} already processed"
    )

    # Filter to unprocessed within lookback window
    to_process = filter_unprocessed(bookmarks, state, lookback_days=lookback)
    print(f"Unprocessed bookmarks in window: {len(to_process)}")

    if not to_process:
        print("No new bookmarks to process. Done.")
        FINDINGS_FILE.unlink(missing_ok=True)
        save_state(STATE_FILE, state)
        return

    # Pick ONE — the oldest unprocessed bookmark (first in the list)
    bm = to_process[0]
    url = bm["url"]
    print(f"\nAnalyzing: {url}")

    # Step 1: Fetch content
    content = _fetch_url(url)
    if "error" in content:
        print(f"  Failed to fetch: {content['error']}")
        mark_processed(state, url, summary=f"fetch-error: {content['error']}")
        FINDINGS_FILE.unlink(missing_ok=True)
        save_state(STATE_FILE, state)
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
        print(f"  Low relevance ({keyword_hits} keyword hits). Skipping LLM analysis.")
        mark_processed(state, url, summary=f"low-relevance: {title}")
        FINDINGS_FILE.unlink(missing_ok=True)
        save_state(STATE_FILE, state)
        print(usage.summary())
        return

    # Step 3: LLM analysis — one call, ask for concrete improvements
    print("  Sending to LLM for analysis...")
    system_prompt = (
        "You are a senior software architect reviewing open-source projects to find "
        "patterns and techniques that could improve an existing codebase. "
        "Be specific and actionable. Only propose improvements that are clearly better "
        "than what exists. Output ONLY a JSON array, no other text."
    )

    user_prompt = f"""\
## Our codebase
{CODEBASE_SUMMARY}

## Repository to analyze
{text[:8000]}

## Task
Analyze this repository and find 1-3 concrete improvements we could apply to our
agent-orchestrator codebase. Only include improvements that:
- Are clearly inspired by a pattern/technique from this repo
- Map to a specific file in our codebase
- Include a code snippet showing the improvement

Respond with a JSON array. Each item must have:
- "component": which part of our codebase (router, agent, skill, provider, graph, etc.)
- "title": short title (max 10 words)
- "description": 2-3 sentences explaining the improvement and how it's inspired by the repo
- "file": path to modify (e.g. "src/agent_orchestrator/core/router.py")
- "code": Python code snippet showing the improvement (function or class to add/modify)
- "benefit": one sentence on the expected benefit

If the repo has nothing useful for our codebase, return an empty array: []
"""

    llm_result = _call_llm(user_prompt, system_prompt)
    if "error" in llm_result:
        print(f"  LLM error: {llm_result['error']}")
        mark_processed(state, url, summary=f"llm-error: {llm_result['error']}")
        FINDINGS_FILE.unlink(missing_ok=True)
        save_state(STATE_FILE, state)
        return

    llm_content = llm_result["content"]
    print(
        f"  LLM response: {len(llm_content)} chars, "
        f"{llm_result['input_tokens']}in/{llm_result['output_tokens']}out tokens"
    )

    # Step 4: Parse improvements
    improvements = _parse_improvements(llm_content)
    print(f"  Parsed {len(improvements)} improvement(s)")

    for imp in improvements:
        print(f"    - [{imp['component']}] {imp['title']}")
        if imp.get("file"):
            print(f"      File: {imp['file']}")

    # Step 5: Write findings (if any)
    if improvements:
        _write_findings(title, url, improvements)
    else:
        print("  No actionable improvements found.")
        FINDINGS_FILE.unlink(missing_ok=True)

    # Mark as processed
    mark_processed(
        state,
        url,
        summary=title,
        improvements=[imp["title"] for imp in improvements],
    )

    # Cleanup old entries
    removed = cleanup_old_entries(state, max_age_days=60)
    if removed:
        print(f"\nCleaned up {removed} old state entries (>60 days)")

    save_state(STATE_FILE, state)

    # Write usage report
    print(usage.summary())
    USAGE_FILE.write_text(json.dumps(usage.to_dict(), indent=2), encoding="utf-8")
    print(f"Done. State saved to {STATE_FILE}")


if __name__ == "__main__":
    main()
