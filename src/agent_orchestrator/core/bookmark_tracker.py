"""Bookmark tracker — JSON-based tracking of processed URLs with 30-day lookback."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_STATE_FILE = ".claude/research-scout-state.json"
DEFAULT_BOOKMARKS_FILE = ".claude/bookmarks.json"
LOOKBACK_DAYS = 30


def load_state(state_path: str | Path) -> dict:
    """Load the research scout state file."""
    path = Path(state_path)
    if not path.exists():
        return {"processed": {}, "last_run": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read state file %s: %s", path, exc)
        return {"processed": {}, "last_run": None}


def save_state(state_path: str | Path, state: dict) -> None:
    """Save the research scout state file."""
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def load_bookmarks(bookmarks_path: str | Path) -> list[dict]:
    """Load bookmarks from a JSON file.

    Expected format:
    [
        {"url": "https://...", "added": "2026-03-01T12:00:00Z", "source": "manual"},
        ...
    ]
    """
    path = Path(bookmarks_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read bookmarks %s: %s", path, exc)
        return []


def filter_unprocessed(
    bookmarks: list[dict],
    state: dict,
    lookback_days: int = LOOKBACK_DAYS,
) -> list[dict]:
    """Filter bookmarks to only unprocessed ones within the lookback window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    processed = state.get("processed", {})
    result = []

    for bm in bookmarks:
        url = bm.get("url", "")
        if not url:
            continue

        # Skip already processed
        if url in processed:
            continue

        # Check date if available
        added_str = bm.get("added")
        if added_str:
            try:
                added = datetime.fromisoformat(added_str.replace("Z", "+00:00"))
                if added < cutoff:
                    continue
            except (ValueError, TypeError):
                pass  # If date is unparseable, include the bookmark

        result.append(bm)

    return result


def mark_processed(
    state: dict,
    url: str,
    summary: str = "",
    improvements: list[str] | None = None,
) -> None:
    """Mark a URL as processed in the state."""
    if "processed" not in state:
        state["processed"] = {}
    state["processed"][url] = {
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "improvements": improvements or [],
    }


def cleanup_old_entries(state: dict, max_age_days: int = 30) -> int:
    """Remove processed entries older than max_age_days. Returns count removed."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    processed = state.get("processed", {})
    to_remove = []

    for url, info in processed.items():
        proc_at = info.get("processed_at", "")
        try:
            dt = datetime.fromisoformat(proc_at.replace("Z", "+00:00"))
            if dt < cutoff:
                to_remove.append(url)
        except (ValueError, TypeError):
            pass

    for url in to_remove:
        del processed[url]

    return len(to_remove)
