#!/usr/bin/env python3
"""Fetch recently starred GitHub repos and merge them into bookmarks.json.

Uses the GitHub API to fetch starred repos with timestamps.
Stars older than LOOKBACK_DAYS (default 7) are ignored.

Optional: set GITHUB_TOKEN for higher rate limits (60/h without, 5000/h with).
Set GITHUB_USERNAME or it will be auto-detected from the token.
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

BOOKMARKS_FILE = Path(".claude/bookmarks.json")
API_BASE = "https://api.github.com"


def _api_get(url: str, token: str = "") -> dict | list | None:
    """Make a GitHub API GET request."""
    headers = {
        "Accept": "application/vnd.github.v3.star+json",
        "User-Agent": "AgentOrchestrator-ResearchScout/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except URLError as exc:
        print(f"  API error: {exc}", file=sys.stderr)
        return None


def _get_username(token: str) -> str | None:
    """Get authenticated username, or None if no token."""
    if not token:
        return None
    data = _api_get(f"{API_BASE}/user", token)
    if data and "login" in data:
        return data["login"]
    return None


def get_recent_stars(username: str, token: str = "", lookback_days: int = 7) -> list[dict]:
    """Fetch starred repos from the last lookback_days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    bookmarks = []
    page = 1

    while True:
        url = f"{API_BASE}/users/{username}/starred?per_page=100&page={page}&sort=created&direction=desc"
        data = _api_get(url, token)

        if not data:
            break

        if not isinstance(data, list) or len(data) == 0:
            break

        for item in data:
            starred_at_str = item.get("starred_at", "")
            repo = item.get("repo", {})

            if not starred_at_str or not repo:
                continue

            starred_at = datetime.fromisoformat(starred_at_str.replace("Z", "+00:00"))

            # Stop pagination if we've gone past the lookback window
            if starred_at < cutoff:
                return bookmarks

            repo_url = repo.get("html_url", "")
            description = repo.get("description", "") or ""
            topics = repo.get("topics", [])
            language = repo.get("language", "")

            notes_parts = [description[:200]]
            if language:
                notes_parts.append(f"Language: {language}")
            if topics:
                notes_parts.append(f"Topics: {', '.join(topics[:5])}")

            bookmarks.append(
                {
                    "url": repo_url,
                    "added": starred_at_str,
                    "source": "github-star",
                    "notes": " | ".join(notes_parts),
                }
            )

        page += 1
        # Safety limit
        if page > 10:
            break

    return bookmarks


def merge_bookmarks(existing: list[dict], new_bookmarks: list[dict]) -> list[dict]:
    """Merge new bookmarks into existing list, avoiding duplicates."""
    existing_urls = {bm.get("url") for bm in existing}
    added = 0
    for bm in new_bookmarks:
        if bm["url"] not in existing_urls:
            existing.append(bm)
            existing_urls.add(bm["url"])
            added += 1
    return existing


def main():
    token = os.environ.get("GITHUB_TOKEN", "")
    username = os.environ.get("GITHUB_USERNAME", "")
    lookback = int(os.environ.get("LOOKBACK_DAYS", "30"))

    # Auto-detect username from token if not set
    if not username and token:
        username = _get_username(token) or ""

    if not username:
        print(
            "GITHUB_USERNAME not set and could not auto-detect. "
            "Set GITHUB_USERNAME or GITHUB_TOKEN.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Fetching GitHub stars for @{username} (last {lookback} days)...")
    new_bookmarks = get_recent_stars(username, token, lookback)
    print(f"Found {len(new_bookmarks)} recently starred repo(s)")

    for bm in new_bookmarks:
        print(f"  - {bm['url']}")

    # Load existing bookmarks
    existing = []
    if BOOKMARKS_FILE.exists():
        try:
            existing = json.loads(BOOKMARKS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = []

    old_count = len(existing)
    merged = merge_bookmarks(existing, new_bookmarks)
    BOOKMARKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    BOOKMARKS_FILE.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Total bookmarks: {len(merged)} (added {len(merged) - old_count} new)")


if __name__ == "__main__":
    main()
